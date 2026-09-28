"""Command line interface for recording, replaying, branching, and viewing runs."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from .core import canonical_json
from .diffing import compare_events
from .sample_agent import DEFAULT_PROMPT, DEFAULT_SEED, SCENARIOS
from .store import (
    install_fixtures,
    load_run,
    record_sample,
    replay_sample,
    branch_sample,
)
from .viewer import render_viewer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recorder",
        description="Deterministic event recording, replay, branching, and comparison.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser("record", help="run and record the scripted sample agent")
    record.add_argument("scenario", choices=sorted(SCENARIOS))
    record.add_argument("--runs-dir", default="runs")
    record.add_argument("--run-id")
    record.add_argument("--prompt", default=DEFAULT_PROMPT)
    record.add_argument("--seed", type=int, default=DEFAULT_SEED)
    record.add_argument("--policy", choices=("fixture", "allow", "deny"), default="fixture")

    replay = commands.add_parser("replay", help="re-execute a run against frozen fixtures")
    replay.add_argument("run")
    replay.add_argument("--runs-dir", default="runs")

    branch = commands.add_parser("branch", help="fork state at a sequence and continue")
    branch.add_argument("run")
    branch.add_argument("--at", type=int, required=True)
    branch.add_argument("--runs-dir", default="runs")
    branch.add_argument("--prompt")
    branch.add_argument("--seed", type=int)
    branch.add_argument("--policy", choices=("fixture", "allow", "deny"))

    counterfactual = commands.add_parser("counterfactual", help="branch with changed inputs")
    counterfactual.add_argument("run")
    counterfactual.add_argument("--at", type=int, required=True)
    counterfactual.add_argument("--runs-dir", default="runs")
    counterfactual.add_argument("--prompt")
    counterfactual.add_argument("--seed", type=int)
    counterfactual.add_argument("--policy", choices=("fixture", "allow", "deny"))

    diff = commands.add_parser("diff", help="compare two timelines event by event")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--runs-dir", default="runs")

    fixtures = commands.add_parser("fixtures", help="list bundled scripted incident fixtures")
    fixtures.add_argument("--install", action="store_true", help="copy fixture runs into runs-dir")
    fixtures.add_argument("--runs-dir", default="runs")

    viewer = commands.add_parser("viewer", help="write a self-contained HTML timeline viewer")
    viewer.add_argument("run")
    viewer.add_argument("--compare")
    viewer.add_argument("--output")
    viewer.add_argument("--runs-dir", default="runs")
    return parser


def _emit_event_diff(result: dict[str, Any], left_id: str, right_id: str) -> None:
    print(f"Compared {left_id} with {right_id}")
    print(f"Identical events: {result['identical_count']}")
    print(f"Divergent positions: {result['divergent_count']}")
    for row in result["rows"]:
        left = row["left"]
        right = row["right"]
        left_text = "missing" if left is None else f"{left['kind']}:{left['actor']} {canonical_json(left['payload'])}"
        right_text = "missing" if right is None else f"{right['kind']}:{right['actor']} {canonical_json(right['payload'])}"
        print(f"seq {row['seq']}: left={left_text} | right={right_text}")


def _fixture_rows() -> list[tuple[str, str, str]]:
    rows = []
    for scenario, details in sorted(SCENARIOS.items()):
        run_id = f"incident-{scenario}"
        rows.append((run_id, scenario, details["title"]))
    return rows


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "record":
            directory, manifest = record_sample(
                args.scenario,
                args.runs_dir,
                args.run_id,
                args.prompt,
                args.seed,
                args.policy,
            )
            digest = hashlib.sha256((directory / "events.jsonl").read_bytes()).hexdigest()
            print(f"RECORDED {manifest['run_id']} events={manifest['event_count']} sha256={digest}")
            return 0

        if args.command == "replay":
            matched, expected, actual, count = replay_sample(args.run, args.runs_dir)
            run_id = load_run(args.run, args.runs_dir)[1]["run_id"]
            digest = hashlib.sha256(actual).hexdigest()
            if matched:
                print(f"REPLAY MATCH {run_id} events={count} sha256={digest}")
                return 0
            print(f"REPLAY MISMATCH {run_id} expected={hashlib.sha256(expected).hexdigest()} actual={digest}")
            return 1

        if args.command in {"branch", "counterfactual"}:
            directory, manifest = branch_sample(
                args.run,
                args.at,
                args.runs_dir,
                args.prompt,
                args.seed,
                args.policy,
            )
            digest = hashlib.sha256((directory / "events.jsonl").read_bytes()).hexdigest()
            print(
                f"BRANCHED {manifest['run_id']} parent={manifest['parent_run']} "
                f"at={manifest['branch_at']} events={manifest['event_count']} sha256={digest}"
            )
            return 0

        if args.command == "diff":
            _, left_manifest, _, left_events = load_run(args.left, args.runs_dir)
            _, right_manifest, _, right_events = load_run(args.right, args.runs_dir)
            _emit_event_diff(
                compare_events(left_events, right_events),
                left_manifest["run_id"],
                right_manifest["run_id"],
            )
            return 0

        if args.command == "fixtures":
            if args.install:
                installed = install_fixtures(args.runs_dir)
                print("INSTALLED " + ", ".join(installed))
            else:
                for run_id, scenario, title in _fixture_rows():
                    print(f"{run_id}\t{scenario}\t{title}\tscripted fixture, no real LLM calls")
            return 0

        if args.command == "viewer":
            _, manifest, _, events = load_run(args.run, args.runs_dir)
            compare_id = None
            compare_events_data = None
            if args.compare:
                _, compare_manifest, _, compare_events_data = load_run(args.compare, args.runs_dir)
                compare_id = compare_manifest["run_id"]
            output = Path(args.output) if args.output else Path(args.runs_dir) / f"viewer-{manifest['run_id']}.html"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                render_viewer(manifest["run_id"], events, compare_id, compare_events_data),
                encoding="utf-8",
                newline="\n",
            )
            print(f"VIEWER {output} events={len(events)}")
            return 0

    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
