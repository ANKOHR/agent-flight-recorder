"""Run manifests, deterministic run identifiers, replay, and branching."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .core import Recorder, canonical_json, validate_event
from .sample_agent import DEFAULT_PROMPT, DEFAULT_SEED, SCENARIOS, run_sample, state_at

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = ROOT / "fixtures" / "incidents"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def config_for(
    scenario: str,
    prompt: str = DEFAULT_PROMPT,
    seed: int = DEFAULT_SEED,
    policy: str = "fixture",
) -> dict[str, Any]:
    return {"runner": "sample", "scenario": scenario, "prompt": prompt, "seed": seed, "policy": policy}


def deterministic_id(config: dict[str, Any], prefix: str = "run") -> str:
    digest = hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{config['scenario']}-{digest}"


def run_path(run_id: str, runs_dir: str | Path = "runs") -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id) or run_id in {".", ".."}:
        raise ValueError("run ID may contain only letters, numbers, dot, underscore, and hyphen")
    return Path(runs_dir) / run_id


def resolve_run(identifier: str | Path, runs_dir: str | Path = "runs") -> Path:
    candidate = Path(identifier)
    if candidate.is_dir() and (candidate / "manifest.json").is_file():
        return candidate
    candidate = run_path(str(identifier), runs_dir)
    if (candidate / "manifest.json").is_file():
        return candidate
    bundled = FIXTURE_ROOT / str(identifier)
    if (bundled / "manifest.json").is_file():
        return bundled
    raise FileNotFoundError(f"run not found: {identifier}")


def load_run(identifier: str | Path, runs_dir: str | Path = "runs") -> tuple[Path, dict[str, Any], bytes, list[dict[str, Any]]]:
    directory = resolve_run(identifier, runs_dir)
    manifest = read_json(directory / "manifest.json")
    raw = (directory / "events.jsonl").read_bytes()
    events = [json.loads(line) for line in raw.splitlines()]
    if len(events) != manifest["event_count"]:
        raise ValueError(f"event count does not match manifest for {manifest['run_id']}")
    for expected_seq, event in enumerate(events, start=1):
        validate_event(event, expected_seq)
    if b"".join(canonical_json(event).encode("utf-8") + b"\n" for event in events) != raw:
        raise ValueError(f"event log is not canonical JSONL for {manifest['run_id']}")
    return directory, manifest, raw, events


def record_sample(
    scenario: str,
    runs_dir: str | Path = "runs",
    run_id: str | None = None,
    prompt: str = DEFAULT_PROMPT,
    seed: int = DEFAULT_SEED,
    policy: str = "fixture",
) -> tuple[Path, dict[str, Any]]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    config = config_for(scenario, prompt, seed, policy)
    final_id = run_id or deterministic_id(config)
    directory = run_path(final_id, runs_dir)
    existing_manifest = directory / "manifest.json"
    if (directory / "events.jsonl").exists():
        if not existing_manifest.is_file():
            raise FileExistsError(f"run ID is immutable and already has an event log: {final_id}")
        manifest = read_json(existing_manifest)
        if manifest.get("run_id") != final_id or manifest.get("config") != config:
            raise FileExistsError(f"run ID is immutable and already belongs to another run: {final_id}")
        matched, _, _, _ = replay_sample(final_id, runs_dir)
        if not matched:
            raise ValueError(f"existing run does not replay byte-identically: {final_id}")
        return directory, manifest
    recorder = Recorder(directory, final_id)
    final_state = run_sample(recorder, scenario, prompt=prompt, seed=seed, policy=policy)
    manifest = {
        "format_version": 1,
        "run_id": final_id,
        "runner": "sample",
        "config": config,
        "parent_run": None,
        "branch_at": None,
        "event_count": len(recorder.events),
        "state_snapshot": final_state,
    }
    write_json(directory / "manifest.json", manifest)
    return directory, manifest


def replay_sample(
    identifier: str | Path, runs_dir: str | Path = "runs"
) -> tuple[bool, bytes, bytes, int]:
    _, manifest, expected, events = load_run(identifier, runs_dir)
    config = manifest["config"]
    branch_at = manifest.get("branch_at")
    if branch_at is None:
        prefix = b""
        start_seq = 0
    else:
        prefix = b"".join(expected.splitlines(keepends=True)[:branch_at])
        start_seq = branch_at
    with tempfile.TemporaryDirectory(prefix="flight-recorder-replay-") as temp:
        recorder = Recorder(temp, manifest["run_id"], prefix=prefix, replaying=True)
        run_sample(
            recorder,
            config["scenario"],
            prompt=config["prompt"],
            seed=config["seed"],
            policy=config["policy"],
            start_seq=start_seq,
        )
        actual = recorder.bytes()
    return actual == expected, expected, actual, len(events)


def branch_sample(
    identifier: str | Path,
    at: int,
    runs_dir: str | Path = "runs",
    prompt: str | None = None,
    seed: int | None = None,
    policy: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    _, parent, raw, events = load_run(identifier, runs_dir)
    if at < 0 or at > len(events):
        raise ValueError(f"branch point must be between 0 and {len(events)}")
    config = dict(parent["config"])
    if prompt is not None:
        config["prompt"] = prompt
    if seed is not None:
        config["seed"] = seed
    if policy is not None:
        config["policy"] = policy
    identity = {"scenario": config["scenario"], "parent": parent["run_id"], "at": at, "config": config}
    branch_id = deterministic_id(identity, prefix="branch")
    directory = run_path(branch_id, runs_dir)
    fork_state = state_at(config["scenario"], events[:at])
    manifest = {
        "format_version": 1,
        "run_id": branch_id,
        "runner": "sample",
        "config": config,
        "parent_run": parent["run_id"],
        "branch_at": at,
        "event_count": len(events),
        "state_snapshot": fork_state,
    }
    existing_manifest = directory / "manifest.json"
    if (directory / "events.jsonl").exists():
        if not existing_manifest.is_file() or read_json(existing_manifest) != manifest:
            raise FileExistsError(f"branch run ID is immutable and already belongs to another run: {branch_id}")
        matched, _, _, _ = replay_sample(branch_id, runs_dir)
        if not matched:
            raise ValueError(f"existing branch does not replay byte-identically: {branch_id}")
        return directory, manifest
    prefix = b"".join(raw.splitlines(keepends=True)[:at])
    recorder = Recorder(directory, branch_id, prefix=prefix)
    run_sample(
        recorder,
        config["scenario"],
        prompt=config["prompt"],
        seed=config["seed"],
        policy=config["policy"],
        start_seq=at,
    )
    manifest["event_count"] = len(recorder.events)
    write_json(directory / "manifest.json", manifest)
    return directory, manifest


def install_fixtures(runs_dir: str | Path = "runs") -> list[str]:
    target_root = Path(runs_dir)
    target_root.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for source in sorted(FIXTURE_ROOT.iterdir()):
        if source.is_dir() and (source / "manifest.json").is_file():
            target = target_root / source.name
            if target.exists():
                for filename in ("events.jsonl", "manifest.json"):
                    source_file = source / filename
                    target_file = target / filename
                    if not target_file.is_file() or target_file.read_bytes() != source_file.read_bytes():
                        raise FileExistsError(f"fixture install will not replace existing run: {target}")
            else:
                shutil.copytree(source, target)
            installed.append(source.name)
    return installed
