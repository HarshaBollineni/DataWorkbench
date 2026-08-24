"""Plan 3 / D5.6 — RCA Checker agent (call-name 'Noether').

Vets an RCA finding (from rca_agent) before resolution — effective challenge on
the diagnosed root cause + proposed remediation. Returns {verdict, issues,
approved, ticket_ready}.
"""
from __future__ import annotations

import json

from .effort import EffortPolicy

_SYSTEM = (
    "You are Noether, an independent RCA checker performing effective challenge. "
    "Given a failed data-quality test and the RCA agent's diagnosed root cause + "
    "evidence + proposed remediation, decide whether the finding is sound and "
    "actionable and ready to become a ticket. Be sceptical of unsupported causal claims. "
    "Respond STRICT JSON: {\"verdict\": \"accept|revise|reject\", \"issues\": [str], "
    "\"approved\": bool, \"suggestion\": str, \"note\": str, \"ticket_ready\": bool, "
    "\"missing_evidence\": [str], \"alternative_causes_not_ruled_out\": [str], "
    "\"required_next_probe\": str, \"signoff_summary\": str, \"confidence_floor_met\": bool}."
)


def check(rca_result: dict, failed_test: dict | None = None,
          context_bundle: dict | None = None,
          effort: EffortPolicy | None = None, on_event=None) -> dict:
    policy = effort or EffortPolicy(agent_key="noether")
    user = (f"FAILED TEST:\n{json.dumps(failed_test or {})[:1200]}\n\n"
            f"RCA FINDING:\n{json.dumps(rca_result)[:2500]}\n\n"
            f"CONTEXT MEMORY:\n{json.dumps(context_bundle or {})[:1500]}")
    from .skills import get_system_prompt
    raw = policy.run([{"role": "system", "content": get_system_prompt("noether", _SYSTEM)},
                      {"role": "user", "content": user}], json_mode=True, on_event=on_event)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"verdict": "revise", "issues": ["Checker output unparseable."],
                "approved": False, "ticket_ready": False, "suggestion": "",
                "note": raw[:300]}
    if data.get("verdict") == "approve":
        data["verdict"] = "accept"
    data.setdefault("approved", data.get("verdict") == "accept")
    data.setdefault("ticket_ready", bool(data.get("approved")))
    data.setdefault("issues", [])
    data.setdefault("suggestion", "")
    data.setdefault("missing_evidence", [])
    data.setdefault("alternative_causes_not_ruled_out", [])
    data.setdefault("required_next_probe", "")
    data.setdefault("signoff_summary", data.get("note", ""))
    data.setdefault("confidence_floor_met", bool(data.get("approved")))
    return data
