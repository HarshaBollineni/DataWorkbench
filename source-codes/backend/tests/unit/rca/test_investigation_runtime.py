from __future__ import annotations

import pandas as pd

from domains.rca import analysis_helpers, investigation_runtime


def test_generated_confirmation_accepts_object_dtype_for_psi_bins():
    frame = pd.DataFrame({"debt": [None, 100.0, 300.0]})
    output = investigation_runtime.run_generated_code(
        "bin_ids = ['missing', 'bin_1', 'bin_2']\n"
        "regular_bins = pd.cut(df['debt'], bins=[-np.inf, 200.0, np.inf], "
        "labels=bin_ids[1:], right=True).astype(object)\n"
        "bins = np.where(df['debt'].isna(), 'missing', regular_bins)\n"
        "result = {'summary': 'PSI populations reconciled.', 'evidence_rows': "
        "[{'bin': value, 'rows': int((bins == value).sum())} for value in bin_ids]}",
        frame, {},
    )
    assert output["ok"], output
    assert output["timings"]["process_seconds"] >= output["timings"]["analysis_seconds"] >= 0
    assert output["timings"]["process_overhead_seconds"] >= 0
    assert [row["bin"] for row in output["result"]["evidence_rows"]] == [
        "missing", "bin_1", "bin_2",
    ]
    assert sum(row["rows"] for row in output["result"]["evidence_rows"]) == len(frame)


def test_object_dtype_support_does_not_allow_reflection():
    from ai import code_sandbox

    for code in ("result = object.__subclasses__()", "result = object.__mro__",
                 "result = getattr(object, '__subclasses__')()"):
        output = code_sandbox.run(code, pd.DataFrame())
        assert not output["ok"]
        assert "not permitted" in output["error"]


def _diagnostic():
    return {
        "feature": "debt_yield", "psi": 2.2918, "classification": "investigate",
        "baseline_count": 19452, "current_count": 10800,
        "bins": [
            {"bin": "missing", "bin_label": "Missing", "baseline_count": 3598,
             "baseline_proportion": 0.185, "current_count": 0,
             "current_proportion": 0.0, "contribution": 2.2433},
            {"bin": "bin_1", "bin_label": "(-inf, 6.27]", "baseline_count": 1977,
             "baseline_proportion": 0.1016, "current_count": 1756,
             "current_proportion": 0.1626, "contribution": 0.029},
        ],
    }


def test_retained_psi_helper_uses_authoritative_bins():
    output = analysis_helpers.run_helper(
        "psi_evidence_decomposition", pd.DataFrame(), {"diagnostic": _diagnostic()}
    )["result"]
    assert output["metrics"]["dominant_bin"] == "Missing"
    assert output["metrics"]["dominant_contribution_share"] > 0.98
    assert output["evidence_rows"][0]["baseline_count"] == 3598
    assert output["evidence_rows"][0]["current_count"] == 0


def test_helper_execution_artifact_freezes_source_and_identity():
    artifact = investigation_runtime.helper_execution_artifact(
        "psi_evidence_decomposition", {"column": "debt_yield"}
    )
    assert artifact["mode"] == "approved_helper"
    assert artifact["helper_id"] == "psi_evidence_decomposition"
    assert "def _psi_evidence_decomposition" in artifact["implementation_source"]
    assert len(artifact["implementation_sha256"]) == 64
    assert artifact["parameters"] == {"column": "debt_yield"}


def test_library_search_prefers_compatible_requested_helper():
    catalog = investigation_runtime.helper_catalog("psi", "Data", ["debt_yield"])
    result = investigation_runtime.search_helpers(
        {"preferred_helper_ids": ["psi_evidence_decomposition"], "helper_params": {}},
        catalog, has_retained_psi=True, available_columns={"debt_yield"},
    )
    assert result["decision"] == "reuse_existing_helper"
    assert result["selected_helper_id"] == "psi_evidence_decomposition"


def test_library_search_requires_codegen_when_no_requested_helper_fits():
    catalog = investigation_runtime.helper_catalog("psi", "Data", ["debt_yield"])
    result = investigation_runtime.search_helpers(
        {"preferred_helper_ids": ["not_in_catalog"], "helper_params": {}},
        catalog, has_retained_psi=True, available_columns={"debt_yield"},
    )
    assert result["decision"] == "generate_fresh_code"
    assert result["selected_helper_id"] is None


def test_population_segment_helper_requires_frozen_population_context():
    catalog = investigation_runtime.helper_catalog(
        "psi", "Data", ["debt_yield", "reporting_quarter"]
    )
    plan = {
        "preferred_helper_ids": ["population_segment_missingness"],
        "helper_params": {"column": "debt_yield", "segment_col": "reporting_quarter"},
    }
    unavailable = investigation_runtime.search_helpers(
        plan, catalog, has_retained_psi=True,
        available_columns={"debt_yield", "reporting_quarter"},
    )
    available = investigation_runtime.search_helpers(
        plan, catalog, has_retained_psi=True,
        available_columns={"debt_yield", "reporting_quarter"},
        has_population_context=True,
    )

    assert unavailable["selected_helper_id"] is None
    assert available["selected_helper_id"] == "population_segment_missingness"


def test_library_search_rejects_helper_that_would_replace_frozen_populations():
    catalog = investigation_runtime.helper_catalog(
        "psi", "Data", ["months_on_book", "year"]
    )
    result = investigation_runtime.search_helpers(
        {
            "analysis_kind": "Frozen-population cohort attribution",
            "question": "Compare baseline/current allocation by year.",
            "rationale": "Reuse the exact split_snapshot definition.",
            "preferred_helper_ids": ["segment_attribution"],
            "helper_params": {"column": "months_on_book", "segment_col": "year"},
        },
        catalog, has_retained_psi=True,
        available_columns={"months_on_book", "year"},
        has_population_context=True,
    )

    assert result["selected_helper_id"] is None
    assert "does not preserve the frozen diagnostic population allocation" in " ".join(
        result["matches"][0]["reasons"]
    )


def test_generated_code_guardrails_reject_unbounded_loop():
    errors = investigation_runtime.validate_generated_code(
        "while True:\n    pass\nresult = {}"
    )
    assert "Unbounded loop constructs are not permitted." in errors


def test_generated_code_guardrails_allow_finite_retained_collection_iteration():
    errors = investigation_runtime.validate_generated_code(
        "rows = []\nfor name in params['analysis_params']['columns']:\n"
        "    rows.append(name)\nresult = {'rows': rows}"
    )
    assert errors == []


def test_generated_code_guardrails_reject_known_infinite_iterator_constructor():
    errors = investigation_runtime.validate_generated_code(
        "for value in itertools.count():\n    pass\nresult = {}"
    )
    assert "Potentially unbounded iterator 'count' is not permitted." in errors


def test_generated_code_guardrails_reject_file_and_network_data_access():
    errors = investigation_runtime.validate_generated_code(
        "result = {'rows': len(pd.read_csv('https://example.test/data.csv'))}"
    )
    assert "External data access 'read_csv' is not permitted." in errors
    assert "Network locations are not permitted in generated code." in errors


def test_generated_code_runs_in_isolated_process_and_returns_dict():
    outcome = investigation_runtime.run_generated_code(
        "result = {'rows': int(len(df)), 'mean': float(df['x'].mean())}",
        pd.DataFrame({"x": [1.0, 2.0, 3.0]}), {}, timeout_seconds=20,
    )
    assert outcome["status"] == "completed"
    assert outcome["result"]["summary"].startswith("Generated analysis completed")
    assert outcome["result"]["metrics"] == {"rows": 3, "mean": 2.0}
    assert outcome["result"]["evidence_rows"] == []


def test_generated_code_receives_regular_values_and_explicit_feature_states():
    outcome = investigation_runtime.run_generated_code(
        "result = {'mean': float(df['NOI'].mean()), "
        "'physical': int(df[params['feature_state_context']['indicator_columns']['NOI']"
        "['physical_missing']].sum()), "
        "'special': int(df[params['feature_state_context']['indicator_columns']['NOI']"
        "['special: -999']].sum())}",
        pd.DataFrame({"NOI": [1000.0, 3000.0, None, -999.0]}), {}, timeout_seconds=20,
        feature_state_snapshot={"schema_version": 1, "columns": {"NOI": {
            "special_values_confirmed": True,
            "confirmed_special_values": [-999], "proposed_special_values": [],
        }}},
    )

    assert outcome["status"] == "completed"
    assert outcome["result"]["metrics"] == {"mean": 2000.0, "physical": 1, "special": 1}
    state = outcome["result"]["feature_state_reconciliation"]["NOI"]
    assert state["regular_rows"] == 2
    assert state["accounted_rows"] == 4
    assert state["reconciles"] is True


def test_generated_code_timeout_uses_aar_compatible_status(monkeypatch):
    def time_out(*_args, **_kwargs):
        raise investigation_runtime.subprocess.TimeoutExpired("sandbox", 15)

    monkeypatch.setattr(investigation_runtime.subprocess, "run", time_out)
    outcome = investigation_runtime.run_generated_code(
        "result = {'rows': int(len(df))}", pd.DataFrame({"x": [1.0]}), {},
    )

    assert outcome["status"] == "timed_out"
    assert outcome["ok"] is False
    assert "safety limit" in outcome["error"]


def test_generated_result_is_bounded_and_prioritizes_readable_evidence():
    raw_result = {
        "population_reconciliation": {
            "baseline_count": 19452,
            "current_count": 10800,
            "population_rule": "year split",
        },
        "joint_cells": [
            {"reporting_quarter": f"Q{index}", "baseline_count": index, "current_count": index + 1}
            for index in range(100)
        ],
        "top_contributing_cells": [
            {"reporting_quarter": "2012-Q2", "contribution": 0.42},
            {"reporting_quarter": "2012-Q1", "contribution": 0.31},
        ],
    }

    result = investigation_runtime.normalize_generated_result(raw_result)

    assert result["summary"].startswith(
        "Generated analysis reconciled 19,452 baseline rows and 10,800 current rows"
    )
    assert result["metrics"]["population_reconciliation.baseline_count"] == 19452
    assert len(result["evidence_rows"]) == investigation_runtime.MAX_EVIDENCE_ROWS
    assert result["evidence_rows"][0]["section"] == "top_contributing_cells"
    assert result["evidence_rows"][0]["reporting_quarter"] == "2012-Q2"
    assert result["truncation"] == {
        "evidence_rows_retained": 12,
        "evidence_rows_available": 102,
        "evidence_rows_truncated": True,
        "metrics_retained": 3,
    }
