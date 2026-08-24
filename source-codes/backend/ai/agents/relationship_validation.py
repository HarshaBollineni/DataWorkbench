"""Plan 3 / D3.3 — Relationship Validation agent (call-name 'Fermat'). LLM, high.

Effective challenge WITHIN the table: for each candidate, issue a verdict
(accept/reject/revise) + a challenge note. Single-responsibility.
"""
from __future__ import annotations

import json

from ..effort import EffortPolicy

_SYSTEM = (
    "You are Fermat, a rigorous model-validation challenger. For EACH candidate "
    "relationship, perform effective challenge using ONLY within-table evidence "
    "and domain logic. Decide a verdict and explain. Respond as STRICT JSON: "
    "{\"verdicts\": [{\"candidate_id\": int, \"verdict\": \"accept|reject|revise\", "
    "\"challenge\": str}]}. candidate_id is the 0-based index in the input list."
)


def validate(candidates: list[dict], metadata: dict,
             effort: EffortPolicy | None = None, on_event=None) -> list[dict]:
    if not candidates:
        return []
    policy = effort or EffortPolicy(agent_key="fermat")
    user = (f"WITHIN-TABLE METADATA:\n{json.dumps(metadata)[:2000]}\n\n"
            f"CANDIDATES (index = candidate_id):\n{json.dumps(candidates)[:3500]}")
    from ..skills import get_system_prompt
    raw = policy.run([{"role": "system", "content": get_system_prompt("fermat", _SYSTEM)},
                      {"role": "user", "content": user}], json_mode=True, on_event=on_event)
    try:
        return json.loads(raw).get("verdicts", [])
    except (ValueError, TypeError):
        return []
