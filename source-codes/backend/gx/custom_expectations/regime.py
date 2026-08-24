from __future__ import annotations
from typing import ClassVar, Tuple
from great_expectations.expectations.expectation import BatchExpectation
from ..metrics import downturn_regime_coverage


class ExpectDownturnRegimeCoverageToMeetMinimum(BatchExpectation):
    date_col: str = ""
    downturn_start: str = "2020-03"
    downturn_end: str = "2020-12"
    min_coverage: float = 0.05
    metric_dependencies: ClassVar[Tuple[str, ...]] = ("table.columns",)
    success_keys: ClassVar[Tuple[str, ...]] = ("date_col", "downturn_start", "downturn_end", "min_coverage")

    def _validate(self, metrics, runtime_configuration=None, execution_engine=None):
        df = execution_engine.get_domain_records({})
        r = downturn_regime_coverage(df, self.date_col, self.downturn_start, self.downturn_end)
        success = (not r["applicable"]) or (r["coverage"] >= self.min_coverage)
        return {"success": success, "result": {"observed_value": r["coverage"]}}
