"""Deterministic empirical engine for T2-D11 directional consistency."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LinearRegression, LogisticRegression


class ObservedDirection(str, Enum):
    INCREASING = "INCREASING"
    DECREASING = "DECREASING"
    NON_MONOTONIC = "NON_MONOTONIC"
    WEAK_OR_NO_RELATIONSHIP = "WEAK_OR_NO_RELATIONSHIP"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceStrength(str, Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class DirectionalityThresholds:
    min_sample: int = 30
    min_binary_class: int = 10
    significance_level: float = 0.05
    corr_floor: float = 0.20
    regression_floor: float = 0.10
    bin_range_floor_sd: float = 0.10
    requested_bins: int = 5

    def __post_init__(self) -> None:
        if self.min_sample < 3 or self.min_binary_class < 1:
            raise ValueError("sample thresholds are invalid")
        if not 0 < self.significance_level < 1:
            raise ValueError("significance_level must be between zero and one")
        if min(self.corr_floor, self.regression_floor, self.bin_range_floor_sd) < 0:
            raise ValueError("materiality thresholds cannot be negative")
        if self.requested_bins < 3:
            raise ValueError("requested_bins must be at least three")


def _component(direction: str | None, *, value: float | None = None,
               p_value: float | None = None, reason: str = "") -> dict[str, Any]:
    return {"direction": direction or "FLAT", "value": value,
            "p_value": p_value, "reason": reason}


def _numeric_specials(values: Iterable[Any]) -> set[float]:
    result: set[float] = set()
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(number):
            result.add(number)
    return result


def _direction(value: float) -> str:
    return "INCREASING" if value > 0 else "DECREASING"


def _empty(direction: ObservedDirection, reason: str, n_input: int,
           n_paired: int, special_dropped: int,
           thresholds: DirectionalityThresholds) -> dict[str, Any]:
    return {
        "observed_direction": direction.value,
        "evidence_strength": EvidenceStrength.NOT_APPLICABLE.value,
        "status_reason": reason,
        "n_input": n_input, "n_paired": n_paired,
        "n_dropped": n_input - n_paired,
        "n_special_value_dropped": special_dropped,
        "pearson": _component(None, reason=reason),
        "spearman": _component(None, reason=reason),
        "regression": {**_component(None, reason=reason), "model": None,
                       "intercept": None, "curve": []},
        "binned": {**_component(None, reason=reason), "bins": [],
                   "shape": "UNAVAILABLE", "realized_bins": 0,
                   "range_standardized": None},
        "thresholds": asdict(thresholds), "chart_sample": [],
    }


def _correlation(frame: pd.DataFrame, method: str,
                 thresholds: DirectionalityThresholds) -> dict[str, Any]:
    function = stats.pearsonr if method == "pearson" else stats.spearmanr
    try:
        result = function(frame["feature"], frame["reference"])
        coefficient, p_value = float(result.statistic), float(result.pvalue)
    except Exception as exc:  # third-party defensive boundary
        return _component(None, reason=str(exc))
    material = abs(coefficient) >= thresholds.corr_floor and p_value <= thresholds.significance_level
    return _component(_direction(coefficient) if material else None,
                      value=coefficient, p_value=p_value,
                      reason=("Clears practical and statistical thresholds."
                              if material else "Below practical or statistical threshold."))


def _regression(frame: pd.DataFrame, binary: bool,
                thresholds: DirectionalityThresholds) -> dict[str, Any]:
    raw_x = frame["feature"].to_numpy(dtype=float)
    y = frame["reference"].to_numpy(dtype=float)
    mean, std = float(raw_x.mean()), float(raw_x.std(ddof=0))
    x = ((raw_x - mean) / std).reshape(-1, 1)
    try:
        if binary:
            model_name = "univariate_logistic_standardized"
            model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=2000)
            with warnings.catch_warnings():
                warnings.simplefilter("error", ConvergenceWarning)
                model.fit(x, y)
            coefficient, intercept = float(model.coef_[0, 0]), float(model.intercept_[0])
            probabilities = np.clip(model.predict_proba(x)[:, 1], 1e-12, 1 - 1e-12)
            fitted = float(np.sum(y * np.log(probabilities) + (1 - y) * np.log(1 - probabilities)))
            prevalence = float(y.mean())
            null = float(np.sum(y * np.log(prevalence) + (1 - y) * np.log(1 - prevalence)))
            p_value = float(stats.chi2.sf(max(0.0, 2 * (fitted - null)), 1))
            curve_x = np.linspace(float(raw_x.min()), float(raw_x.max()), 41)
            curve_y = model.predict_proba(((curve_x - mean) / std).reshape(-1, 1))[:, 1]
        else:
            model_name = "univariate_linear_standardized"
            y_mean, y_std = float(y.mean()), float(y.std(ddof=0))
            standardized_y = (y - y_mean) / y_std
            model = LinearRegression().fit(x, standardized_y)
            coefficient, intercept = float(model.coef_[0]), float(model.intercept_)
            p_value = float(stats.linregress(x[:, 0], standardized_y).pvalue)
            curve_x = np.asarray([float(raw_x.min()), float(raw_x.max())])
            standardized_curve = model.predict(((curve_x - mean) / std).reshape(-1, 1))
            curve_y = standardized_curve * y_std + y_mean
        material = abs(coefficient) >= thresholds.regression_floor and p_value <= thresholds.significance_level
        return {**_component(_direction(coefficient) if material else None,
                             value=coefficient, p_value=p_value,
                             reason=("Clears practical and statistical thresholds."
                                     if material else "Below practical or statistical threshold.")),
                "model": model_name, "intercept": intercept,
                "curve": [{"x": float(a), "y": float(b)} for a, b in zip(curve_x, curve_y)]}
    except Exception as exc:
        return {**_component(None, reason=str(exc)), "model": "univariate_regression",
                "intercept": None, "curve": []}


def _binned(frame: pd.DataFrame, thresholds: DirectionalityThresholds) -> dict[str, Any]:
    try:
        labels = pd.qcut(frame["feature"], q=thresholds.requested_bins, duplicates="drop")
        groups = frame.assign(_bin=labels).groupby("_bin", observed=True, sort=True)
        bins = [{"bin_number": index, "lower_bound": float(interval.left),
                 "upper_bound": float(interval.right), "n_observations": int(len(group)),
                 "feature_mean": float(group["feature"].mean()),
                 "reference_mean": float(group["reference"].mean())}
                for index, (interval, group) in enumerate(groups, start=1)]
    except Exception as exc:
        return {**_component(None, reason=str(exc)), "bins": [], "shape": "UNAVAILABLE",
                "realized_bins": 0, "range_standardized": None}
    if len(bins) < 3:
        return {**_component(None, reason="Fewer than three distinct quantile bins."),
                "bins": bins, "shape": "UNAVAILABLE", "realized_bins": len(bins),
                "range_standardized": None}
    means = np.asarray([row["reference_mean"] for row in bins], dtype=float)
    ref_std = float(frame["reference"].std(ddof=0))
    standardized_range = float(np.ptp(means) / ref_std) if ref_std > 0 else 0.0
    floor = thresholds.bin_range_floor_sd
    # Shape is deliberately broad: only an interior extreme separated materially
    # from both endpoints is called non-monotonic. Adjacent wiggles are descriptive.
    interior = means[1:-1]
    high_turn = float(interior.max() - max(means[0], means[-1])) if len(interior) else 0.0
    low_turn = float(min(means[0], means[-1]) - interior.min()) if len(interior) else 0.0
    material_turn = max(high_turn, low_turn) / ref_std if ref_std > 0 else 0.0
    if material_turn >= floor:
        return {**_component("NON_MONOTONIC", value=material_turn,
                             reason="A broad interior turning point clears the materiality floor."),
                "bins": bins, "shape": "NON_MONOTONIC", "realized_bins": len(bins),
                "range_standardized": standardized_range}
    slope = float(stats.linregress(np.arange(1, len(means) + 1), means).slope)
    endpoint_change = float((means[-1] - means[0]) / ref_std) if ref_std > 0 else 0.0
    material = abs(endpoint_change) >= floor
    return {**_component(_direction(slope) if material and slope != 0 else None,
                         value=endpoint_change,
                         reason=("Broad first-to-last movement clears the materiality floor."
                                 if material else "Binned relationship is essentially flat.")),
            "bins": bins, "shape": (_direction(slope) if material and slope != 0 else "FLAT"),
            "realized_bins": len(bins), "range_standardized": standardized_range}


def _consensus(spearman: dict[str, Any], regression: dict[str, Any],
               binned: dict[str, Any]) -> tuple[ObservedDirection, EvidenceStrength, str]:
    if binned["shape"] == "NON_MONOTONIC":
        return (ObservedDirection.NON_MONOTONIC, EvidenceStrength.NOT_APPLICABLE,
                "Binned averages show a material broad reversal.")
    votes = [spearman["direction"], regression["direction"], binned["direction"]]
    increasing, decreasing = votes.count("INCREASING"), votes.count("DECREASING")
    if increasing == 3 or decreasing == 3:
        direction = ObservedDirection.INCREASING if increasing == 3 else ObservedDirection.DECREASING
        return direction, EvidenceStrength.STRONG, "All three directional evidence sources agree."
    if increasing >= 2 or decreasing >= 2:
        direction = ObservedDirection.INCREASING if increasing >= 2 else ObservedDirection.DECREASING
        return direction, EvidenceStrength.MODERATE, "Two of three directional evidence sources agree."
    if increasing and decreasing:
        return (ObservedDirection.CONFLICTING_EVIDENCE, EvidenceStrength.NOT_APPLICABLE,
                "Material evidence sources point in opposing directions without a majority.")
    return (ObservedDirection.WEAK_OR_NO_RELATIONSHIP, EvidenceStrength.WEAK,
            "No direction is corroborated by at least two material evidence sources.")


def analyze_directionality(feature: pd.Series, reference: pd.Series, *,
                           target_type: str, positive_class: Any = None,
                           thresholds: DirectionalityThresholds | None = None,
                           feature_special_values: Iterable[Any] = (),
                           reference_special_values: Iterable[Any] = ()) -> dict[str, Any]:
    """Assess one numeric feature against one binary or continuous target."""
    config = thresholds or DirectionalityThresholds()
    n_input = len(feature)
    if len(reference) != n_input:
        raise ValueError("feature and reference must have equal lengths")
    feature_input = pd.Series(feature).reset_index(drop=True)
    reference_input = pd.Series(reference).reset_index(drop=True)
    if not pd.api.types.is_numeric_dtype(feature_input):
        return _empty(ObservedDirection.NOT_APPLICABLE, "Feature is not numeric.", n_input, 0, 0, config)
    feature_numeric = pd.to_numeric(feature_input, errors="coerce")
    raw_reference_numeric = pd.to_numeric(reference_input, errors="coerce")
    special_mask = (
        feature_numeric.isin(_numeric_specials(feature_special_values))
        | raw_reference_numeric.isin(_numeric_specials(reference_special_values))
    )
    binary = target_type == "binary"
    if binary:
        raw_reference = reference_input.astype("string")
        usable_labels = sorted(raw_reference[reference_input.notna() & ~special_mask].unique().tolist())
        selected_label = str(positive_class) if positive_class is not None else (
            usable_labels[-1] if usable_labels else "")
        reference_numeric = raw_reference.map(
            lambda value: np.nan if pd.isna(value) else float(str(value) == selected_label))
    else:
        reference_numeric = raw_reference_numeric
    numeric = pd.DataFrame({"feature": feature_numeric,
                            "reference": reference_numeric}).replace([np.inf, -np.inf], np.nan)
    special_dropped = int(special_mask.sum())
    frame = numeric.mask(special_mask).dropna()
    if len(frame) < config.min_sample:
        return _empty(ObservedDirection.INSUFFICIENT_DATA,
                      f"Only {len(frame)} usable observations; {config.min_sample} required.",
                      n_input, len(frame), special_dropped, config)
    if frame["feature"].nunique() < 2:
        return _empty(ObservedDirection.INSUFFICIENT_DATA, "Feature is constant after cleaning.",
                      n_input, len(frame), special_dropped, config)
    if target_type not in {"binary", "continuous"}:
        return _empty(ObservedDirection.NOT_APPLICABLE,
                      f"Target type {target_type!r} is not supported.", n_input, len(frame), special_dropped, config)
    event_value = None
    if binary:
        values = frame["reference"].dropna().unique().tolist()
        if len(values) != 2 or len(usable_labels) != 2:
            return _empty(ObservedDirection.INSUFFICIENT_DATA,
                          "Binary target does not contain exactly two usable classes.",
                          n_input, len(frame), special_dropped, config)
        if selected_label not in usable_labels:
            return _empty(ObservedDirection.INSUFFICIENT_DATA,
                          "Configured positive class is not present in the usable target.",
                          n_input, len(frame), special_dropped, config)
        counts = frame["reference"].value_counts()
        if len(counts) != 2 or int(counts.min()) < config.min_binary_class:
            return _empty(ObservedDirection.INSUFFICIENT_DATA,
                          "The smaller binary class is below the minimum sample.",
                          n_input, len(frame), special_dropped, config)
        event_value = selected_label
    elif frame["reference"].nunique() < 2:
        return _empty(ObservedDirection.INSUFFICIENT_DATA, "Continuous target is constant.",
                      n_input, len(frame), special_dropped, config)
    pearson = _correlation(frame, "pearson", config)
    spearman = _correlation(frame, "spearman", config)
    regression = _regression(frame, binary, config)
    binned = _binned(frame, config)
    observed, strength, reason = _consensus(spearman, regression, binned)
    sample = frame.iloc[np.linspace(0, len(frame) - 1, min(400, len(frame)), dtype=int)]
    return {"observed_direction": observed.value, "evidence_strength": strength.value,
            "status_reason": reason, "n_input": n_input, "n_paired": len(frame),
            "n_dropped": n_input - len(frame), "n_special_value_dropped": special_dropped,
            "event_value": event_value, "pearson": pearson, "spearman": spearman,
            "regression": regression, "binned": binned, "thresholds": asdict(config),
            "chart_sample": [{"x": float(x), "y": float(y)} for x, y in zip(sample["feature"], sample["reference"])]}


def expected_reference_direction(expected_risk_direction: str, reference_orientation: str) -> str:
    if expected_risk_direction not in {"INCREASING", "DECREASING", "NON_MONOTONIC", "NO_CLEAR_DIRECTION", "NOT_APPLICABLE"}:
        raise ValueError("unsupported expected direction")
    if reference_orientation == "HIGHER_IS_BETTER":
        return {"INCREASING": "DECREASING", "DECREASING": "INCREASING"}.get(
            expected_risk_direction, expected_risk_direction)
    if reference_orientation != "HIGHER_IS_WORSE":
        raise ValueError("unsupported reference orientation")
    return expected_risk_direction


def compare_expected_observed(expected_risk_direction: str, reference_orientation: str,
                              observed_direction: str) -> str:
    expected = expected_reference_direction(expected_risk_direction, reference_orientation)
    if expected == "NOT_APPLICABLE" or observed_direction == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    if expected == "NO_CLEAR_DIRECTION":
        return "NO_ECONOMIC_PRIOR"
    if observed_direction == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA"
    if observed_direction == "CONFLICTING_EVIDENCE":
        return "CONFLICTING_EVIDENCE"
    if observed_direction == "WEAK_OR_NO_RELATIONSHIP":
        return "WEAK_OR_NO_RELATIONSHIP"
    return "AGREEMENT" if expected == observed_direction else "REVIEW_RECOMMENDED"


__all__ = ["DirectionalityThresholds", "EvidenceStrength", "ObservedDirection",
           "analyze_directionality", "compare_expected_observed", "expected_reference_direction"]
