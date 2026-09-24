"""Bounded, diagnostic-aware evidence for the RCA Initial Review.

The immutable case-context artifact is the authority for intake evidence. A
raw snapshot profile is accepted only as a compatibility fallback for older
or non-diagnostic issues that have no governed column-profile artifact.
"""
from __future__ import annotations
from domains.rca import progress

from typing import Any, Callable

from analysis_runtime.contracts import stable_fingerprint


_PROFILE_FIELDS = (
    "total_count", "physical_null_count", "physical_null_share",
    "effective_missing_count", "effective_missing_share",
    "special_value_row_count", "special_value_share", "regular_value_count",
    "regular_value_share", "distinct_count", "mean", "median", "stddev",
    "min", "max", "q1", "q3", "negative_count", "negative_share",
    "profile_basis", "calculation_method", "classification", "data_type",
    "description", "special_values_confirmed", "declared_special_values",
    "normalized_special_values", "special_value_counts",
)


def _bounded_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {key: profile[key] for key in _PROFILE_FIELDS if profile.get(key) is not None}


def _population_scope(profile: dict[str, Any], psi: dict[str, Any] | None) -> str:
    if psi:
        total = profile.get("total_count")
        baseline = psi.get("baseline_count")
        current = psi.get("current_count")
        if all(isinstance(value, (int, float)) for value in (total, baseline, current)):
            if int(total) == int(baseline) + int(current):
                return "combined_baseline_and_current_source_snapshot"
        return "source_snapshot_not_population_specific"
    return "retained_source_snapshot"


def _psi_evidence(payload: dict[str, Any]) -> dict[str, Any] | None:
    bins = payload.get("bins")
    if payload.get("psi") is None or not isinstance(bins, list):
        return None
    bounded_bins = [{
        key: row.get(key) for key in (
            "bin", "bin_label", "baseline_count", "baseline_proportion",
            "current_count", "current_proportion", "contribution",
        )
    } for row in bins if isinstance(row, dict)]
    dominant = max(
        bounded_bins,
        key=lambda row: abs(float(row.get("contribution") or 0.0)),
        default=None,
    )
    total_contribution = sum(abs(float(row.get("contribution") or 0.0))
                             for row in bounded_bins)
    dominant_driver = None
    if dominant:
        dominant_driver = {
            **dominant,
            "absolute_contribution_share": (
                abs(float(dominant.get("contribution") or 0.0)) / total_contribution
                if total_contribution else None
            ),
        }
    return {
        "kind": "population_stability_index",
        "feature": payload.get("feature"),
        "psi": payload.get("psi"),
        "classification": payload.get("classification"),
        "thresholds": payload.get("thresholds") or {},
        "baseline_count": payload.get("baseline_count"),
        "current_count": payload.get("current_count"),
        "methodology": payload.get("methodology"),
        "epsilon": payload.get("epsilon"),
        "bins": bounded_bins,
        "dominant_driver": dominant_driver,
    }


@progress.phase("Reviewing retained evidence")
def build_opening_evidence(
    case_context: dict[str, Any],
    raw_profile: dict[str, Any],
    *,
    source_artifact_ids: tuple[str, ...] = (),
    artifact_loader: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Create the static/LLM input from the frozen intake evidence."""
    issue = case_context.get("issue") or {}
    source = issue.get("source_evidence") or {}
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    profile = source.get("data_profile") if isinstance(source.get("data_profile"), dict) else {}
    if not profile and isinstance(metrics.get("data_profile"), dict):
        profile = metrics["data_profile"]

    psi_payload = metrics
    psi_artifact_id = metrics.get("artifact_id")
    if artifact_loader and isinstance(psi_artifact_id, str):
        loaded = artifact_loader(psi_artifact_id)
        if isinstance(loaded, dict) and loaded.get("psi") is not None:
            psi_payload = loaded
            if not profile and isinstance(loaded.get("data_profile"), dict):
                profile = loaded["data_profile"]
    psi = _psi_evidence(psi_payload)

    result = dict(raw_profile)
    result["evidence_authority"] = "governed_aar" if (profile or psi) else "raw_snapshot_fallback"
    if profile:
        bounded_profile = _bounded_profile(profile)
        result.update({
            "found": True,
            "column": (psi.get("feature") if psi else None) or result.get("column"),
            "null_share": profile.get("physical_null_share", profile.get("null_share")),
            "distinct": profile.get("distinct_count"),
            "mean": profile.get("mean"),
            "profile": bounded_profile,
            "profile_population_scope": _population_scope(profile, psi),
        })
    if psi:
        result["diagnostic"] = psi

    refs = tuple(dict.fromkeys(value for value in source_artifact_ids if value))
    result["source_artifact_ids"] = list(refs)
    result["evidence_bundle_fingerprint"] = stable_fingerprint(result)
    return result


__all__ = ["build_opening_evidence"]
