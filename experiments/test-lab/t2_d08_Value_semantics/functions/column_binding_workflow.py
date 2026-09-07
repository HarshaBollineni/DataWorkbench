"""Resolve semantic-role bindings for arbitrary dictionary-shaped columns."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from .kb_loader import expand_implied_roles
from .role_adjudication_contract import (
    RoleDecision, build_adjudication_input, expand_adjudication_input,
    validate_role_adjudication,
)
from .role_adjudication_validation import AdjudicationCheckpoint, adjudication_checkpoint_key
from .role_matching import PreparedRoleMatcher, match_variable_to_roles


def resolve_column_bindings(columns: Iterable[Mapping[str, Any]], prepared: PreparedRoleMatcher,
                            *, adjudicator: Any | None = None,
                            checkpoint_path: str | Path | None = None,
                            binding_overrides: Mapping[str, list[str]] | None = None) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Match columns, optionally adjudicate unresolved cases, and return reviewed bindings."""
    checkpoint = AdjudicationCheckpoint(checkpoint_path)
    overrides = {str(key): list(value) for key, value in (binding_overrides or {}).items()}
    rows: list[dict[str, Any]] = []
    bindings: dict[str, list[str]] = {}
    for column in columns:
        match = match_variable_to_roles(column, prepared)
        name = match["input_variable"]
        base = {
            "column_name": name, "production_role": match["production_role"],
            "description_supplied": bool(match.get("description")),
            "match_status": match["match_status"],
            "detailed_candidate_count": len(match["detailed_candidates"]),
            "adjudication_used": False, "checkpoint_hit": False,
            "decision": "", "direct_roles": "", "resolved_roles": "",
            "review_required": False, "reason": "", "response_id": "", "error": "",
        }
        if name in overrides:
            direct = overrides[name]
            resolved = expand_implied_roles(direct, prepared.kb)
            bindings[name] = resolved
            rows.append({**base, "decision": "REVIEWED_OVERRIDE", "direct_roles": ";".join(direct),
                         "resolved_roles": ";".join(resolved), "reason": "Explicit notebook binding override"})
            continue
        if match["match_status"] == "exact_match":
            direct = [match["exact_match"]["role"]]
            resolved = expand_implied_roles(direct, prepared.kb)
            bindings[name] = resolved
            rows.append({**base, "decision": "MATCH", "direct_roles": ";".join(direct),
                         "resolved_roles": ";".join(resolved), "reason": "Deterministic exact match"})
            continue
        if adjudicator is None:
            rows.append({**base, "decision": "PENDING_ADJUDICATION", "review_required": True,
                         "reason": "No exact role and LLM fallback is disabled"})
            continue
        base["adjudication_used"] = True
        base["review_required"] = True
        input_ = build_adjudication_input(match, dict(prepared.kb))
        try:
            expansion_passes = 0
            while True:
                key = adjudication_checkpoint_key(input_, adjudicator, prepared.kb)
                call = checkpoint.get(key)
                if call is None:
                    call = adjudicator.adjudicate(input_)
                    checkpoint.put(key, call)
                else:
                    base["checkpoint_hit"] = True
                output = validate_role_adjudication(input_, call.output)
                if output.decision is not RoleDecision.CANDIDATE_SET_INCOMPLETE:
                    break
                expansion_passes += 1
                if expansion_passes > 3:
                    raise RuntimeError("Role adjudication exceeded three candidate-expansion passes")
                input_ = expand_adjudication_input(input_, output.requested_expansion_roles, dict(prepared.kb))
            direct = ([output.primary_role] if output.primary_role else []) + list(output.secondary_roles)
            resolved = expand_implied_roles(direct, prepared.kb)
            if resolved:
                bindings[name] = resolved
            rows.append({**base, "decision": output.decision.value, "direct_roles": ";".join(direct),
                         "resolved_roles": ";".join(resolved), "reason": output.reason,
                         "response_id": call.response_id or ""})
        except Exception as exc:
            rows.append({**base, "decision": "ERROR", "review_required": True,
                         "error": f"{type(exc).__name__}: {exc}"})
    return pd.DataFrame(rows), bindings
