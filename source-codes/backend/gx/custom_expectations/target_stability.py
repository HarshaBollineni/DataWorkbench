from __future__ import annotations
from typing import ClassVar, Tuple
from great_expectations.expectations.expectation import BatchExpectation
from ..metrics import target_rate_quarterly_range


class ExpectTargetRateToBeStructurallyStable(BatchExpectation):
    target_col: str = ""
    date_col: str = ""
    max_range: float = 0.15
    metric_dependencies: ClassVar[Tuple[str, ...]] = ("table.columns",)
    success_keys: ClassVar[Tuple[str, ...]] = ("target_col", "date_col", "max_range")

    def _validate(self, metrics, runtime_configuration=None, execution_engine=None):
        df = execution_engine.get_domain_records({})
        r = target_rate_quarterly_range(df, self.target_col, self.date_col)
        return {"success": r["range"] <= self.max_range, "result": {"observed_value": r["range"]}}
