from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from flight_recorder.core import EVENT_KINDS, Instrumentor, Recorder, apply_mutations, canonical_json
from flight_recorder.sample_agent import DEFAULT_PROMPT, SCENARIOS
from flight_recorder.store import (
    FIXTURE_ROOT,
    branch_sample,
    load_run,
    record_sample,
    replay_sample,
)


def test_event_schema_order_and_payload_hash(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "one", "schema-run")
    recorder.record("model_call", "planner", {"z": 2, "a": 1})
    rows = (tmp_path / "one" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    event = json.loads(rows[0])
    assert list(event) == ["actor", "kind", "payload", "payload_hash", "seq", "ts"]
    assert event["seq"] == 1
    assert event["kind"] in EVENT_KINDS
    assert event["payload_hash"] == hashlib.sha256(canonical_json(event["payload"]).encode()).hexdigest()
    assert event["ts"] == "2026-01-01T00:00:00.000Z"


def test_serialization_is_byte_deterministic_across_runs(tmp_path: Path) -> None:
    first, manifest_one = record_sample("duplicate-charge", tmp_path / "first")
    second, manifest_two = record_sample("duplicate-charge", tmp_path / "second")
    assert (first / "events.jsonl").read_bytes() == (second / "events.jsonl").read_bytes()
    assert manifest_one == manifest_two
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()


def test_run_id_is_immutable_and_identical_recording_is_reused(tmp_path: Path) -> None:
    first, first_manifest = record_sample("stale-update", tmp_path)
    original = (first / "events.jsonl").read_bytes()
    second, second_manifest = record_sample("stale-update", tmp_path)
    assert first == second
    assert first_manifest == second_manifest
    assert (second / "events.jsonl").read_bytes() == original
    with pytest.raises(FileExistsError):
        record_sample("blocked-export", tmp_path, run_id=first_manifest["run_id"])


def test_recorder_refuses_to_replace_an_existing_event_log(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "immutable")
    recorder.record("model_call", "planner", {"prompt": "one"})
    with pytest.raises(FileExistsError):
        Recorder(tmp_path, "immutable")


def test_non_json_numeric_payload_is_rejected(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "strict-json")
    with pytest.raises(ValueError):
        recorder.record("tool_action", "tools", {"value": float("nan")})


def test_instrumentation_records_every_sample_operation(tmp_path: Path) -> None:
    directory, manifest = record_sample("stale-update", tmp_path)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = [event["kind"] for event in events]
    assert kinds == [
        "model_call",
        "retrieval",
        "permission_check",
        "tool_action",
        "state_mutation",
        "model_call",
        "side_effect",
        "state_mutation",
    ]
    assert manifest["event_count"] == 8
    assert manifest["state_snapshot"]["status"] == "simulated"
    assert events[0]["payload"]["prompt"] == DEFAULT_PROMPT
    assert events[3]["payload"]["output"]["fixture_only"] is True
    assert events[6]["payload"]["status"] == "fixture_absorbed"


def test_capture_decorator_records_without_agent_record_calls(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "decorator")
    sdk = Instrumentor(recorder)

    @sdk.capture("tool_action", "tools")
    def compute(name: str, arguments: dict[str, int]) -> dict[str, int]:
        return {"total": arguments["left"] + arguments["right"]}

    assert compute("sum", {"left": 3, "right": 4}) == {"total": 7}
    assert recorder.events[0]["payload"] == {
        "name": "sum",
        "arguments": {"left": 3, "right": 4},
        "output": {"total": 7},
    }


def test_all_bundled_incidents_are_scripted_and_replayable() -> None:
    fixture_dirs = sorted(path for path in FIXTURE_ROOT.iterdir() if path.is_dir())
    assert len(fixture_dirs) == 3
    for directory in fixture_dirs:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["config"]["scenario"] in SCENARIOS
        assert manifest["state_snapshot"]["decision"] == SCENARIOS[manifest["config"]["scenario"]]["decision"]
        assert len(manifest["state_snapshot"]["documents"]) == 2
        matched, expected, actual, count = replay_sample(directory)
        assert matched
        assert expected == actual
        assert count == 8


def test_bundled_fixture_can_be_branched(tmp_path: Path) -> None:
    branch_dir, branch_manifest = branch_sample("incident-blocked-export", 3, tmp_path)
    source = FIXTURE_ROOT / "incident-blocked-export"
    parent_lines = (source / "events.jsonl").read_bytes().splitlines(keepends=True)
    branch_prefix = (branch_dir / "events.jsonl").read_bytes().splitlines(keepends=True)[:3]
    assert b"".join(parent_lines[:3]) == b"".join(branch_prefix)
    assert branch_manifest["state_snapshot"]["permission"] is False
    assert replay_sample(branch_manifest["run_id"], tmp_path)[0] is True


def test_load_run_detects_payload_corruption(tmp_path: Path) -> None:
    directory, manifest = record_sample("stale-update", tmp_path)
    path = directory / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["payload"]["prompt"] = "changed without updating its hash"
    lines[0] = canonical_json(event)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="payload hash mismatch"):
        load_run(manifest["run_id"], tmp_path)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_replay_reexecutes_to_identical_timeline(tmp_path: Path, scenario: str) -> None:
    _, manifest = record_sample(scenario, tmp_path)
    matched, expected, actual, count = replay_sample(manifest["run_id"], tmp_path)
    assert matched is True
    assert count == 8
    assert actual == expected


def test_branch_copies_exact_prefix_and_forks_state(tmp_path: Path) -> None:
    parent_dir, parent_manifest = record_sample("duplicate-charge", tmp_path)
    branch_dir, branch_manifest = branch_sample(parent_manifest["run_id"], 5, tmp_path)
    parent_bytes = (parent_dir / "events.jsonl").read_bytes()
    branch_bytes = (branch_dir / "events.jsonl").read_bytes()
    parent_prefix = b"".join(parent_bytes.splitlines(keepends=True)[:5])
    branch_prefix = b"".join(branch_bytes.splitlines(keepends=True)[:5])
    assert branch_prefix == parent_prefix
    assert branch_manifest["parent_run"] == parent_manifest["run_id"]
    assert branch_manifest["state_snapshot"]["status"] == "blocked"
    _, _, _, branch_events = load_run(branch_manifest["run_id"], tmp_path)
    forked_state = apply_mutations(branch_events[:5], {"status": "started"})
    assert forked_state["status"] == "blocked"


def test_counterfactual_prompt_and_seed_diverge_after_branch(tmp_path: Path) -> None:
    _, parent_manifest = record_sample("duplicate-charge", tmp_path)
    _, branch_manifest = branch_sample(
        parent_manifest["run_id"],
        5,
        tmp_path,
        prompt="Review an alternate fixture outcome.",
        seed=17,
    )
    _, _, _, parent_events = load_run(parent_manifest["run_id"], tmp_path)
    _, _, _, branch_events = load_run(branch_manifest["run_id"], tmp_path)
    assert parent_events[:5] == branch_events[:5]
    assert parent_events[5] != branch_events[5]
    assert branch_events[5]["payload"]["seed"] == 17
    assert "alternate fixture" in branch_events[5]["payload"]["prompt"].lower()
    assert replay_sample(branch_manifest["run_id"], tmp_path)[0] is True


def test_policy_counterfactual_changes_future_permission_check(tmp_path: Path) -> None:
    _, manifest = record_sample("duplicate-charge", tmp_path)
    _, branch_manifest = branch_sample(manifest["run_id"], 2, tmp_path, policy="allow")
    _, _, _, parent_events = load_run(manifest["run_id"], tmp_path)
    _, _, _, branch_events = load_run(branch_manifest["run_id"], tmp_path)
    assert parent_events[:2] == branch_events[:2]
    assert parent_events[2]["payload"]["allowed"] is False
    assert branch_events[2]["payload"]["allowed"] is True


def test_replay_absorbs_side_effect_handler(tmp_path: Path) -> None:
    calls: list[str] = []
    normal = Recorder(tmp_path / "normal", "effect")
    Instrumentor(normal).side_effect(
        "notifier", "send", {"message": "fixture"}, {"id": "fixture-1"},
        handler=lambda: calls.append("called") or {"id": "fixture-1"},
    )
    recorded_event = normal.events[0]
    assert calls == ["called"]

    replay = Recorder(tmp_path / "replay", "effect", replaying=True)
    Instrumentor(replay).side_effect(
        "notifier", "send", {"message": "fixture"}, {"id": "fixture-1"},
        handler=lambda: calls.append("called-again") or {"id": "wrong"},
    )
    assert calls == ["called"]
    assert replay.events[0] == recorded_event


def test_nested_state_mutations_rebuild_in_order(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "state")
    sdk = Instrumentor(recorder, {"account": {"status": "new"}})
    sdk.mutate("state", "account.status", "review")
    sdk.mutate("state", "account.status", "closed")
    rebuilt = apply_mutations(recorder.events, {"account": {"status": "new"}})
    assert rebuilt == {"account": {"status": "closed"}}


def test_source_and_bundled_artifacts_contain_no_em_dash() -> None:
    root = Path(__file__).resolve().parents[1]
    checked = [root / "README.md", root / "pyproject.toml", root / ".gitignore"]
    checked.extend((root / "flight_recorder").glob("*.py"))
    checked.extend((root / "tests").glob("*.py"))
    checked.extend((root / "fixtures").rglob("*.json"))
    checked.extend((root / "fixtures").rglob("*.jsonl"))
    em_dash = chr(0x2014)
    for path in checked:
        assert em_dash not in path.read_text(encoding="utf-8"), str(path)
