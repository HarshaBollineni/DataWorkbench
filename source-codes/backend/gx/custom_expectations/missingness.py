"""Plan 3 — segment/window-aware missingness-mechanism expectation.

Wraps metrics.missingness_rate so the platform can test the null-rate of a
column optionally restricted to a segment value and/or a date window (the I2
mechanism: balance nulled for channel=Online during 2018-H2)."""
from __future__ import annotations

from typing import ClassVar, Tuple

from great_expectations.expectations.expectation import BatchExpectation

from ..metrics import MISSINGNESS_TOL, missingness_rate


class ExpectColumnMissingnessToBeBelowTolerance(BatchExpectation):
    column: str = ""
    segment_col: str = ""
    segment_value: str = ""
    date_col: str = ""
    date_start: str = ""
    date_end: str = ""
    tol: float = MISSINGNESS_TOL
    metric_dependencies: ClassVar[Tuple[str, ...]] = ("table.columns",)
    success_keys: ClassVar[Tuple[str, ...]] = (
        "column", "segment_col", "segment_value", "date_col",
        "date_start", "date_end", "tol",
    )

    def _validate(self, metrics, runtime_configuration=None, execution_engine=None):
        df = execution_engine.get_domain_records({})
        res = missingness_rate(
            df, self.column,
            self.segment_col or None, self.segment_value or None,
            self.date_col or None, self.date_start or None, self.date_end or None,
        )
        return {"success": res["rate"] <= self.tol,
                "result": {"observed_value": res["rate"], "n": res["n"]}}
