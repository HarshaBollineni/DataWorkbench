from __future__ import annotations
from typing import ClassVar, Tuple
from great_expectations.expectations.expectation import BatchExpectation
from ..metrics import compute_psi

class ExpectColumnPSIToBeBelowThreshold(BatchExpectation):
    column: str = ""
    date_col: str = ""
    threshold: float = 0.20
    metric_dependencies: ClassVar[Tuple[str, ...]] = ("table.columns",)
    success_keys: ClassVar[Tuple[str, ...]] = ("column", "date_col", "threshold")

    def _validate(self, metrics, runtime_configuration=None, execution_engine=None):
        df = execution_engine.get_domain_records({})
        psi = compute_psi(df, self.column, self.date_col)
        return {"success": psi <= self.threshold, "result": {"observed_value": psi}}
