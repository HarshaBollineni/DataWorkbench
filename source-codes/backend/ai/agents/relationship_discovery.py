"""Plan 3 / D3.2 — Relationship Discovery agent (call-name 'Poincaré'). LLM, high.

Single-responsibility: given a DiscoveryState (Newton's machine-readable profile)
or legacy Fisher screening + metadata, propose multivariate, domain-centric,
business-relevant candidate relationships / distribution & stability checks.
The orchestrator runs the loop.

Preferred call: pass ``discovery_state`` (Newton's DiscoveryState). The legacy
``screening`` + ``metadata`` path is kept for backward compatibility.
"""
from __future__ import annotations

import json

from ..effort import EffortPolicy

_SYSTEM = (
    "You are Poincaré, a senior credit-risk data scientist. Given a database "
    "profile with per-column statistics, null rates, distributions, anomalies, "
    "and cross-table join candidates, propose the most business-relevant "
    "candidate relationships and data-quality checks (distribution, stability, "
    "multivariate consistency). Favour relationships grounded in lending / risk "
    "domain logic. When DQ FRAMEWORK PRIORITIES are provided, favour candidates "
    "that map to the highest-priority IN-SCOPE areas for the stated model family "
    "and tag each with the matching framework_area + family_priority. Respond as "
    "STRICT JSON: {\"candidates\": [{\"relationship\": str, \"columns\": [str], "
    "\"rationale\": str, \"domain_basis\": str, \"suggested_test\": str, "
    "\"framework_area\": str (optional), \"family_priority\": str (optional)}]}. "
    "Propose 3-6 high-value candidates."
)


def _build_user_from_discovery_state(ds, accepted: list | None) -> str:
    """Build a richer, multi-table-aware user prompt from DiscoveryState."""
    from ..discovery_state import DiscoveryState as _DS
    assert isinstance(ds, _DS)
    avoid = ""
    if accepted:
        avoid = ("\n\nAlready accepted (propose NEW, non-overlapping ones):\n" +
                 json.dumps(accepted)[:1500])
    return f"DATABASE PROFILE:\n{ds.compress()}{avoid}"


def discover(
    screening: dict | None = None,
    metadata: dict | None = None,
    accepted: list | None = None,
    effort: EffortPolicy | None = None,
    on_event=None,
    *,
    discovery_state=None,  # DiscoveryState | None — preferred over screening/metadata
    framework_brief: str | None = None,  # DQ Framework priorities for the DB's family
) -> list[dict]:
    policy = effort or EffortPolicy(agent_key="poincare")

    if discovery_state is not None:
        user = _build_user_from_discovery_state(discovery_state, accepted)
    else:
        # Legacy path: Fisher screening + table metadata dict.
        avoid = ""
        if accepted:
            avoid = ("\nAlready accepted (propose NEW, non-overlapping ones):\n" +
                     json.dumps(accepted)[:1500])
        user = (f"TABLE METADATA:\n{json.dumps(metadata or {})[:2000]}\n\n"
                f"COLUMN PROFILE:\n{json.dumps(screening or {})[:3000]}{avoid}")

    if framework_brief:
        user += f"\n\n{framework_brief}"

    from ..skills import get_system_prompt
    raw = policy.run(
        [{"role": "system", "content": get_system_prompt("poincare", _SYSTEM)},
         {"role": "user", "content": user}],
        json_mode=True, on_event=on_event,
    )
    try:
        return json.loads(raw).get("candidates", [])
    except (ValueError, TypeError):
        return []
