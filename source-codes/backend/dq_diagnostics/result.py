"""FWK-07 — the diagnostic result contract every engine emits.

`DiagnosticResult` is decision-type-shaped: `decision_type` selects which
companion fields are mandatory, and the shape is validated once in
`__post_init__` — so no engine, present or future, can construct a
structurally invalid result and have it silently flow downstream.

Five decision types exist across the 9-row register (docs/0.4.0/00-framework.md
§3): a threshold-flavoured "threshold+SME" row (#14, PSI) canonicalizes to
`contextual` — the same shape as #11/#17, which are also a computed metric
that needs SME interpretation rather than a bare pass/fail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DECISION_TYPES = {"verdict", "candidate_flag", "contextual", "classification_output", "sme_gate"}
VERDICTS = {"pass", "violation", "inconclusive", "not_applicable"}
REVIEW_STATES = {"open", "confirmed", "dismissed"}

# decision types whose result must carry a `review_state` (a flag awaiting
# human disposition, as opposed to a verdict already computed).
_REVIEW_STATE_TYPES = {"candidate_flag", "contextual"}


class InvalidDiagnosticResultError(ValueError):
    """Raised by DiagnosticResult.__post_init__ when the decision_type /
    companion-field shape is missing or inconsistent."""


@dataclass
class DiagnosticResult:
    """The one shape every diagnostic engine returns, regardless of mode.

    `decision_type` is the only field without a default — omit it and
    Python's own dataclass machinery raises (missing required argument)
    before __post_init__ ever runs. Passing an explicit invalid value
    (None, or one outside DECISION_TYPES) is caught here instead, as is
    every companion-field mismatch:
      - decision_type == 'verdict' requires `verdict` in VERDICTS, and
        `na_reason` whenever verdict == 'not_applicable'.
      - decision_type in {'candidate_flag', 'contextual'} requires
        `review_state` in REVIEW_STATES.
      - 'classification_output' / 'sme_gate' carry no additional required
        field here — the former's payload lives in `evidence` (the tags
        themselves), the latter is a workflow marker with no computed
        metric at all.
    """

    decision_type: str
    diagnostic_id: int | None = None
    verdict: str | None = None
    na_reason: str | None = None
    review_state: str | None = None
    metric: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.decision_type or self.decision_type not in DECISION_TYPES:
            raise InvalidDiagnosticResultError(
                f"decision_type must be one of {sorted(DECISION_TYPES)}, got {self.decision_type!r}"
            )
        if self.decision_type == "verdict":
            if self.verdict not in VERDICTS:
                raise InvalidDiagnosticResultError(
                    f"verdict-typed result requires verdict in {sorted(VERDICTS)}, got {self.verdict!r}"
                )
            if self.verdict == "not_applicable" and not self.na_reason:
                raise InvalidDiagnosticResultError(
                    "verdict='not_applicable' requires a non-empty na_reason"
                )
        elif self.decision_type in _REVIEW_STATE_TYPES:
            if self.review_state not in REVIEW_STATES:
                raise InvalidDiagnosticResultError(
                    f"{self.decision_type!r} result requires review_state in {sorted(REVIEW_STATES)}, "
                    f"got {self.review_state!r}"
                )
