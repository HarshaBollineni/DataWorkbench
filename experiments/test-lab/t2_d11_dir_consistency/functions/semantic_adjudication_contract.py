"""Provider-independent contracts for semantic feature adjudication.

This module deliberately contains no model client, prompt, matching logic, direction
metadata, or performance statistics.  It defines the boundary that any future
semantic adjudicator must obey.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _StrictContractModel(BaseModel):
    """Common configuration for rejecting undeclared contract fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InputFeature(_StrictContractModel):
    """Feature metadata visible to the semantic adjudicator."""

    name: NonEmptyString
    description: str | None = None


class Candidate(_StrictContractModel):
    """One canonical concept that the adjudicator is allowed to select."""

    canonical_feature: NonEmptyString
    definition: NonEmptyString
    representations: list[NonEmptyString]
    inverse_representations: list[NonEmptyString] = Field(default_factory=list)


class AdjudicationInput(_StrictContractModel):
    """Leakage-limited input containing only semantic identification metadata."""

    input_feature: InputFeature
    candidates: list[Candidate] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def canonical_features_are_unique(self) -> AdjudicationInput:
        names = [candidate.canonical_feature for candidate in self.candidates]
        if len(names) != len(set(names)):
            raise ValueError("candidate canonical_feature values must be unique")
        return self


class AdjudicationDecision(str, Enum):
    """Controlled outcomes from semantic feature adjudication."""

    MATCH = "MATCH"
    NO_CANDIDATE_MATCH = "NO_CANDIDATE_MATCH"
    NOT_DIRECTIONAL = "NOT_DIRECTIONAL"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"


DECISION_DEFINITIONS: dict[AdjudicationDecision, str] = {
    AdjudicationDecision.MATCH: (
        "The feature is understood, numerical directionality is meaningful, and one supplied "
        "candidate adequately represents its economic meaning."
    ),
    AdjudicationDecision.NO_CANDIDATE_MATCH: (
        "The feature is understood and numerical directionality is meaningful, but none of the "
        "supplied candidates adequately represents its economic meaning."
    ),
    AdjudicationDecision.NOT_DIRECTIONAL: (
        "The input feature can be understood, but an intrinsic increasing/decreasing numeric "
        "interpretation is not meaningful. This does not mean that the feature cannot be predictive."
    ),
    AdjudicationDecision.INSUFFICIENT_CONTEXT: (
        "The feature name and description do not provide enough information to reliably determine "
        "its economic meaning."
    ),
}


class RepresentationOrientation(str, Enum):
    """Relationship between the input representation and canonical concept."""

    SAME = "SAME"
    INVERSE = "INVERSE"
    UNDETERMINED = "UNDETERMINED"


ORIENTATION_DEFINITIONS: dict[RepresentationOrientation, str] = {
    RepresentationOrientation.SAME: (
        "Increasing values of the input feature represent increasing values of the selected "
        "canonical concept."
    ),
    RepresentationOrientation.INVERSE: (
        "Increasing values of the input feature represent decreasing values of the selected "
        "canonical concept."
    ),
    RepresentationOrientation.UNDETERMINED: (
        "The canonical concept can be identified, but the available metadata does not establish "
        "numerical orientation reliably."
    ),
}


class AdjudicationOutput(_StrictContractModel):
    """The complete and exact output shape required from an adjudicator."""

    decision: AdjudicationDecision
    selected_candidate: NonEmptyString | None
    representation_orientation: RepresentationOrientation | None
    reason: NonEmptyString

    @model_validator(mode="after")
    def fields_agree_with_decision(self) -> AdjudicationOutput:
        if self.decision is AdjudicationDecision.MATCH:
            if self.selected_candidate is None:
                raise ValueError("selected_candidate is required when decision is MATCH")
            if self.representation_orientation is None:
                raise ValueError("representation_orientation is required when decision is MATCH")
        else:
            if self.selected_candidate is not None:
                raise ValueError("selected_candidate must be null when decision is not MATCH")
            if self.representation_orientation is not None:
                raise ValueError("representation_orientation must be null when decision is not MATCH")
        return self


class AdjudicationResultValidationError(ValueError):
    """Raised when a structurally valid output violates the input/output boundary."""


def validate_adjudication_result(
    adjudication_input: AdjudicationInput,
    adjudication_output: AdjudicationOutput,
) -> AdjudicationOutput:
    """Validate output against its input and return the validated output.

    Model validation handles the output's internal invariants.  This explicit
    boundary check prevents a MATCH from selecting a canonical feature that was
    not included in the supplied candidate set.
    """

    if adjudication_output.decision is AdjudicationDecision.MATCH:
        supplied = {
            candidate.canonical_feature for candidate in adjudication_input.candidates
        }
        if adjudication_output.selected_candidate not in supplied:
            raise AdjudicationResultValidationError(
                "selected_candidate "
                f"{adjudication_output.selected_candidate!r} was not supplied; "
                f"allowed candidates are {sorted(supplied)!r}"
            )
    return adjudication_output


DETERMINISTIC_EXACT_MATCH_SOURCES = frozenset(
    {"deterministic_exact", "canonical", "representation", "inverse_representation"}
)


def determine_review_required(match_source: str, adjudication_used: bool) -> bool:
    """Apply system-owned MVP review policy, separate from adjudicator output.

    Every semantic adjudication requires review.  A recognized deterministic
    exact match does not.  Unknown or future non-adjudicated sources default to
    review, which is the conservative behavior until policy explicitly changes.
    """

    if not isinstance(match_source, str) or not match_source.strip():
        raise ValueError("match_source must be a non-empty string")
    if not isinstance(adjudication_used, bool):
        raise TypeError("adjudication_used must be a bool")
    return adjudication_used or match_source not in DETERMINISTIC_EXACT_MATCH_SOURCES
