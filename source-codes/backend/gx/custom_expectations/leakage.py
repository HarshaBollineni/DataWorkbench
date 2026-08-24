"""REFERENCE custom expectation — the frozen pattern all wrappers copy.

GX v1.x (1.18) custom expectation:
  * declare kwargs as annotated class attributes with defaults
  * list them in ``success_keys``
  * read the full pandas batch via ``execution_engine.get_domain_records({})``
  * delegate ALL statistics to backend.gx.metrics (never compute here)
  * return ``{"success": bool, "result": {"observed_value": ...}}``
Do NOT use GX's reserved ``mostly`` kwarg — use explicit custom kwargs.
"""
from __future__ import annotations

from typing import ClassVar, List, Tuple

from great_expectations.expectations.expectation import BatchExpectation

from ..metrics import detect_leakage


class ExpectPostOutcomeColumnsToBeClean(BatchExpectation):
    """FAIL if any post-outcome column is present (and non-null) in the feature set."""

    post_outcome_cols: List[str] = []

    metric_dependencies: ClassVar[Tuple[str, ...]] = ("table.columns",)
    success_keys: ClassVar[Tuple[str, ...]] = ("post_outcome_cols",)

    def _validate(self, metrics, runtime_configuration=None, execution_engine=None):
        df = execution_engine.get_domain_records({})
        outcome = detect_leakage(df, list(self.post_outcome_cols))
        return {
            "success": not outcome["leaked"],
            "result": {"observed_value": outcome["leaked_columns"]},
        }
