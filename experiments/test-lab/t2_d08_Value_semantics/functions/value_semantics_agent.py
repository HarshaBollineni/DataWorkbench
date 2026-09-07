"""Offline orchestration for role matching and deterministic rule routing."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .role_adjudication_contract import (
    RoleDecision,
    RoleAdjudicationOutput,
    build_adjudication_input,
    expand_adjudication_input,
    validate_role_adjudication,
)
from .kb_loader import expand_implied_roles
from .llm_role_adjudication import RoleAdjudicationCall
from .role_matching import PreparedRoleMatcher, match_variable_to_roles
from .rule_router import route_roles_to_rules


Adjudicator = Callable[[Any], RoleAdjudicationOutput]


def _invoke_adjudicator(adjudicator: Any, input_: Any) -> RoleAdjudicationCall:
    result = adjudicator.adjudicate(input_) if hasattr(adjudicator, "adjudicate") else adjudicator(input_)
    return result if isinstance(result, RoleAdjudicationCall) else RoleAdjudicationCall(output=result)


def process_schema(
    columns: Iterable[Mapping[str, Any]],
    prepared: PreparedRoleMatcher,
    *,
    declarations: Mapping[str, Any] | None = None,
    adjudicator: Adjudicator | None = None,
) -> dict[str, Any]:
    matches = [match_variable_to_roles(column, prepared) for column in columns]
    available_roles = {
        result["exact_match"]["role"]
        for result in matches
        if result["match_status"] == "exact_match"
    }
    binding_rows: list[dict[str, Any]] = []
    for result in matches:
        if result["match_status"] == "exact_match":
            roles = [result["exact_match"]["role"]]
            binding_rows.append({"input_variable": result["input_variable"], "decision": "MATCH", "roles": roles, "review_required": False})
            continue
        if adjudicator is None:
            binding_rows.append({"input_variable": result["input_variable"], "decision": "PENDING_ADJUDICATION", "roles": [], "review_required": True, "match_result": result})
            continue
        input_ = build_adjudication_input(result, dict(prepared.kb))
        call = _invoke_adjudicator(adjudicator, input_)
        output = validate_role_adjudication(input_, call.output)
        expansion_passes = 0
        while output.decision is RoleDecision.CANDIDATE_SET_INCOMPLETE:
            expansion_passes += 1
            if expansion_passes > 3:
                raise RuntimeError("Role adjudication exceeded three candidate-expansion passes")
            input_ = expand_adjudication_input(input_, output.requested_expansion_roles, dict(prepared.kb))
            call = _invoke_adjudicator(adjudicator, input_)
            output = validate_role_adjudication(input_, call.output)
        direct_roles = ([output.primary_role] if output.primary_role else []) + list(output.secondary_roles)
        roles = expand_implied_roles(direct_roles, prepared.kb)
        available_roles.update(roles)
        binding_rows.append({"input_variable": result["input_variable"], "decision": output.decision.value,
                             "direct_roles": direct_roles, "roles": roles, "review_required": True,
                             "reason": output.reason, "response_id": call.response_id,
                             "response_model": call.response_model})

    # Exact bindings may also carry deterministic role implications.
    for binding in binding_rows:
        binding["direct_roles"] = binding.get("direct_roles", list(binding["roles"]))
        binding["roles"] = expand_implied_roles(binding["roles"], prepared.kb)
    available_roles = {role for binding in binding_rows for role in binding["roles"]}
    binding_index: dict[str, list[str]] = {}
    for binding in binding_rows:
        for role in binding["roles"]:
            binding_index.setdefault(role, []).append(binding["input_variable"])
    cardinality = {role["role"]: role["binding_cardinality"] for role in prepared.kb["semantic_roles"]}
    cardinality_violations = [
        {"role": role, "cardinality": cardinality[role], "input_variables": variables}
        for role, variables in binding_index.items()
        if cardinality[role] == "one" and len(variables) > 1
    ]

    routing = []
    for binding in binding_rows:
        if binding["roles"]:
            for row in route_roles_to_rules(binding["roles"], prepared.kb, available_roles=available_roles, declarations=declarations):
                routing.append({"input_variable": binding["input_variable"], **row})
    return {"bindings": binding_rows, "binding_index": binding_index,
            "cardinality_violations": cardinality_violations, "routing": routing}
