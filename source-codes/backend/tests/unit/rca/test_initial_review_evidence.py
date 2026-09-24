from __future__ import annotations

import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[3]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from domains.rca.initial_review_evidence import build_opening_evidence  # noqa: E402


def test_psi_opening_evidence_uses_governed_profile_and_bin_contributions():
    psi = {
        "artifact_id": "art_psi",
        "feature": "debt_yield",
        "psi": 2.2918188252948624,
        "classification": "investigate",
        "thresholds": {"watch": 0.1, "investigate": 0.25},
        "baseline_count": 19452,
        "current_count": 10800,
        "methodology": "frozen_bins_psi_v1",
        "epsilon": 1e-6,
        "bins": [
            {"bin": "missing", "bin_label": "Missing", "baseline_count": 3598,
             "baseline_proportion": 0.18496812667077936, "current_count": 0,
             "current_proportion": 0.0, "contribution": 2.2432699924509776},
            {"bin": "bin_1", "bin_label": "(-inf, 6.265]", "baseline_count": 1977,
             "baseline_proportion": 0.10163479333744602, "current_count": 1756,
             "current_proportion": 0.1625925925925926,
             "contribution": 0.028641735761468144},
            {"bin": "bin_2", "bin_label": "(6.265, 7.295]", "baseline_count": 2136,
             "baseline_proportion": 0.10980876002467613, "current_count": 1413,
             "current_proportion": 0.13083333333333333,
             "contribution": 0.003683167610386321},
            {"bin": "bin_3", "bin_label": "(7.295, +inf]", "baseline_count": 11741,
             "baseline_proportion": 0.6035883199670985, "current_count": 7631,
             "current_proportion": 0.706574074074074,
             "contribution": 0.0162239294720304},
        ],
    }
    profile = {
        "total_count": 30252,
        "physical_null_count": 3598,
        "physical_null_share": 0.1189,
        "distinct_count": 771,
        "mean": 8.522427780452576,
        "profile_basis": "confirmed_regular_values",
        "special_values_confirmed": True,
        "declared_special_values": ["-999"],
        "special_value_counts": {"-999": 1730},
        "special_value_row_count": 1730,
        "regular_value_count": 24924,
    }
    context = {"issue": {"source_evidence": {
        "metrics": {**psi, "data_profile": profile},
        "data_profile": profile,
    }}}

    result = build_opening_evidence(
        context,
        {"column": "debt_yield", "found": True, "null_share": 0.1189,
         "distinct": 772, "mean": -56.8717},
        source_artifact_ids=("art_context", "art_psi", "art_profile", "art_bins"),
        artifact_loader=lambda artifact_id: psi if artifact_id == "art_psi" else None,
    )

    assert result["evidence_authority"] == "governed_aar"
    assert result["mean"] == 8.522427780452576
    assert result["distinct"] == 771
    assert result["profile"]["special_values_confirmed"] is True
    assert result["profile"]["special_value_counts"] == {"-999": 1730}
    assert result["profile_population_scope"] == "combined_baseline_and_current_source_snapshot"
    assert result["diagnostic"]["baseline_count"] == 19452
    assert result["diagnostic"]["current_count"] == 10800
    assert result["diagnostic"]["dominant_driver"]["bin"] == "missing"
    assert result["diagnostic"]["dominant_driver"]["current_count"] == 0
    assert result["diagnostic"]["dominant_driver"]["absolute_contribution_share"] > 0.97
    assert result["source_artifact_ids"] == [
        "art_context", "art_psi", "art_profile", "art_bins",
    ]
    assert len(result["evidence_bundle_fingerprint"]) == 64


def test_raw_profile_remains_the_fallback_without_governed_evidence():
    raw = {"column": "amount", "found": True, "null_share": 0.4,
           "distinct": 12, "mean": 10.5}
    result = build_opening_evidence({"issue": {}}, raw)

    assert result["evidence_authority"] == "raw_snapshot_fallback"
    assert result["mean"] == 10.5
    assert "diagnostic" not in result
