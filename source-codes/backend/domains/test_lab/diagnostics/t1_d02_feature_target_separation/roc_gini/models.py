"""Contracts for risk-weighted single-feature analysis."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

TargetType = Literal["auto", "binary", "continuous", "multinomial"]
FeatureType = Literal["auto", "numeric", "categorical"]
MissingTargetAction = Literal["prompt", "drop"]


class AnalysisConstraints(BaseModel):
    """Validated run-level controls for global and localized leakage diagnostics."""

    global_medium_auc: float = Field(0.60, ge=0.50, le=1.0)
    global_high_auc: float = Field(0.75, ge=0.50, le=1.0)
    stability_tolerance: float = Field(0.05, ge=0.0, le=0.50)
    localized_target_rate_threshold: float = Field(0.90, ge=0.50, le=1.0)
    localized_minimum_rate_increase: float = Field(0.10, ge=0.0, le=1.0)
    localized_minimum_target_count: int = Field(5, ge=1)
    localized_minimum_population_fraction: float = Field(0.001, gt=0.0, le=0.25)
    localized_minimum_rows_floor: int = Field(30, ge=1)
    localized_adjusted_p_value_threshold: float = Field(0.01, gt=0.0, le=0.25)

    @model_validator(mode="after")
    def validate_ordering(self) -> AnalysisConstraints:
        if self.global_medium_auc >= self.global_high_auc:
            raise ValueError("global_medium_auc must be lower than global_high_auc.")
        return self


class TargetSpec(BaseModel):
    """One target selected for universal analysis."""

    column: str
    target_type: TargetType = "auto"
    positive_class: str | int | float | bool | None = None
    class_order: list[str] | None = None


class FeatureSpec(BaseModel):
    """One independent variable and its missing/special-value metadata."""

    column: str
    feature_type: FeatureType = "auto"
    special_values: list[str | int | float] = Field(default_factory=lambda: [-999])


class AnalysisMessage(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ConfigurationMetric(BaseModel):
    configuration: Literal["primary", "conservative", "permissive"]
    auc: float
    gini: float
    macro_auc: float
    weighted_auc: float
    class_auc: dict[str, float] = Field(default_factory=dict)
    risk_tier: Literal["low", "medium", "high"]
    leaves: int
    maximum_leaves: int
    minimum_leaf_rows: int
    leaf_cap_reached: bool


class LeafSummary(BaseModel):
    leaf_id: int
    rule_conditions: list[str] = Field(default_factory=list)
    rows: int
    target_events: int | None = None
    target_mean: float
    score: float
    class_distribution: dict[str, int] = Field(default_factory=dict)
    regular_rows: int
    generic_missing_rows: int
    special_rows: dict[str, int] = Field(default_factory=dict)


class TreeNode(BaseModel):
    node_id: int
    depth: int
    node_type: Literal["split", "leaf"]
    prompt: str | None = None
    left_child: int | None = None
    right_child: int | None = None
    left_label: str | None = None
    right_label: str | None = None
    rows: int
    target_mean: float | None = None
    target_events: int | None = None
    leading_class: str | None = None
    leading_class_rate: float | None = None
    parent_delta: float | None = None


class BenchmarkResult(BaseModel):
    method: str
    rows_evaluated: int
    groups: int
    auc: float
    gini: float
    macro_auc: float
    weighted_auc: float
    class_auc: dict[str, float] = Field(default_factory=dict)
    buckets: list[dict[str, Any]] = Field(default_factory=list)


class LocalizedLeakageCandidate(BaseModel):
    feature: str
    rule: str
    linked_outcome: str
    signal_type: Literal["direct_target_link", "class_concentration", "target_tail"]
    rows: int
    population_share: float
    target_count: int
    target_rate: float
    baseline_rate: float
    lift: float
    target_capture: float
    adjusted_p_value: float
    primary_auc: float
    primary_gini: float


class LocalizedLeakageScan(BaseModel):
    status: Literal["candidates_found", "no_candidates"]
    description: str
    candidates: list[LocalizedLeakageCandidate] = Field(default_factory=list)
    tests_evaluated: int
    minimum_rows: int
    minimum_target_count: int
    target_rate_threshold: float
    adjusted_p_value_threshold: float


class FeatureResult(BaseModel):
    feature: str
    target_type: Literal["binary", "continuous", "multinomial"]
    feature_type: Literal["numeric", "categorical"]
    rows_evaluated: int
    regular_rows: int
    generic_missing_rows: int
    special_value_rows: dict[str, int] = Field(default_factory=dict)
    primary_auc: float
    primary_gini: float
    primary_risk: Literal["low", "medium", "high"]
    macro_auc: float
    weighted_auc: float
    class_auc: dict[str, float] = Field(default_factory=dict)
    configurations: list[ConfigurationMetric]
    auc_min: float
    auc_max: float
    auc_spread: float
    robustness: str
    transformation: str
    optimization_profile: dict[str, Any] = Field(default_factory=dict)
    tree_rules: str
    primary_tree: list[TreeNode] = Field(default_factory=list)
    leaves: list[LeafSummary]
    percentile_benchmark: BenchmarkResult | None = None
    messages: list[AnalysisMessage] = Field(default_factory=list)


class TargetResult(BaseModel):
    target: str
    status: Literal["ready", "action_required", "invalid"]
    target_type: Literal["binary", "continuous", "multinomial"] | None = None
    input_rows: int
    evaluated_rows: int = 0
    missing_target_rows: int = 0
    dropped_target_rows: int = 0
    target_mapping: dict[str, int] = Field(default_factory=dict)
    messages: list[AnalysisMessage] = Field(default_factory=list)
    features: list[FeatureResult] = Field(default_factory=list)
    localized_leakage_scan: LocalizedLeakageScan | None = None


class AnalysisResponse(BaseModel):
    status: Literal["complete", "action_required", "invalid"]
    methodology: dict[str, Any]
    targets: list[TargetResult]
    total_feature_target_pairs: int
    trees_run: int
    full_sensitivity_trees: int
    trees_avoided: int
