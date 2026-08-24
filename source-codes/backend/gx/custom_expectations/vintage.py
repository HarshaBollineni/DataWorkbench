from __future__ import annotations
from typing import ClassVar, Tuple
from great_expectations.expectations.expectation import BatchExpectation
from ..metrics import observation_window_months

class ExpectObservationWindowDepthToMeetMinimum(BatchExpectation):
    date_col: str = ""
    min_months: int = 24
    metric_dependencies: ClassVar[Tuple[str, ...]] = ("table.columns",)
    success_keys: ClassVar[Tuple[str, ...]] = ("date_col", "min_months")

    def _validate(self, metrics, runtime_configuration=None, execution_engine=None):
        df = execution_engine.get_domain_records({})
        months = observation_window_months(df, self.date_col)
        return {"success": months >= self.min_months, "result": {"observed_value": months}}
