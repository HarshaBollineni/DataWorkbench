from __future__ import annotations
from typing import ClassVar, Tuple
from great_expectations.expectations.expectation import BatchExpectation
from ..metrics import conditional_spearman, relationship_holds


class ExpectConditionalColumnRelationship(BatchExpectation):
    column_A: str = ""
    column_B: str = ""
    relationship: str = "inverse"
    min_strength: float = 0.10

    metric_dependencies: ClassVar[Tuple[str, ...]] = ("table.columns",)
    success_keys: ClassVar[Tuple[str, ...]] = ("column_A", "column_B", "relationship", "min_strength")

    def _validate(self, metrics, runtime_configuration=None, execution_engine=None):
        df = execution_engine.get_domain_records({})
        rho = conditional_spearman(df, self.column_A, self.column_B)
        success = relationship_holds(rho, self.relationship, self.min_strength)
        return {"success": success, "result": {"observed_value": rho}}
