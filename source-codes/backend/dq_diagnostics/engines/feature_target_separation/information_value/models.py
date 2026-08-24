"""Versioned contracts for supervised binning and Information Value."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

TargetKind = Literal["binary", "continuous", "multinomial"]
FeatureKind = Literal["numeric", "categorical"]
Trend = Literal[
    "auto",
    "ascending",
    "descending",
    "peak",
    "valley",
    "convex",
    "concave",
    "none",
]


class BinningConstraints(BaseModel):
    """Fine- and coarse-binning controls shared by API and notebooks."""

    max_prebins: int = Field(default=50, ge=4, le=100)
    min_prebin_size: float = Field(default=0.01, gt=0, le=0.25)
    min_bins: int = Field(default=2, ge=1, le=20)
    max_bins: int = Field(default=8, ge=2, le=20)
    min_bin_size: float = Field(default=0.05, gt=0, le=0.5)
    max_bin_size: float | None = Field(default=None, gt=0, le=1)
    min_event_count: int = Field(default=1, ge=1)
    min_non_event_count: int = Field(default=1, ge=1)
    min_target_difference: float = Field(default=0, ge=0)
    monotonic_trend: Trend = "auto"
    max_pvalue: float | None = Field(default=None, gt=0, lt=1)
    rare_category_cutoff: float = Field(default=0.02, ge=0, le=0.25)
    solver_time_limit_seconds: int = Field(default=30, ge=1, le=300)
    user_splits: list[float] = Field(default_factory=list)
    fixed_user_splits: list[bool] = Field(default_factory=list)
    suggest_zero_boundary: bool = True
    create_out_of_range_guard_bins: bool = False
    execution_mode: Literal["full", "quick"] = "full"
    max_workers: int = Field(default=4, ge=1, le=8)

    @model_validator(mode="after")
    def validate_relationships(self) -> BinningConstraints:
        if self.min_bins > self.max_bins:
            raise ValueError("min_bins cannot exceed max_bins.")
        if self.max_bin_size is not None and self.min_bin_size > self.max_bin_size:
            raise ValueError("min_bin_size cannot exceed max_bin_size.")
        if self.fixed_user_splits and len(self.fixed_user_splits) != len(self.user_splits):
            raise ValueError("fixed_user_splits must match user_splits in length.")
        if self.user_splits != sorted(set(self.user_splits)):
            raise ValueError("user_splits must be unique and sorted.")
        return self


class BinDefinition(BaseModel):
    """Portable bin rules independent of any optimizer's private state."""

    schema_version: Literal[1] = 1
    feature: str
    feature_type: FeatureKind
    target: str | None = None
    target_type: TargetKind | None = None
    numeric_splits: list[float] = Field(default_factory=list)
    numeric_transform: Literal["identity", "datetime_days"] = "identity"
    observed_min: float | None = None
    observed_max: float | None = None
    out_of_range_guard_bins: bool = False
    categorical_groups: list[list[str]] = Field(default_factory=list)
    categorical_group_labels: list[str] = Field(default_factory=list)
    special_values: dict[str, list[Any]] = Field(default_factory=dict)
    missing_label: str = "Missing"
    unseen_label: str = "Unseen"
    method: str
    solver_status: str
    constraints: BinningConstraints
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_rules(self) -> BinDefinition:
        if self.feature_type == "numeric" and self.categorical_groups:
            raise ValueError("Numeric definitions cannot contain categorical groups.")
        if self.feature_type == "categorical" and self.numeric_splits:
            raise ValueError("Categorical definitions cannot contain numeric splits.")
        if self.numeric_splits != sorted(set(self.numeric_splits)):
            raise ValueError("numeric_splits must be unique and sorted.")
        flattened = [value for group in self.categorical_groups for value in group]
        if len(flattened) != len(set(flattened)):
            raise ValueError("A category cannot belong to more than one bin.")
        return self


class BinRow(BaseModel):
    bin_id: str
    label: str
    kind: Literal["regular", "missing", "special", "unseen", "guard"]
    rows: int
    population_share: float
    feature_min: float | None = None
    feature_max: float | None = None
    feature_mean: float | None = None
    events: int | None = None
    non_events: int | None = None
    event_rate: float | None = None
    event_distribution: float | None = None
    non_event_distribution: float | None = None
    woe: float | None = None
    iv: float | None = None
    target_mean: float | None = None
    target_std: float | None = None
    class_counts: dict[str, int] = Field(default_factory=dict)
    class_rates: dict[str, float] = Field(default_factory=dict)
    class_iv: dict[str, float] = Field(default_factory=dict)


class BinningMetric(BaseModel):
    name: str
    value: float
    interpretation: str


class FeatureBinningResult(BaseModel):
    feature: str
    target: str
    feature_type: FeatureKind
    target_type: TargetKind
    rows_evaluated: int
    fine_definition: BinDefinition
    coarse_definition: BinDefinition
    fine_bins: list[BinRow]
    coarse_bins: list[BinRow]
    fine_metrics: list[BinningMetric] = Field(default_factory=list)
    coarse_metrics: list[BinningMetric] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coarse_groups: list[list[str]] = Field(default_factory=list)
    binning_profile: dict[str, Any] = Field(default_factory=dict)


class BinningReviewResult(BaseModel):
    """Recalculated statistics for a user-edited coarse definition."""

    definition: BinDefinition
    bins: list[BinRow]
    metrics: list[BinningMetric]
    warnings: list[str] = Field(default_factory=list)
    coarse_groups: list[list[str]] = Field(default_factory=list)


class BinningTargetSpec(BaseModel):
    column: str
    target_type: TargetKind
    positive_class: str | int | float | bool | None = None


class BinningFeatureSpec(BaseModel):
    column: str
    feature_type: Literal["auto", "numeric", "categorical"] = "auto"
    special_values: dict[str, list[Any]] = Field(default_factory=dict)
    constraints: BinningConstraints | None = None


class FeatureBinningFailure(BaseModel):
    feature: str
    error: str


class BinningResponse(BaseModel):
    status: Literal["complete", "partial"] = "complete"
    target: BinningTargetSpec
    rows_evaluated: int
    results: list[FeatureBinningResult]
    failures: list[FeatureBinningFailure] = Field(default_factory=list)
    methodology: dict[str, Any]


ProgressCallback = Callable[[int, int, str], None]
