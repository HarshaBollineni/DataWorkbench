"""Plan 3 / D5.4 — Criticality agent (call-name 'Pascal').

Produces ONLY a contextual Importance *ranking* of the in-scope tests (most ->
least important). The deterministic floor-split arithmetic lives in
scoring/criticality.py — never here.
"""
from __future__ import annotations

import json

from scoring import criticality

from ..effort import EffortPolicy


def rank_and_assign(tests: list[dict], context: str = "",
                    effort: EffortPolicy | None = None, on_event=None) -> dict:
    """Rank executable instances; deterministic code assigns criticality."""
    ids = [t["test_instance_id"] for t in tests]
    if len(ids) <= 1:
        ranking = [{"test_instance_id": i,
                    "reason": "Only executable test instance in scope."} for i in ids]
    else:
        policy = effort or EffortPolicy(agent_key="pascal")
        sys = (
            "You are Pascal. Rank every executable test instance exactly once by "
            "regulatory/materiality relevance, target/outcome impact, breadth of "
            "affected records/fields, downstream model/business impact, and "
            "detectability/redundancy. Do not assign High/Medium/Low or calculate "
            "health/splits. Return STRICT JSON: "
            "{\"ranking\":[{\"test_instance_id\":\"str\",\"reason\":\"str\"}]}."
        )
        user = f"CONTEXT: {context}\nTESTS:\n{json.dumps(tests)[:2500]}"
        from ..skills import get_system_prompt
        raw = policy.run([{"role": "system", "content": get_system_prompt("pascal", sys)},
                          {"role": "user", "content": user}], json_mode=True, on_event=on_event)
        try:
            ranking = json.loads(raw).get("ranking", [])
        except (ValueError, TypeError):
            ranking = []
        # Repair to a complete, duplicate-free permutation with mandatory reasons.
        repaired = []
        seen = set()
        for item in ranking:
            if isinstance(item, str):
                item = {"test_instance_id": item, "reason": ""}
            instance_id = item.get("test_instance_id") if isinstance(item, dict) else None
            if instance_id in ids and instance_id not in seen:
                repaired.append({
                    "test_instance_id": instance_id,
                    "reason": str(item.get("reason") or "Ranked from supplied context."),
                })
                seen.add(instance_id)
        repaired.extend({
            "test_instance_id": instance_id,
            "reason": "Appended by deterministic completeness repair.",
        } for instance_id in ids if instance_id not in seen)
        ranking = repaired

    ordered_ids = [r["test_instance_id"] for r in ranking]
    assignment = criticality.assign(ordered_ids)
    return {"ranking": ranking, "assignment": assignment,
            "framework": criticality.framework(len(ordered_ids)),
            "validate": criticality.validate(assignment)}
