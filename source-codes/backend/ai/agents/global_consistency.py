"""Plan 3 / D3.4 — Global Consistency agent (call-name 'Euler'). LLM, high.

Effective challenge BEYOND the table/db: big-picture, conceptual coherence
across the wider data landscape. Same verdict shape as Relationship Validation.
"""
from __future__ import annotations

import json

from ..effort import EffortPolicy

_SYSTEM = (
    "You are Euler, a chief data architect. Challenge each candidate from a "
    "BIG-PICTURE, cross-table / cross-database, conceptual standpoint: does it "
    "cohere with the broader data landscape, regulatory framing and portfolio "
    "logic? Respond as STRICT JSON: {\"verdicts\": [{\"candidate_id\": int, "
    "\"verdict\": \"accept|reject|revise\", \"challenge\": str}]}. "
    "candidate_id is the 0-based index in the input list."
)


def review(candidates: list[dict], global_metadata: dict,
           effort: EffortPolicy | None = None, on_event=None) -> list[dict]:
    if not candidates:
        return []
    policy = effort or EffortPolicy(agent_key="euler")
    user = (f"GLOBAL (cross-table/db) METADATA:\n{json.dumps(global_metadata)[:2500]}\n\n"
            f"CANDIDATES (index = candidate_id):\n{json.dumps(candidates)[:3000]}")
    from ..skills import get_system_prompt
    raw = policy.run([{"role": "system", "content": get_system_prompt("euler", _SYSTEM)},
                      {"role": "user", "content": user}], json_mode=True, on_event=on_event)
    try:
        return json.loads(raw).get("verdicts", [])
    except (ValueError, TypeError):
        return []
