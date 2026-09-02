"""Pure-engine tests for Test 2, Diagnostic 6: Row Completeness."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from analysis_runtime.contracts import stable_fingerprint
from dq_diagnostics.engines.row_completeness.engine import (
    RowCompletenessInputError,
    evaluate_row_completeness,
)
from dq_diagnostics.engines.row_completeness.models import build_external_report_payload
from dq_diagnostics.engines.row_completeness.periods import key_from_ordinal, parse_period


ZERO_LLM = {
    "llm_call_count": 0,
    "llm_used": False,
    "verdict_influenced_by_llm": False,
    "statement": "No LLM calls were made during this diagnostic journey",
    "events": [],
    "deterministic_inferences": [],
    "event_set_hash": "0" * 64,
}


def _scope(*, grain="monthly", floor=0.95, segment=None):
    return {
        "asset_id": "asset_1", "snapshot_id": "snapshot_1", "table": "portfolio",
        "facility_id": {"table": "portfolio", "column": "facility_id", "source": "manual",
                        "score": None, "reason": "Confirmed for test."},
        "period": {"table": "portfolio", "column": "period", "source": "manual",
                   "score": None, "reason": "Confirmed for test."},
        "segment": ({"table": "portfolio", "column": segment, "source": "manual",
                     "score": None, "reason": "Confirmed for test."} if segment else None),
        "reporting_grain": grain,
        "continuity_floor": floor,
    }


def _evaluate(frame, **scope_overrides):
    return evaluate_row_completeness(
        frame,
        scope=_scope(**scope_overrides),
        origin_run_id="run_1",
        calculation_inference_disclosure=ZERO_LLM,
        manifest_fingerprint=stable_fingerprint({"run": "run_1"}),
        source_artifact_references=[],
        evidence_limit=3,
        aggregate_limit=20,
    )


@pytest.mark.parametrize(("grain", "value", "expected"), [
    ("monthly", "2024-01", "2024-01"),
    ("monthly", 202402, "2024-02"),
    ("monthly", pd.Timestamp("2024-03-31"), "2024-03"),
    ("quarterly", "2024-Q1", "2024-Q1"),
    ("quarterly", "2024Q2", "2024-Q2"),
    ("quarterly", "2024-08-31", "2024-Q3"),
    ("semiannual", "2024-H1", "2024-H1"),
    ("semiannual", "2024H2", "2024-H2"),
    ("annual", 2024, "2024"),
    ("annual", "2025-06-30", "2025"),
])
def test_period_parser_supports_confirmed_grains(grain, value, expected):
    parsed = parse_period(value, grain)
    assert parsed.key == expected
    assert key_from_ordinal(parsed.ordinal, grain) == expected


@pytest.mark.parametrize(("grain", "value"), [
    ("monthly", "2024-Q1"),
    ("quarterly", "202401"),
    ("semiannual", "2024-Q2"),
    ("annual", "2024-H1"),
    ("monthly", "not-a-period"),
])
def test_period_parser_rejects_incompatible_or_unparseable_labels(grain, value):
    with pytest.raises(ValueError):
        parse_period(value, grain)


def test_complete_panel_passes_with_optional_segment_not_applicable():
    frame = pd.DataFrame({
        "facility_id": ["A", "A", "A", "B", "B"],
        "period": ["2025-01", "2025-02", "2025-03", "2025-02", "2025-03"],
    })
    result = _evaluate(frame)
    outcomes = {rule.rule_id: rule.outcome for rule in result.rules}

    assert result.overall_verdict == "pass"
    assert result.required_facility_period_pairs == 5
    assert result.received_required_pairs == 5
    assert result.continuity_coverage == 1.0
    assert outcomes == {
        "T2D6-01": "PASS", "T2D6-02": "PASS", "T2D6-03": "PASS",
        "T2D6-04": "PASS", "T2D6-05": "PASS", "T2D6-06": "NOT-APPLICABLE",
    }


def test_invalid_rows_full_calendar_gap_duplicates_and_observed_span_reconcile():
    frame = pd.DataFrame({
        "facility_id": ["A", "A", "A", "B", "B", "   "],
        "period": ["2025-01", "2025-01", "2025-03", "2025-01", "2025-03", "bad"],
        "value": [1, 1, 3, 1, 3, 2],
    })
    result = _evaluate(frame)
    by_rule = {rule.rule_id: rule for rule in result.rules}

    assert result.overall_verdict == "violation"
    assert by_rule["T2D6-01"].issue_count == 1
    assert by_rule["T2D6-02"].issue_count == 1
    assert by_rule["T2D6-02"].evidence[0].period == "2025-02"
    assert by_rule["T2D6-04"].metrics["exact_duplicate_pairs"] == 1
    assert by_rule["T2D6-04"].metrics["surplus_rows"] == 1
    assert result.required_facility_period_pairs == 6
    assert result.received_required_pairs == 4
    assert result.issue_summary.primary_issue_instances == 4  # invalid + 2 missing + 1 surplus


def test_calendar_rule_counts_a_period_even_when_its_only_row_has_invalid_facility():
    frame = pd.DataFrame({
        "facility_id": ["A", None, "A"],
        "period": ["2025-01", "2025-02", "2025-03"],
    })
    result = _evaluate(frame)

    assert result.rules[0].outcome == "VIOLATION"
    assert result.rules[1].outcome == "PASS"
    assert result.rules[1].metrics["received_periods"] == 3


def test_uncovered_calendar_gap_is_counted_once_as_a_primary_issue():
    frame = pd.DataFrame({
        "facility_id": ["A", "B"],
        "period": ["2025-01", "2025-03"],
    })
    result = _evaluate(frame)

    assert result.rules[1].outcome == "VIOLATION"
    assert result.issue_summary.missing_facility_period_pairs == 0
    assert result.issue_summary.missing_reporting_periods == 1
    assert result.issue_summary.calendar_gaps_without_required_pairs == 1
    assert result.issue_summary.primary_issue_instances == 1


def test_duplicate_classification_separates_exact_from_conflicting():
    frame = pd.DataFrame({
        "facility_id": ["A", "A", "B", "B"],
        "period": ["2025-01"] * 4,
        "balance": [10, 10, 20, 21],
    })
    result = _evaluate(frame)
    duplicate = result.rules[3]

    assert duplicate.issue_count == 2
    assert duplicate.metrics["exact_duplicate_pairs"] == 1
    assert duplicate.metrics["conflicting_duplicate_pairs"] == 1
    assert duplicate.metrics["surplus_rows"] == 2
    assert result.required_facility_period_pairs == 2
    assert result.received_required_pairs == 2


def test_period_level_failure_can_be_hidden_by_passing_portfolio_coverage():
    rows = []
    for facility in range(100):
        for month in range(1, 13):
            if month == 6 and facility < 6:
                continue
            rows.append((f"F{facility:03d}", f"2025-{month:02d}"))
    result = _evaluate(pd.DataFrame(rows, columns=["facility_id", "period"]))

    assert result.rules[2].outcome == "VIOLATION"
    assert result.rules[2].metrics["minimum_period_coverage"] == pytest.approx(0.94)
    assert result.rules[4].outcome == "PASS"
    assert result.continuity_coverage == pytest.approx(1194 / 1200)


def test_segment_failure_can_be_hidden_by_portfolio_and_period_results():
    rows = []
    for facility in range(100):
        segment = "SMALL" if facility < 10 else "LARGE"
        for month in range(1, 4):
            if facility == 0 and month == 2:
                continue
            rows.append((f"F{facility:03d}", f"2025-{month:02d}", segment))
    result = _evaluate(pd.DataFrame(rows, columns=["facility_id", "period", "segment"]),
                       segment="segment")

    assert result.rules[2].outcome == "PASS"
    assert result.rules[4].outcome == "PASS"
    assert result.rules[5].outcome == "VIOLATION"
    assert result.rules[5].issue_count == 1
    failing = [row for row in result.rules[5].metrics["cell_results"] if row["coverage"] < 0.95]
    assert failing == [{"segment": "SMALL", "period": "2025-02", "required_facilities": 10,
                        "received_facilities": 9, "missing_facilities": 1, "coverage": 0.9}]


def test_fully_unstable_segment_assignment_is_not_assessable_not_passed():
    frame = pd.DataFrame({
        "facility_id": ["A", "A", "B", "B"],
        "period": ["2025-01", "2025-02", "2025-01", "2025-02"],
        "segment": ["X", "Y", None, "Z"],
    })
    result = _evaluate(frame, segment="segment")

    assert result.rules[5].outcome == "NOT-ASSESSABLE"
    assert result.rules[5].affected_population["unstable_facilities"] == 2
    assert result.overall_verdict == "inconclusive"


def test_all_invalid_rows_make_grid_rules_not_applicable_without_hiding_key_failure():
    frame = pd.DataFrame({"facility_id": [None, ""], "period": [None, "bad"]})
    result = _evaluate(frame)

    assert result.rules[0].outcome == "VIOLATION"
    assert result.rules[1].outcome == "NOT-APPLICABLE"
    assert result.rules[2].outcome == "NOT-APPLICABLE"
    assert result.rules[3].outcome == "PASS"
    assert result.rules[4].outcome == "NOT-APPLICABLE"
    assert result.overall_verdict == "violation"


def test_single_period_facility_has_no_constructed_gap():
    result = _evaluate(pd.DataFrame({"facility_id": ["A"], "period": ["2025-01"]}))
    assert result.required_facility_period_pairs == 1
    assert result.issue_summary.missing_facility_period_pairs == 0
    assert result.rules[4].outcome == "PASS"


def test_evidence_is_bounded_and_two_runs_are_byte_deterministic():
    frame = pd.DataFrame({
        "facility_id": ["A", "A", "B", "B", "C", "C", "D", "D"],
        "period": ["2025-01", "2025-03"] * 4,
    })
    first = _evaluate(frame)
    second = _evaluate(frame)

    gap_rule = first.rules[4]
    assert gap_rule.total_findings == 4
    assert len(gap_rule.evidence) == 3
    assert gap_rule.evidence_truncated is True
    assert first.model_dump_json() == second.model_dump_json()


def test_external_report_masks_segment_metrics_facilities_and_row_references():
    frame = pd.DataFrame({
        "facility_id": ["SECRET-A", "SECRET-A", "SECRET-A"],
        "period": ["2025-01", "2025-01", "2025-03"],
        "segment": ["SENSITIVE-SEGMENT"] * 3,
        "value": [1, 1, 3],
    })
    reconciliation = _evaluate(frame, segment="segment")
    report = build_external_report_payload(
        reconciliation,
        report_id="report_1",
        source_artifact_id="art_1",
        source_payload_hash="a" * 64,
        renderer_version="1.0.0",
        report_salt="salt",
        recommended_next_steps=[],
        limitations=["Observed-span method."],
        current_run_id="run_1",
        segment_label_policy="mask",
    )
    serialized = json.dumps(report.model_dump(mode="json"), sort_keys=True)

    assert "SECRET-A" not in serialized
    assert "SENSITIVE-SEGMENT" not in serialized
    assert "ROW-00000001" not in serialized
    assert "FAC-" in serialized and "SEG-" in serialized and "ROW-" in serialized


def test_missing_bound_column_fails_before_any_rule_execution():
    with pytest.raises(RowCompletenessInputError, match="bound columns"):
        _evaluate(pd.DataFrame({"facility_id": ["A"]}))


def test_operational_grid_limit_blocks_pathological_span_before_materialization():
    frame = pd.DataFrame({"facility_id": ["A", "A"], "period": ["2000", "2025"]})
    with pytest.raises(RowCompletenessInputError, match="operational limit"):
        evaluate_row_completeness(
            frame, scope=_scope(grain="annual"), origin_run_id="run_1",
            calculation_inference_disclosure=ZERO_LLM,
            manifest_fingerprint=stable_fingerprint({"run": "run_1"}),
            max_required_pairs=10,
        )
