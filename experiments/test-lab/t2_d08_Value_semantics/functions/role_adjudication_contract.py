"""Provider-independent structured contract for semantic-role adjudication."""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ProductionRole = Annotated[str, StringConstraints(pattern=r"^(identifier|period|target|feature|score|weight|date|ignore|group)$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InputVariable(StrictModel):
    name: NonEmptyString
    description: str | None = None
    data_type: str | None = None
    role: ProductionRole = "ignore"
    business_name: str | None = None
    allowed_values: str | None = None
    model_type: str | None = None
    use_cases: list[str] = Field(default_factory=list)
    product: str | None = None


class AbbreviationAmbiguity(StrictModel):
    abbreviation: NonEmptyString
    candidates: list[NonEmptyString] = Field(min_length=2)
    note: str | None = None


class RoleOverview(StrictModel):
    role: NonEmptyString
    definition: NonEmptyString
    role_kind: NonEmptyString
    model_types: list[NonEmptyString] = Field(default_factory=list)


class DetailedRoleCandidate(StrictModel):
    role: NonEmptyString
    definition: NonEmptyString
    representations: list[NonEmptyString]
    matching_note: str | None = None


class RoleAdjudicationInput(StrictModel):
    input_variable: InputVariable
    role_catalog_version: NonEmptyString
    full_catalog: list[RoleOverview] = Field(min_length=1)
    detailed_candidates: list[DetailedRoleCandidate] = Field(min_length=1)
    unresolved_abbreviations: list[AbbreviationAmbiguity] = Field(default_factory=list)

    @model_validator(mode="after")
    def identifiers_are_unique(self):
        catalog = [item.role for item in self.full_catalog]
        detailed = [item.role for item in self.detailed_candidates]
        if len(catalog) != len(set(catalog)) or len(detailed) != len(set(detailed)):
            raise ValueError("Role identifiers must be unique")
        if not set(detailed) <= set(catalog):
            raise ValueError("Every detailed candidate must appear in the full catalog")
        return self


class RoleDecision(str, Enum):
    MATCH = "MATCH"
    MULTI_ROLE_MATCH = "MULTI_ROLE_MATCH"
    NO_CANDIDATE_MATCH = "NO_CANDIDATE_MATCH"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    AMBIGUOUS_ROLE = "AMBIGUOUS_ROLE"
    CANDIDATE_SET_INCOMPLETE = "CANDIDATE_SET_INCOMPLETE"


class RoleAdjudicationOutput(StrictModel):
    decision: RoleDecision
    primary_role: NonEmptyString | None = None
    secondary_roles: list[NonEmptyString] = Field(default_factory=list)
    requested_expansion_roles: list[NonEmptyString] = Field(default_factory=list)
    reason: NonEmptyString

    @model_validator(mode="after")
    def decision_fields_agree(self):
        if self.decision is RoleDecision.MATCH:
            if self.primary_role is None or self.secondary_roles or self.requested_expansion_roles:
                raise ValueError("MATCH requires only primary_role")
        elif self.decision is RoleDecision.MULTI_ROLE_MATCH:
            if self.primary_role is None or not self.secondary_roles or self.requested_expansion_roles:
                raise ValueError("MULTI_ROLE_MATCH requires primary_role and secondary_roles")
        elif self.decision is RoleDecision.CANDIDATE_SET_INCOMPLETE:
            if self.primary_role is not None or self.secondary_roles or not self.requested_expansion_roles:
                raise ValueError("CANDIDATE_SET_INCOMPLETE requires only requested_expansion_roles")
        elif self.primary_role is not None or self.secondary_roles or self.requested_expansion_roles:
            raise ValueError("Non-match decisions cannot select or request roles")
        return self


def validate_role_adjudication(input_: RoleAdjudicationInput, output: RoleAdjudicationOutput) -> RoleAdjudicationOutput:
    catalog = {item.role for item in input_.full_catalog}
    detailed = {item.role for item in input_.detailed_candidates}
    selected = ({output.primary_role} if output.primary_role else set()) | set(output.secondary_roles)
    if not selected <= detailed:
        raise ValueError(f"Selected roles were not supplied with details: {sorted(selected - detailed)}")
    requested = set(output.requested_expansion_roles)
    if not requested <= catalog:
        raise ValueError(f"Expansion requested unknown catalog roles: {sorted(requested - catalog)}")
    if requested & detailed:
        raise ValueError("Expansion may only request roles absent from the detailed subset")
    return output


def build_adjudication_input(match_result: dict, kb: dict) -> RoleAdjudicationInput:
    return RoleAdjudicationInput(
        input_variable=InputVariable(
            name=match_result["input_variable"],
            description=match_result.get("description") or None,
            data_type=match_result.get("data_type"),
            role=match_result["production_role"],
            business_name=match_result.get("business_name") or None,
            allowed_values=match_result.get("allowed_values") or None,
            model_type=match_result.get("model_type") or None,
            use_cases=list(match_result.get("use_cases") or []),
            product=match_result.get("product") or None,
        ),
        role_catalog_version=str(kb["metadata"]["version"]),
        full_catalog=match_result["full_catalog"],
        detailed_candidates=[
            {
                "role": item["role"],
                "definition": item["definition"],
                "representations": item["representations"],
                "matching_note": item.get("matching_note"),
            }
            for item in match_result["detailed_candidates"]
        ],
        unresolved_abbreviations=match_result.get("unresolved_abbreviations", []),
    )


def expand_adjudication_input(
    input_: RoleAdjudicationInput,
    requested_roles: list[str],
    kb: dict,
) -> RoleAdjudicationInput:
    """Return a new contract input with requested catalog roles fully detailed."""
    role_index = {role["role"]: role for role in kb["semantic_roles"]}
    existing = {candidate.role for candidate in input_.detailed_candidates}
    unknown = set(requested_roles) - set(role_index)
    if unknown:
        raise ValueError(f"Cannot expand unknown roles: {sorted(unknown)}")
    additions = [
        DetailedRoleCandidate(
            role=role_id,
            definition=role_index[role_id]["definition"],
            representations=list(role_index[role_id]["representations"]),
            matching_note=role_index[role_id].get("matching_note"),
        )
        for role_id in requested_roles
        if role_id not in existing
    ]
    return input_.model_copy(update={"detailed_candidates": [*input_.detailed_candidates, *additions]})
