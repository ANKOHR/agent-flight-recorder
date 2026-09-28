"""Append-only deterministic event recording and instrumentation."""

from __future__ import annotations

import hashlib
import inspect
import json
from functools import wraps
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

EVENT_KINDS = frozenset(
    {
        "model_call",
        "tool_action",
        "state_mutation",
        "retrieval",
        "permission_check",
        "side_effect",
    }
)
_LOGICAL_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def canonical_json(value: Any) -> str:
    """Return stable compact JSON suitable for hashes and event lines."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def event_bytes(event: dict[str, Any]) -> bytes:
    return (canonical_json(event) + "\n").encode("utf-8")


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def logical_timestamp(seq: int) -> str:
    """Map sequence numbers to deterministic timestamps without wall-clock reads."""
    value = _LOGICAL_EPOCH + timedelta(milliseconds=seq - 1)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_event(event: dict[str, Any], expected_seq: int) -> None:
    """Validate the fixed schema and deterministic fields for one event."""
    required = {"seq", "ts", "kind", "actor", "payload_hash", "payload"}
    if set(event) != required:
        raise ValueError(f"event {expected_seq} has an invalid schema")
    if type(event["seq"]) is not int or event["seq"] != expected_seq:
        raise ValueError(f"event sequence mismatch at {expected_seq}")
    if not isinstance(event["ts"], str) or event["ts"] != logical_timestamp(expected_seq):
        raise ValueError(f"event timestamp mismatch at {expected_seq}")
    if not isinstance(event["kind"], str) or event["kind"] not in EVENT_KINDS:
        raise ValueError(f"unsupported event kind at {expected_seq}")
    if not isinstance(event["actor"], str) or not event["actor"]:
        raise ValueError(f"invalid actor at {expected_seq}")
    if not isinstance(event["payload"], dict):
        raise ValueError(f"event payload must be an object at {expected_seq}")
    if event["payload_hash"] != payload_hash(event["payload"]):
        raise ValueError(f"payload hash mismatch at {expected_seq}")


class Recorder:
    """Write validated events to an append-only JSONL timeline."""

    def __init__(
        self,
        directory: str | Path,
        run_id: str,
        *,
        prefix: bytes = b"",
        replaying: bool = False,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.events_path = self.directory / "events.jsonl"
        self.replaying = replaying
        self.events: list[dict[str, Any]] = []
        if self.events_path.exists():
            raise FileExistsError(f"event log already exists and cannot be replaced: {self.events_path}")
        if prefix:
            if not prefix.endswith(b"\n"):
                raise ValueError("event prefix must end at a complete JSONL row")
            self.events = [json.loads(line) for line in prefix.splitlines()]
            for expected_seq, event in enumerate(self.events, start=1):
                validate_event(event, expected_seq)
                if event_bytes(event) != prefix.splitlines(keepends=True)[expected_seq - 1]:
                    raise ValueError(f"event prefix is not canonical JSON at {expected_seq}")
            self.events_path.write_bytes(prefix)
        else:
            self.events_path.write_bytes(b"")

    @property
    def next_seq(self) -> int:
        return len(self.events) + 1

    def record(self, kind: str, actor: str, payload: Any) -> dict[str, Any]:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unsupported event kind: {kind}")
        if not isinstance(actor, str) or not actor:
            raise ValueError("actor must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("event payload must be an object")
        seq = self.next_seq
        event = {
            "seq": seq,
            "ts": logical_timestamp(seq),
            "kind": kind,
            "actor": actor,
            "payload_hash": payload_hash(payload),
            "payload": payload,
        }
        with self.events_path.open("ab") as stream:
            stream.write(event_bytes(event))
        self.events.append(event)
        return event

    def bytes(self) -> bytes:
        return self.events_path.read_bytes()


class Instrumentor:
    """Wrap agent operations and record their inputs and fixture outputs."""

    def __init__(self, recorder: Recorder, state: dict[str, Any] | None = None) -> None:
        self.recorder = recorder
        self.state = state if state is not None else {}

    def capture(self, kind: str, actor: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorate an operation so its inputs and output are recorded automatically.

        Supported function argument names are prompt/seed for model calls,
        name/arguments for tools, query for retrieval, and subject/action for
        permission checks.
        """
        if kind not in {"model_call", "tool_action", "retrieval", "permission_check"}:
            raise ValueError("capture supports model_call, tool_action, retrieval, and permission_check")

        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            signature = inspect.signature(function)

            @wraps(function)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                inputs = signature.bind(*args, **kwargs).arguments
                output = function(*args, **kwargs)
                if kind == "model_call":
                    payload = {"prompt": inputs["prompt"], "seed": inputs["seed"], "output": output}
                elif kind == "tool_action":
                    payload = {"name": inputs["name"], "arguments": inputs["arguments"], "output": output}
                elif kind == "retrieval":
                    payload = {"query": inputs["query"], "documents": output}
                else:
                    payload = {
                        "subject": inputs["subject"],
                        "action": inputs["action"],
                        "allowed": bool(output),
                    }
                    output = bool(output)
                self.recorder.record(kind, actor, payload)
                return output

            return wrapped

        return decorate

    def model_call(
        self, actor: str, prompt: str, seed: int, model_fixture: Callable[[], Any]
    ) -> Any:
        output = model_fixture()
        self.recorder.record(
            "model_call", actor, {"prompt": prompt, "seed": seed, "output": output}
        )
        return output

    def tool_action(
        self, actor: str, name: str, arguments: dict[str, Any], tool_fixture: Callable[[], Any]
    ) -> Any:
        output = tool_fixture()
        self.recorder.record(
            "tool_action", actor, {"name": name, "arguments": arguments, "output": output}
        )
        return output

    def mutate(self, actor: str, path: str, value: Any) -> Any:
        keys = path.split(".")
        target = self.state
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        before = target.get(keys[-1])
        target[keys[-1]] = value
        self.recorder.record(
            "state_mutation",
            actor,
            {"path": path, "before": before, "after": value},
        )
        return value

    def retrieval(self, actor: str, query: str, provider_fixture: Callable[[], Any]) -> Any:
        documents = provider_fixture()
        self.recorder.record(
            "retrieval", actor, {"query": query, "documents": documents}
        )
        return documents

    def permission_check(
        self, actor: str, subject: str, action: str, policy_fixture: Callable[[], bool]
    ) -> bool:
        allowed = bool(policy_fixture())
        self.recorder.record(
            "permission_check",
            actor,
            {"subject": subject, "action": action, "allowed": allowed},
        )
        return allowed

    def side_effect(
        self,
        actor: str,
        name: str,
        inputs: Any,
        fixture_result: Any,
        handler: Callable[[], Any] | None = None,
    ) -> Any:
        """Record an effect. Replay mode absorbs it and never calls the handler."""
        if self.recorder.replaying:
            result = fixture_result
            status = "executed" if handler is not None else "fixture_absorbed"
        elif handler is not None:
            result = handler()
            status = "executed"
        else:
            result = fixture_result
            status = "fixture_absorbed"
        self.recorder.record(
            "side_effect",
            actor,
            {"name": name, "inputs": inputs, "status": status, "result": result},
        )
        return result


def apply_mutations(events: Iterable[dict[str, Any]], state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild the latest state represented by state_mutation events."""
    result = state if state is not None else {}
    for event in events:
        if event["kind"] != "state_mutation":
            continue
        mutation = event["payload"]
        keys = mutation["path"].split(".")
        target = result
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        target[keys[-1]] = mutation["after"]
    return result
