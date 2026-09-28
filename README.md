# Agent Flight Recorder

Agent Flight Recorder records agent execution as deterministic JSONL events. It supports replay against frozen fixtures, state-aware branching, event diffs, and a self-contained timeline viewer.

## Honesty boundary

The bundled sample agent and the three bundled incident runs are scripted fixtures. They make no real LLM calls, use no live services, and do not describe production incidents. The recorder can be integrated with any agent, but this repository's demo uses deterministic fixtures rather than live models.

## Quickstart

Python 3.10 or newer is required. The project uses only the Python standard library at runtime. Pytest is needed only to run the test suite.

```powershell
python -m flight_recorder fixtures
python -m flight_recorder record duplicate-charge
python -m flight_recorder replay RUN_ID
python -m flight_recorder branch RUN_ID --at 5
python -m flight_recorder counterfactual RUN_ID --at 5 --prompt "Review the alternative" --seed 9
python -m flight_recorder diff RUN_ID BRANCH_ID
python -m flight_recorder viewer BRANCH_ID --compare RUN_ID
```

`record` accepts `duplicate-charge`, `blocked-export`, or `stale-update`. Runs are written under `runs/` by default. Pass `--runs-dir PATH` to use another location. A run identifier is derived from its deterministic configuration unless `--run-id` is supplied. Repeating the same configuration creates the same event bytes and identifier.

Run identifiers are immutable. Re-recording an identical configuration verifies and reuses its existing timeline. Attempting to reuse an identifier for different inputs is rejected. Manifests store the latest state snapshot, while branch manifests also store the state at the fork point.

Install the `recorder` console command with `python -m pip install -e .`, or keep using `python -m flight_recorder`. The `fixtures` command lists the three checked-in scripted runs. Add `--install` to copy them into a chosen runs directory.

## Architecture

```text
Agent operation
    |
    v
Instrumentation SDK  ---->  Canonical event encoder
    |                               |
    | state mutation                v
    +-----------------------> append-only events.jsonl
                                    |
                   +----------------+----------------+
                   v                v                v
                 replay           branch            viewer
                   |                |                |
                   +------ frozen fixtures ---------+
```

The recorder assigns sequence numbers and logical timestamps. It never reads wall-clock time for event bytes. Each event payload is serialized as compact, sorted-key UTF-8 JSON, and its SHA-256 hash is stored next to the payload. Branching copies the parent JSONL prefix byte-for-byte, rebuilds state from recorded mutations and prior operation results, then continues the remaining sample-agent operations.

The SDK exposes operation methods and a `capture` decorator. The sample agent uses the decorator for model-shaped calls, retrieval, permission checks, and tool actions. `mutate` and `side_effect` record state changes and external-effect intent. During replay, a configured side-effect handler is suppressed while its fixture result is recorded, so replay cannot repeat the real action. The instrumentation primitives can wrap other agent code; the bundled replay and branch runner is for the sample agent and its frozen fixtures. Integrating another agent's replay requires its own deterministic runner and fixture adapters.

The bundled agent is deliberately simple and fixture-driven. Its seed defaults to `0`; no random number generator is used. The seed is converted to a stable SHA-256 tag to make counterfactual changes visible. Tools and retrieval return fixed scenario data.

## Event schema

Each line in `events.jsonl` is one JSON object with these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `seq` | integer | One-based logical order within this run |
| `ts` | string | Deterministic UTC timestamp derived from `seq` |
| `kind` | string | One of the event kinds below |
| `actor` | string | Instrumented agent component |
| `payload_hash` | string | SHA-256 of canonical JSON for `payload` |
| `payload` | object | Operation inputs, result, or state change |

Event kinds are `model_call`, `tool_action`, `state_mutation`, `retrieval`, `permission_check`, and `side_effect`. JSON object keys are sorted, whitespace is omitted, and each JSONL row ends with one newline. Timestamps start at `2026-01-01T00:00:00.000Z` and advance one millisecond per sequence number. They are logical ordering labels, not observed execution times.

Run metadata is kept in `manifest.json`, separate from the event stream. It includes the runner configuration, event count, and optional parent run and branch sequence. This keeps inherited branch events byte-identical to their parent.

## Commands

- `record SCENARIO`: execute the sample agent and write a new timeline.
- `replay RUN`: re-execute the sample runner against frozen fixtures and report whether every event byte matches.
- `branch RUN --at N`: copy the first N events, fork state, and continue from the next operation.
- `counterfactual RUN --at N`: branch with optional changed `--prompt`, `--seed`, or `--policy` values. Changed inputs affect operations that occur after the branch point.
- `diff LEFT RIGHT`: show event positions whose serialized event objects differ.
- `fixtures`: list bundled scripted incident fixtures, or use `--install` to copy them into `runs/`.
- `viewer RUN --compare OTHER`: write a static HTML timeline with kind, actor, and payload filters. State mutation events show before and after values. With `--compare`, differing sequence positions are highlighted.

Every bundled incident is a fictional demo fixture. The names describe test scenarios only, not real customer data or real production failures.

## Tests

Run the full suite with:

```powershell
python -m pytest
```
