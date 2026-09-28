"""Event-by-event run comparisons."""

from __future__ import annotations

from typing import Any

from .core import canonical_json


def compare_events(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    identical = 0
    for index in range(max(len(left), len(right))):
        first = left[index] if index < len(left) else None
        second = right[index] if index < len(right) else None
        same = first is not None and second is not None and canonical_json(first) == canonical_json(second)
        if same:
            identical += 1
        else:
            rows.append({"seq": index + 1, "left": first, "right": second})
    return {
        "left_count": len(left),
        "right_count": len(right),
        "identical_count": identical,
        "divergent_count": len(rows),
        "rows": rows,
    }
