"""Compose existing ROC/AUC, leakage, and IV engines without redefining metrics."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from domains.test_lab.shared.binning import (
    BinningConstraints,
    BinningFeatureSpec,
    BinningTargetSpec,
    fit_dataset_binning,
)
from .roc_gini import (
    AnalysisConstraints,
    FeatureSpec,
    TargetSpec,
    analyze_features,
)

from .models import (
    FeatureTargetSeparationResponse,
    SeparationCategory,
    SeparationFeatureResult,
    SeparationThresholds,
)

ProgressCallback = Callable[[int, int, str, str], None]
FeaturePreviewCallback = Callable[[dict[str, Any]], None]


def classify_feature(
    auc: float | None,
    iv: float | None,
    thresholds: SeparationThresholds | None = None,
) -> SeparationCategory:
    """Apply Suspicious > Strong > Medium > Weak precedence exactly once."""
    rules = thresholds or SeparationThresholds()
    if (auc is not None and auc >= rules.suspicious_auc) or (
        iv is not None and iv >= rules.suspicious_iv
    ):
        return "suspicious"
    if (auc is not None and auc >= rules.strong_auc) or (
        iv is not None and iv >= rules.strong_iv
    ):
        return "strong"
    if (auc is not None and auc >= rules.medium_auc) or (
        iv is not None and iv >= rules.medium_iv
    ):
        return "medium"
    return "weak"


def assess_feature_target_separation(
    data: pd.DataFrame,
    target: TargetSpec,
    roc_features: list[FeatureSpec],
    iv_features: list[BinningFeatureSpec],
    *,
    missing_target_action: str = "prompt",
    percentile_bins: int = 10,
    analysis_constraints: AnalysisConstraints | None = None,
    binning_constraints: BinningConstraints | None = None,
    separation_thresholds: SeparationThresholds | None = None,
    feature_profiles: dict[str, dict[str, Any]] | None = None,
    progress_callback: ProgressCallback | None = None,
    cached_roc_results: dict[str, dict[str, dict[str, Any]]] | None = None,
    roc_result_callback: Callable[[TargetSpec, FeatureSpec, dict[str, Any]], None]
    | None = None,
    cached_binning_results: dict[str, Any] | None = None,
    cached_binning_result_provider: Callable[
        [BinningTargetSpec], dict[str, Any]
    ] | None = None,
    binning_result_callback: Callable[[BinningFeatureSpec, Any], None] | None = None,
    feature_preview_callback: FeaturePreviewCallback | None = None,
) -> FeatureTargetSeparationResponse:
    """Run the established engines, align their results, then classify features."""
    if not roc_features or not iv_features:
        raise ValueError("Select at least one independent variable.")
    if [item.column for item in roc_features] != [item.column for item in iv_features]:
        raise ValueError("ROC and IV feature selections must have the same ordered columns.")
    if target.column in {item.column for item in roc_features}:
        raise ValueError("The target cannot also be selected as an independent variable.")

    rules = separation_thresholds or SeparationThresholds()
    total = len(roc_features) * 2

    def report_roc(completed: int, _count: int, feature: str) -> None:
        if progress_callback:
            progress_callback(completed, total, feature, "roc")

    analytics = analyze_features(
        data,
        [target],
        roc_features,
        missing_target_action=missing_target_action,
        percentile_bins=percentile_bins,
        analysis_constraints=analysis_constraints,
        feature_profiles=feature_profiles,
        progress_callback=report_roc,
        cached_feature_results=cached_roc_results,
        feature_result_callback=roc_result_callback,
    )
    target_result = analytics.targets[0]
    if target_result.status != "ready":
        return FeatureTargetSeparationResponse(
            status="action_required" if target_result.status == "action_required" else "invalid",
            target=target_result,
            localized_leakage_scan=target_result.localized_leakage_scan,
            classification_thresholds=rules,
            methodology={
                "composition": "ROC/AUC target validation completed before IV binning",
                "roc": analytics.methodology,
                "classification_precedence": ["suspicious", "strong", "medium", "weak"],
            },
        )

    inferred_target_type = target_result.target_type
    assert inferred_target_type is not None
    working = data.loc[data[target.column].notna()].copy()
    effective_positive_class = target.positive_class
    if inferred_target_type == "binary" and effective_positive_class is None:
        effective_positive_class = next(
            (
                label
                for label, encoded in target_result.target_mapping.items()
                if encoded == 1
            ),
            None,
        )
    binning_target = BinningTargetSpec(
        column=target.column,
        target_type=inferred_target_type,
        positive_class=effective_positive_class,
    )
    if cached_binning_result_provider:
        resolved_cached = cached_binning_result_provider(binning_target)
        cached_binning_results = {
            **resolved_cached,
            **(cached_binning_results or {}),
        }

    def report_iv(completed: int, _count: int, feature: str) -> None:
        if progress_callback:
            progress_callback(len(roc_features) + completed, total, feature, "iv")

    roc_by_feature = {item.feature: item for item in target_result.features}

    def report_feature(
        feature: BinningFeatureSpec,
        result: Any | None,
        error: str | None,
    ) -> None:
        if not feature_preview_callback:
            return
        roc_result = roc_by_feature.get(feature.column)
        iv_value = _iv_value(result) if result else None
        auc_value = roc_result.primary_auc if roc_result else None
        feature_preview_callback({
            "feature": feature.column,
            "stage": "iv",
            "status": "failed" if error else "completed",
            "feature_type": result.feature_type if result else feature.feature_type,
            "auc": auc_value,
            "gini": roc_result.primary_gini if roc_result else None,
            "iv": iv_value,
            "category": classify_feature(auc_value, iv_value, rules),
            "fine_bin_count": len(result.fine_bins) if result else None,
            "coarse_bin_count": len(result.coarse_bins) if result else None,
            "optimizer_status": (result.binning_profile or {}).get("solver_status") if result else None,
            "warnings": list(result.warnings) if result else [],
            "error": error,
        })

    binning = fit_dataset_binning(
        working,
        binning_target,
        iv_features,
        constraints=binning_constraints,
        feature_profiles=feature_profiles,
        progress_callback=report_iv,
        cached_results=cached_binning_results,
        result_callback=binning_result_callback,
        completion_callback=report_feature,
    )
    iv_by_feature = {item.feature: item for item in binning.results}
    failure_by_feature = {item.feature: item.error for item in binning.failures}
    leakage_features = {
        item.feature for item in (target_result.localized_leakage_scan.candidates
                                  if target_result.localized_leakage_scan else [])
    }
    results = []
    for requested in roc_features:
        roc_result = roc_by_feature.get(requested.column)
        iv_result = iv_by_feature.get(requested.column)
        iv_value = _iv_value(iv_result)
        auc_value = roc_result.primary_auc if roc_result else None
        results.append(
            SeparationFeatureResult(
                feature=requested.column,
                auc=auc_value,
                iv=iv_value,
                category=classify_feature(auc_value, iv_value, rules),
                leakage_status=(
                    "candidate" if requested.column in leakage_features
                    else "clear" if target_result.localized_leakage_scan else "not_available"
                ),
                roc=roc_result,
                binning=iv_result,
                errors=[failure_by_feature[requested.column]]
                if requested.column in failure_by_feature else [],
            )
        )
    results.sort(key=lambda item: (item.auc is None, -(item.auc or 0), item.feature))
    return FeatureTargetSeparationResponse(
        status="partial" if binning.failures else "complete",
        target=target_result,
        localized_leakage_scan=target_result.localized_leakage_scan,
        results=results,
        binning_failures=binning.failures,
        classification_thresholds=rules,
        methodology={
            "composition": "existing ROC/AUC and supervised IV results joined by feature name",
            "roc": analytics.methodology,
            "iv": binning.methodology,
            "classification_precedence": ["suspicious", "strong", "medium", "weak"],
            "continuous_target_iv": (
                "unavailable; existing between-bin variance ratio is not relabelled as IV"
            ),
        },
    )


def _iv_value(result: Any | None) -> float | None:
    if result is None:
        return None
    preferred = {"information_value", "maximum_one_vs_rest_iv"}
    metric = next((item for item in result.coarse_metrics if item.name in preferred), None)
    return metric.value if metric else None
