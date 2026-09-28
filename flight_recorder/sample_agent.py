"""Deterministic fixture-driven sample agent. It makes no model calls."""

from __future__ import annotations

import hashlib
from typing import Any

from .core import Instrumentor, Recorder, apply_mutations

DEFAULT_PROMPT = "Review the scripted fixture and follow its recorded policy."
DEFAULT_SEED = 0

SCENARIOS: dict[str, dict[str, Any]] = {
    "duplicate-charge": {
        "title": "Duplicate payment review",
        "decision": "refund_duplicate_charge",
        "allowed": False,
        "amount_minor": 2400,
        "query": "payment ledger entry demo-payment-104",
        "documents": [
            {"id": "fixture-ledger-1", "text": "Two matching entries are present in the scripted ledger."},
            {"id": "fixture-policy-1", "text": "Refunds require a human review in this fixture."},
        ],
    },
    "blocked-export": {
        "title": "Customer data export review",
        "decision": "export_customer_data",
        "allowed": False,
        "amount_minor": 0,
        "query": "customer export permissions demo-account-7",
        "documents": [
            {"id": "fixture-data-1", "text": "The scripted request includes restricted customer fields."},
            {"id": "fixture-policy-2", "text": "This fixture denies bulk data export."},
        ],
    },
    "stale-update": {
        "title": "Stale record refresh review",
        "decision": "refresh_stale_record",
        "allowed": True,
        "amount_minor": 0,
        "query": "record freshness demo-record-22",
        "documents": [
            {"id": "fixture-record-1", "text": "The scripted record is older than the fixture threshold."},
            {"id": "fixture-policy-3", "text": "A refresh is allowed in this fixture."},
        ],
    },
}


def initial_state(scenario: str) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "status": "started",
        "decision": None,
        "documents": [],
        "permission": None,
        "action_result": None,
        "summary": None,
    }


def _seed_tag(seed: int) -> str:
    return hashlib.sha256(str(seed).encode("ascii")).hexdigest()[:8]


def context_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for event in events:
        payload = event["payload"]
        if event["kind"] == "model_call" and event["actor"] == "planner":
            context["decision"] = payload["output"]["decision"]
        elif event["kind"] == "retrieval":
            context["documents"] = payload["documents"]
        elif event["kind"] == "permission_check":
            context["allowed"] = payload["allowed"]
        elif event["kind"] == "tool_action":
            context["action_result"] = payload["output"]
        elif event["kind"] == "model_call" and event["actor"] == "reviewer":
            context["summary"] = payload["output"]["text"]
    return context


def state_at(scenario: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild the complete sample-agent snapshot from mutations and prior outputs."""
    state = apply_mutations(events, initial_state(scenario))
    context = context_from_events(events)
    state["decision"] = context.get("decision")
    state["documents"] = context.get("documents", [])
    state["permission"] = context.get("allowed")
    state["action_result"] = context.get("action_result")
    state["summary"] = context.get("summary", state.get("summary"))
    return state


def run_sample(
    recorder: Recorder,
    scenario: str,
    *,
    prompt: str = DEFAULT_PROMPT,
    seed: int = DEFAULT_SEED,
    policy: str = "fixture",
    start_seq: int = 0,
) -> dict[str, Any]:
    """Execute remaining fixture operations from the recorder's current prefix."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    if policy not in {"fixture", "allow", "deny"}:
        raise ValueError(f"unknown policy: {policy}")
    if start_seq != len(recorder.events):
        raise ValueError("start sequence must match the existing event prefix")

    fixture = SCENARIOS[scenario]
    state = state_at(scenario, recorder.events)
    sdk = Instrumentor(recorder, state)
    context = context_from_events(recorder.events)

    if start_seq < 1:
        @sdk.capture("model_call", "planner")
        def plan(prompt: str, seed: int) -> dict[str, Any]:
            return {"decision": fixture["decision"], "seed_tag": _seed_tag(seed)}

        decision = plan(prompt, seed)
        context["decision"] = decision["decision"]

    if start_seq < 2:
        @sdk.capture("retrieval", "retriever")
        def retrieve(query: str) -> list[dict[str, str]]:
            return fixture["documents"]

        documents = retrieve(fixture["query"])
        context["documents"] = documents

    if start_seq < 3:
        @sdk.capture("permission_check", "policy")
        def check_permission(subject: str, action: str) -> bool:
            return policy == "allow" or (policy == "fixture" and fixture["allowed"])

        allowed = check_permission("demo-agent", context["decision"])
        context["allowed"] = allowed

    if start_seq < 4:
        @sdk.capture("tool_action", "tools")
        def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "simulated" if context["allowed"] else "blocked",
                "amount_minor": fixture["amount_minor"],
                "fixture_only": True,
            }

        action_result = run_tool(
            context["decision"],
            {"amount_minor": fixture["amount_minor"], "allowed": context["allowed"]},
        )
        context["action_result"] = action_result

    if start_seq < 5:
        sdk.mutate("state", "status", context["action_result"]["status"])

    if start_seq < 6:
        @sdk.capture("model_call", "reviewer")
        def review(prompt: str, seed: int) -> dict[str, Any]:
            return {
                "text": (
                    f"{fixture['title']}: {context['action_result']['status']} under "
                    f"the scripted fixture policy."
                ),
                "seed_tag": _seed_tag(seed),
                "document_count": len(context["documents"]),
            }

        summary = review(
            f"{prompt} Decision: {context['decision']}. Result: {context['action_result']['status']}.",
            seed,
        )
        context["summary"] = summary["text"]

    if start_seq < 7:
        sdk.side_effect(
            "notifier",
            "notify_operator",
            {"message": context["summary"]},
            {"delivery": "fixture_absorbed"},
        )

    if start_seq < 8:
        sdk.mutate("state", "summary", context["summary"])

    return state_at(scenario, recorder.events)
