from __future__ import annotations
from typing import ClassVar, Tuple
from great_expectations.expectations.expectation import BatchExpectation
from ..metrics import compute_ks_by_segment


class ExpectColumnKSBySegmentToBeBelowThreshold(BatchExpectation):
    column: str = ""
    segment_col: str = ""
    threshold: float = 0.20

    metric_dependencies: ClassVar[Tuple[str, ...]] = ("table.columns",)
    success_keys: ClassVar[Tuple[str, ...]] = ("column", "segment_col", "threshold")

    def _validate(self, metrics, runtime_configuration=None, execution_engine=None):
        df = execution_engine.get_domain_records({})
        r = compute_ks_by_segment(df, self.column, self.segment_col)
        return {"success": r["ks"] <= self.threshold, "result": {"observed_value": r["ks"]}}
