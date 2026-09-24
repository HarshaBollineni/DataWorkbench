from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from domains.rca.driver_search import run_driver_search, validate_target_spec


def _population_context() -> dict:
    return {"definition": {
        "method": "split_snapshot", "split_feature": "year",
        "expression": {"operator": "in", "values": ["2011"]},
        "null_policy": "baseline", "special_values": [], "special_policy": "exclude",
    }}


def test_population_driver_search_excludes_leakage_and_finds_validated_separator():
    rows = 400
    frame = pd.DataFrame({
        "year": [2011] * 200 + [2015] * 200,
        "debt": np.linspace(100, 500, rows),
        "origination_band": (["older"] * 180 + ["recent"] * 20
                             + ["older"] * 20 + ["recent"] * 180),
        "noise": np.tile([0, 1, 2, 3], 100),
        "account_id": [f"acct-{index}" for index in range(rows)],
    })
    proposed = {
        "problem_statement": "Which retained inputs best separate baseline and current?",
        "target_mode": "population_membership", "affected_column": "debt",
        "positive_class_definition": "current population",
        "candidate_columns": ["year", "debt", "origination_band", "noise", "account_id"],
        "excluded_columns": [], "rationale": "Find contextual population drivers.",
    }

    validated = validate_target_spec(frame, proposed, _population_context())
    result = run_driver_search(frame, {
        "target_spec": validated, "population_context": _population_context(),
        "declared_special_values": [],
    })

    assert validated["candidate_columns"] == ["origination_band", "noise"]
    assert set(validated["rejected_columns"]) == {"year", "debt", "account_id"}
    assert result["metrics"]["top_feature"] == "origination_band"
    assert result["metrics"]["validation_auc"] > 0.8
    assert result["evidence_rows"]
    assert "not a proven root cause" in result["interpretation_hints"][0]


def test_missingness_target_requires_two_supported_classes():
    frame = pd.DataFrame({"amount": [None] + [1.0] * 49, "segment": ["A"] * 50})
    spec = {
        "problem_statement": "What separates missing amount rows?",
        "target_mode": "missingness", "affected_column": "amount",
        "positive_class_definition": "amount is missing", "candidate_columns": ["segment"],
        "excluded_columns": [], "rationale": "Find missingness drivers.",
    }

    with pytest.raises(ValueError, match="at least 20 rows each"):
        run_driver_search(frame, {"target_spec": spec})


def test_psi_missing_target_keeps_temporal_inputs_and_explains_bin_movement():
    rows = 400
    frame = pd.DataFrame({
        "year": [2011] * 200 + [2015] * 200,
        "reporting_quarter": ["2011-Q4"] * 200 + ["2015-Q1"] * 200,
        "segment": (["A"] * 100 + ["B"] * 100) * 2,
        "debt": ([None] * 10 + list(range(190))
                 + [None] * 60 + list(range(140))),
    })
    frame.loc[[20, 270], "debt"] = -999
    diagnostic = {
        "kind": "population_stability_index", "feature": "debt", "psi": 0.5,
        "bins": [
            {"bin": "missing", "bin_label": "Missing", "baseline_count": 10,
             "baseline_proportion": 0.05, "current_count": 60,
             "current_proportion": 0.30, "contribution": 0.40},
            {"bin": "bin_1", "bin_label": "Regular", "baseline_count": 190,
             "baseline_proportion": 0.95, "current_count": 140,
             "current_proportion": 0.70, "contribution": 0.10},
        ],
    }
    spec = {
        "problem_statement": "What explains movement in the Missing PSI bin?",
        "target_mode": "missingness", "affected_column": "debt",
        "positive_class_definition": "debt is missing", "candidate_columns": ["segment"],
        # Model-proposed exclusions are advisory and must not silently narrow evidence.
        "excluded_columns": ["year", "reporting_quarter"],
        "rationale": "Explain the dominant PSI symptom.",
    }

    validated = validate_target_spec(frame, spec, _population_context(), diagnostic)
    result = run_driver_search(frame, {
        "target_spec": validated, "population_context": _population_context(),
        "diagnostic": diagnostic, "declared_special_values": [-999],
    })

    assert "year" in validated["candidate_columns"]
    assert "reporting_quarter" in validated["candidate_columns"]
    assert validated["excluded_columns"] == ["debt"]
    assert set(validated["llm_proposed_exclusions"]) == {"year", "reporting_quarter"}
    assert result["psi_impact"]["baseline_symptom_rate"] == pytest.approx(0.05)
    assert result["psi_impact"]["current_symptom_rate"] == pytest.approx(0.30)
    assert result["psi_impact"]["target_bin_share_of_absolute_psi"] == pytest.approx(0.8)
    assert {row["population"] for row in result["evidence_rows"]} == {"baseline", "current"}
    assert "contributed 80.0% of absolute PSI" in result["summary"]


def test_numeric_feature_states_are_explicit_and_regular_thresholds_exclude_non_regular_rows():
    rows = 400
    noi = list(np.linspace(1000, 5000, 360)) + [None] * 20 + [-999] * 20
    frame = pd.DataFrame({
        "affected": [None] * 200 + list(range(200)),
        "NOI": noi,
        "segment": ["A", "B"] * 200,
    })
    snapshot = {"schema_version": 1, "columns": {
        "NOI": {
            "special_values_confirmed": True,
            "confirmed_special_values": [-999],
            "proposed_special_values": [],
        }
    }}
    spec = {
        "problem_statement": "What separates missing affected rows?",
        "target_mode": "missingness", "affected_column": "affected",
        "positive_class_definition": "affected is missing",
        "candidate_columns": ["NOI", "segment"], "excluded_columns": [],
        "rationale": "Find governed feature segments.",
    }

    result = run_driver_search(frame, {
        "target_spec": spec, "feature_state_snapshot": snapshot,
    })

    reconciliation = result["feature_state_reconciliation"]["NOI"]
    assert reconciliation == {
        **reconciliation,
        "total_rows": 400, "regular_rows": 360, "physical_missing_rows": 20,
        "special_rows": {"-999": 20}, "accounted_rows": 400, "reconciles": True,
    }
    labels = {row["segment"] for row in result["feature_state_segments"]}
    assert "NOI physical missing" in labels
    assert "NOI special: -999" in labels
    assert any(label.startswith("NOI regular <=") for label in labels)
    assert any(label.startswith("NOI regular >") for label in labels)
    assert all("NOI <=" not in label for label in labels)


def test_unconfirmed_proposed_special_value_remains_regular_and_is_disclosed():
    frame = pd.DataFrame({
        "affected": [None] * 100 + list(range(100)),
        "NOI": [-999] * 25 + list(np.linspace(1000, 5000, 175)),
    })
    snapshot = {"schema_version": 1, "columns": {
        "NOI": {
            "special_values_confirmed": False,
            "confirmed_special_values": [],
            "proposed_special_values": [-999],
        }
    }}
    result = run_driver_search(frame, {
        "target_spec": {
            "problem_statement": "What separates missing affected rows?",
            "target_mode": "missingness", "affected_column": "affected",
            "positive_class_definition": "affected is missing",
            "candidate_columns": ["NOI"], "excluded_columns": [],
            "rationale": "Test governance.",
        },
        "feature_state_snapshot": snapshot,
    })

    state = result["feature_state_reconciliation"]["NOI"]
    assert state["governance_status"] == "proposed_unconfirmed"
    assert state["proposed_special_values"] == [-999]
    assert state["regular_rows"] == 200
    assert state["special_rows"] == {}
