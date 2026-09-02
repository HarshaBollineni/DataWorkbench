"""Deterministic empirical evidence for feature/reference directionality."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from collections.abc import Iterable
from typing import Any
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LinearRegression, LogisticRegression


class ReferenceKind(str, Enum):
    BINARY = "BINARY"
    CONTINUOUS = "CONTINUOUS"


class EvidenceStatus(str, Enum):
    COMPUTED = "computed"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


class ObservedDirection(str, Enum):
    INCREASING = "increasing"
    DECREASING = "decreasing"
    NON_MONOTONIC = "non_monotonic"
    WEAK_NO_RELATIONSHIP = "weak_no_relationship"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class EmpiricalThresholds:
    min_paired_observations: int = 30
    min_binary_class_observations: int = 10
    significance_level: float = 0.05
    min_abs_correlation: float = 0.05
    min_abs_standardized_logit_coefficient: float = 0.10
    min_abs_standardized_linear_coefficient: float = 0.05
    requested_bins: int = 5
    min_bin_observations: int = 10
    min_bin_reference_change: float = 0.002
    min_bin_reference_range: float = 0.01
    min_continuous_bin_reference_change_sd: float = 0.05
    min_continuous_bin_reference_range_sd: float = 0.10

    def __post_init__(self) -> None:
        if self.min_paired_observations < 3:
            raise ValueError("min_paired_observations must be at least 3")
        if self.min_binary_class_observations < 1:
            raise ValueError("min_binary_class_observations must be positive")
        if not 0 < self.significance_level < 1:
            raise ValueError("significance_level must be between 0 and 1")
        if self.min_abs_correlation < 0:
            raise ValueError("min_abs_correlation cannot be negative")
        if self.min_abs_standardized_logit_coefficient < 0:
            raise ValueError("min_abs_standardized_logit_coefficient cannot be negative")
        if self.min_abs_standardized_linear_coefficient < 0:
            raise ValueError("min_abs_standardized_linear_coefficient cannot be negative")
        if self.requested_bins < 3:
            raise ValueError("requested_bins must be at least 3")
        if self.min_bin_observations < 1:
            raise ValueError("min_bin_observations must be positive")


@dataclass(frozen=True)
class CorrelationEvidence:
    method: str
    status: EvidenceStatus
    coefficient: float | None
    p_value: float | None
    n_observations: int
    material_direction: str | None
    reason: str


@dataclass(frozen=True)
class RegressionEvidence:
    model: str
    status: EvidenceStatus
    coefficient: float | None
    intercept: float | None
    p_value: float | None
    n_observations: int
    material_direction: str | None
    feature_mean: float | None
    feature_std: float | None
    reference_mean: float | None
    reference_std: float | None
    reason: str


@dataclass(frozen=True)
class BinSummary:
    bin_number: int
    lower_bound: float
    upper_bound: float
    n_observations: int
    feature_mean: float
    reference_mean: float


@dataclass(frozen=True)
class BinnedEvidence:
    status: EvidenceStatus
    requested_bins: int
    realized_bins: int
    reference_range: float | None
    reference_range_standardized: float | None
    material_up_steps: int
    material_down_steps: int
    trend: str | None
    bins: tuple[BinSummary, ...]
    reason: str


@dataclass(frozen=True)
class EmpiricalDirectionalityResult:
    observed_direction: ObservedDirection
    status_reason: str
    n_input: int
    n_paired: int
    n_dropped: int
    n_special_value_dropped: int
    event_value: float | None
    class_counts: dict[str, int] | None
    pearson: CorrelationEvidence
    spearman: CorrelationEvidence
    regression: RegressionEvidence
    binned: BinnedEvidence
    thresholds: EmpiricalThresholds

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_empirical_directionality(
    feature: pd.Series,
    reference: pd.Series,
    *,
    reference_kind: ReferenceKind,
    thresholds: EmpiricalThresholds | None = None,
    feature_missing_values: Iterable[object] = (),
    reference_missing_values: Iterable[object] = (),
) -> EmpiricalDirectionalityResult:
    """Compute separate statistics and a controlled empirical direction state."""

    config = thresholds or EmpiricalThresholds()
    if not isinstance(reference_kind, ReferenceKind):
        raise ValueError("reference_kind must be a ReferenceKind")
    if len(feature) != len(reference):
        raise ValueError("feature and reference must have equal lengths")

    n_input = len(feature)
    if not pd.api.types.is_numeric_dtype(feature):
        return _empty_result(
            ObservedDirection.NOT_APPLICABLE,
            "Feature is not numeric; ordered numeric directionality is not applicable.",
            n_input,
            config,
        )

    numeric = pd.DataFrame(
        {
            "feature": pd.to_numeric(feature, errors="coerce"),
            "reference": pd.to_numeric(reference, errors="coerce"),
        }
    ).replace([np.inf, -np.inf], np.nan)
    feature_specials = _numeric_special_values(feature_missing_values)
    reference_specials = _numeric_special_values(reference_missing_values)
    special_mask = numeric["feature"].isin(feature_specials) | numeric["reference"].isin(
        reference_specials
    )
    n_special_value_dropped = int(special_mask.sum())
    paired = numeric.mask(special_mask).dropna()
    n_paired = len(paired)
    if n_paired < config.min_paired_observations:
        return _empty_result(
            ObservedDirection.INSUFFICIENT_EVIDENCE,
            f"Only {n_paired} paired observations; at least {config.min_paired_observations} required.",
            n_input,
            config,
            n_paired=n_paired,
            n_special_value_dropped=n_special_value_dropped,
        )
    if paired["feature"].nunique() < 2:
        return _empty_result(
            ObservedDirection.INSUFFICIENT_EVIDENCE,
            "Feature has fewer than two distinct paired values.",
            n_input,
            config,
            n_paired=n_paired,
            n_special_value_dropped=n_special_value_dropped,
        )

    event_value: float | None = None
    class_counts: dict[str, int] | None = None
    if reference_kind is ReferenceKind.BINARY:
        values = sorted(paired["reference"].unique().tolist())
        counts = paired["reference"].value_counts().sort_index()
        class_counts = {str(key): int(value) for key, value in counts.items()}
        if len(values) != 2:
            return _empty_result(
                ObservedDirection.INSUFFICIENT_EVIDENCE,
                f"Binary reference must have exactly two paired classes; found {len(values)}.",
                n_input,
                config,
                n_paired=n_paired,
                class_counts=class_counts,
                n_special_value_dropped=n_special_value_dropped,
            )
        if int(counts.min()) < config.min_binary_class_observations:
            return _empty_result(
                ObservedDirection.INSUFFICIENT_EVIDENCE,
                "The smaller binary class does not meet the minimum observation threshold.",
                n_input,
                config,
                n_paired=n_paired,
                class_counts=class_counts,
                n_special_value_dropped=n_special_value_dropped,
            )
        event_value = float(values[-1])
        paired["reference"] = (paired["reference"] == event_value).astype(float)
    elif paired["reference"].nunique() < 2:
        return _empty_result(
            ObservedDirection.INSUFFICIENT_EVIDENCE,
            "Reference has fewer than two distinct paired values.",
            n_input,
            config,
            n_paired=n_paired,
            n_special_value_dropped=n_special_value_dropped,
        )

    pearson = _correlation(paired, "pearson", config)
    spearman = _correlation(paired, "spearman", config)
    regression = _regression(paired, reference_kind, config)
    binned = _binned(paired, reference_kind, config)
    observed, reason = _synthesize(pearson, spearman, regression, binned)
    return EmpiricalDirectionalityResult(
        observed_direction=observed,
        status_reason=reason,
        n_input=n_input,
        n_paired=n_paired,
        n_dropped=n_input - n_paired,
        n_special_value_dropped=n_special_value_dropped,
        event_value=event_value,
        class_counts=class_counts,
        pearson=pearson,
        spearman=spearman,
        regression=regression,
        binned=binned,
        thresholds=config,
    )


def _direction(value: float) -> str:
    return "increasing" if value > 0 else "decreasing"


def _numeric_special_values(values: Iterable[object]) -> set[float]:
    converted: set[float] = set()
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(numeric):
            converted.add(numeric)
    return converted


def _correlation(
    paired: pd.DataFrame, method: str, config: EmpiricalThresholds
) -> CorrelationEvidence:
    function = stats.pearsonr if method == "pearson" else stats.spearmanr
    try:
        result = function(paired["feature"], paired["reference"])
        coefficient, p_value = float(result.statistic), float(result.pvalue)
    except Exception as exc:  # defensive audit boundary around third-party statistics
        return CorrelationEvidence(
            method, EvidenceStatus.FAILED, None, None, len(paired), None, str(exc)
        )
    material = (
        _direction(coefficient)
        if abs(coefficient) >= config.min_abs_correlation
        and p_value <= config.significance_level
        else None
    )
    reason = (
        "Clears practical and statistical thresholds."
        if material
        else "Does not clear both practical and statistical thresholds."
    )
    return CorrelationEvidence(
        method, EvidenceStatus.COMPUTED, coefficient, p_value, len(paired), material, reason
    )


def _regression(
    paired: pd.DataFrame,
    reference_kind: ReferenceKind,
    config: EmpiricalThresholds,
) -> RegressionEvidence:
    x_raw = paired["feature"].to_numpy(dtype=float)
    y = paired["reference"].to_numpy(dtype=float)
    feature_mean = float(x_raw.mean())
    feature_std = float(x_raw.std(ddof=0))
    reference_mean = float(y.mean())
    reference_std = float(y.std(ddof=0))
    x = ((x_raw - feature_mean) / feature_std).reshape(-1, 1)
    try:
        if reference_kind is ReferenceKind.BINARY:
            model_name = "univariate_logistic_standardized"
            model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=2000)
            with warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                model.fit(x, y)
            coefficient = float(model.coef_[0, 0])
            intercept = float(model.intercept_[0])
            probabilities = np.clip(model.predict_proba(x)[:, 1], 1e-12, 1 - 1e-12)
            fitted_ll = float(np.sum(y * np.log(probabilities) + (1 - y) * np.log(1 - probabilities)))
            prevalence = float(y.mean())
            null_ll = float(np.sum(y * np.log(prevalence) + (1 - y) * np.log(1 - prevalence)))
            p_value = float(stats.chi2.sf(max(0.0, 2 * (fitted_ll - null_ll)), df=1))
            practical = config.min_abs_standardized_logit_coefficient
        else:
            model_name = "univariate_linear_standardized"
            y_standardized = (y - y.mean()) / y.std(ddof=0)
            model = LinearRegression().fit(x, y_standardized)
            coefficient = float(model.coef_[0])
            intercept = float(model.intercept_)
            correlation = stats.pearsonr(x[:, 0], y)
            p_value = float(correlation.pvalue)
            practical = config.min_abs_standardized_linear_coefficient
    except Exception as exc:  # preserve failures as evidence, rather than aborting a batch
        return RegressionEvidence(
            "univariate_regression",
            EvidenceStatus.FAILED,
            None,
            None,
            None,
            len(paired),
            None,
            feature_mean,
            feature_std,
            reference_mean,
            reference_std,
            str(exc),
        )
    material = (
        _direction(coefficient)
        if abs(coefficient) >= practical and p_value <= config.significance_level
        else None
    )
    reason = (
        "Clears practical and statistical thresholds."
        if material
        else "Does not clear both practical and statistical thresholds."
    )
    return RegressionEvidence(
        model_name,
        EvidenceStatus.COMPUTED,
        coefficient,
        intercept,
        p_value,
        len(paired),
        material,
        feature_mean,
        feature_std,
        reference_mean,
        reference_std,
        reason,
    )


def _binned(
    paired: pd.DataFrame,
    reference_kind: ReferenceKind,
    config: EmpiricalThresholds,
) -> BinnedEvidence:
    try:
        labels = pd.qcut(paired["feature"], q=config.requested_bins, duplicates="drop")
        grouped = paired.assign(_bin=labels).groupby("_bin", observed=True, sort=True)
        summaries = tuple(
            BinSummary(
                bin_number=index,
                lower_bound=float(interval.left),
                upper_bound=float(interval.right),
                n_observations=int(len(group)),
                feature_mean=float(group["feature"].mean()),
                reference_mean=float(group["reference"].mean()),
            )
            for index, (interval, group) in enumerate(grouped, start=1)
        )
    except Exception as exc:
        return BinnedEvidence(
            EvidenceStatus.FAILED, config.requested_bins, 0, None, None, 0, 0, None, (), str(exc)
        )
    if len(summaries) < 3 or any(item.n_observations < config.min_bin_observations for item in summaries):
        return BinnedEvidence(
            EvidenceStatus.INSUFFICIENT,
            config.requested_bins,
            len(summaries),
            None,
            None,
            0,
            0,
            None,
            summaries,
            "Fewer than three usable bins or a bin is below the minimum size.",
        )
    means = np.asarray([item.reference_mean for item in summaries])
    differences = np.diff(means)
    reference_range = float(means.max() - means.min())
    reference_std = float(paired["reference"].std(ddof=0))
    reference_range_standardized = (
        reference_range / reference_std if reference_std > 0 else None
    )
    if reference_kind is ReferenceKind.BINARY:
        step_threshold = config.min_bin_reference_change
        range_is_material = reference_range >= config.min_bin_reference_range
    else:
        step_threshold = config.min_continuous_bin_reference_change_sd * reference_std
        range_is_material = (
            reference_range_standardized is not None
            and reference_range_standardized >= config.min_continuous_bin_reference_range_sd
        )
    up = int(np.sum(differences >= step_threshold))
    down = int(np.sum(differences <= -step_threshold))
    if not range_is_material:
        trend = None
        reason = "Across-bin reference range is below the materiality threshold."
    elif up and down:
        trend = "non_monotonic"
        reason = "Material upward and downward adjacent-bin movements are both present."
    elif up:
        trend = "increasing"
        reason = "Material adjacent-bin movement is upward only."
    elif down:
        trend = "decreasing"
        reason = "Material adjacent-bin movement is downward only."
    else:
        trend = None
        reason = "No adjacent-bin movement clears the materiality threshold."
    return BinnedEvidence(
        EvidenceStatus.COMPUTED,
        config.requested_bins,
        len(summaries),
        reference_range,
        reference_range_standardized,
        up,
        down,
        trend,
        summaries,
        reason,
    )


def _synthesize(
    pearson: CorrelationEvidence,
    spearman: CorrelationEvidence,
    regression: RegressionEvidence,
    binned: BinnedEvidence,
) -> tuple[ObservedDirection, str]:
    global_signs = {
        item.material_direction
        for item in (pearson, spearman, regression)
        if item.material_direction is not None
    }
    if len(global_signs) > 1:
        return ObservedDirection.CONFLICTING_EVIDENCE, "Material global statistics have opposing signs."
    if binned.trend == "non_monotonic":
        if global_signs:
            return (
                ObservedDirection.CONFLICTING_EVIDENCE,
                "A material global trend coexists with material two-way binned movement.",
            )
        return ObservedDirection.NON_MONOTONIC, "Binned evidence has material two-way movement."
    binned_sign = binned.trend if binned.trend in {"increasing", "decreasing"} else None
    if global_signs and binned_sign and binned_sign not in global_signs:
        return ObservedDirection.CONFLICTING_EVIDENCE, "Global and binned evidence have opposing signs."
    signs = set(global_signs)
    if binned_sign:
        signs.add(binned_sign)
    if signs == {"increasing"}:
        return ObservedDirection.INCREASING, "All material evidence points to increasing reference values."
    if signs == {"decreasing"}:
        return ObservedDirection.DECREASING, "All material evidence points to decreasing reference values."
    return (
        ObservedDirection.WEAK_NO_RELATIONSHIP,
        "No component clears the configured practical and statistical thresholds.",
    )


def _empty_result(
    direction: ObservedDirection,
    reason: str,
    n_input: int,
    config: EmpiricalThresholds,
    *,
    n_paired: int = 0,
    class_counts: dict[str, int] | None = None,
    n_special_value_dropped: int = 0,
) -> EmpiricalDirectionalityResult:
    correlation = lambda method: CorrelationEvidence(  # noqa: E731
        method, EvidenceStatus.NOT_APPLICABLE, None, None, n_paired, None, reason
    )
    return EmpiricalDirectionalityResult(
        direction,
        reason,
        n_input,
        n_paired,
        n_input - n_paired,
        n_special_value_dropped,
        None,
        class_counts,
        correlation("pearson"),
        correlation("spearman"),
        RegressionEvidence(
            "univariate_regression",
            EvidenceStatus.NOT_APPLICABLE,
            None,
            None,
            None,
            n_paired,
            None,
            None,
            None,
            None,
            None,
            reason,
        ),
        BinnedEvidence(
            EvidenceStatus.NOT_APPLICABLE,
            config.requested_bins,
            0,
            None,
            None,
            0,
            0,
            None,
            (),
            reason,
        ),
        config,
    )


__all__ = [
    "BinSummary",
    "BinnedEvidence",
    "CorrelationEvidence",
    "EmpiricalDirectionalityResult",
    "EmpiricalThresholds",
    "EvidenceStatus",
    "ObservedDirection",
    "ReferenceKind",
    "RegressionEvidence",
    "analyze_empirical_directionality",
]
