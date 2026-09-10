"""Pure-engine tests for Test 2, Diagnostic 6: Row Completeness."""
from __future__ import annotations

import json
import time

import pandas as pd
import pytest

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.dataset_structure_context import project_materialized_dataset_structure
from dq_diagnostics.engines.row_completeness.engine import (
    RowCompletenessInputError,
    evaluate_row_completeness,
)
from dq_diagnostics.engines.row_completeness.models import build_external_report_payload
from dq_diagnostics.engines.row_completeness.periods import key_from_ordinal, parse_period
from domains.test_lab.diagnostics.t2_d06_row_completeness.dsc_cadence_shadow import _comparison, observe, schedule, _validate_projection, request_fingerprint


ZERO_LLM = {
    "llm_call_count": 0,
    "llm_used": False,
    "verdict_influenced_by_llm": False,
    "statement": "No LLM calls were made during this diagnostic journey",
    "events": [],
    "deterministic_inferences": [],
    "event_set_hash": "0" * 64,
}


@pytest.mark.parametrize(("grain", "interval"), [
    ("monthly", {"unit": "month", "step": 1}),
    ("quarterly", {"unit": "quarter", "step": 1}),
    ("quarterly", {"unit": "month", "step": 3}),
    ("semiannual", {"unit": "quarter", "step": 2}),
    ("semiannual", {"unit": "month", "step": 6}),
    ("annual", {"unit": "year", "step": 1}),
    ("annual", {"unit": "quarter", "step": 4}),
    ("annual", {"unit": "month", "step": 12}),
])
def test_d06_cadence_shadow_closed_interval_equivalences(grain, interval):
    result = {"outcome": "fulfilled", "cadence": {"value": {
        "state": "regular", "observed_interval_class": interval}}}
    assert _comparison(result, grain)[0] == "supports"


def test_d06_cadence_shadow_never_calls_projection_when_flag_is_disabled(monkeypatch):
    monkeypatch.setattr("tenancy.is_flag_enabled", lambda *_args, **_kwargs: False)
    called = []
    frozen = {"run_id": "drun_shadow", "manifest_fingerprint": "0" * 64,
              "scope": _scope()}
    assert observe(frozen, tenant_id="tenant-a", projection=lambda *_args, **_kwargs: called.append(True)) is None
    assert called == []


def test_d06_cadence_shadow_contains_tenant_flag_failures_before_projection(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("feature flag service unavailable")
    monkeypatch.setattr("tenancy.is_flag_enabled", unavailable)
    called = []
    frozen = {"run_id": "drun_shadow", "manifest_fingerprint": "0" * 64,
              "scope": _scope()}
    assert observe(frozen, tenant_id="tenant-a", projection=lambda *_args, **_kwargs: called.append(True)) is None
    assert called == []


def test_d06_cadence_shadow_deadline_starts_before_a_slow_flag_lookup(monkeypatch):
    monkeypatch.setattr("tenancy.is_flag_enabled", lambda *_args, **_kwargs: (time.sleep(0.01), True)[1])
    frozen = {"run_id": "drun_shadow", "manifest_fingerprint": "0" * 64, "scope": _scope()}
    projected, metrics = [], []
    result = observe(frozen, tenant_id="tenant-a", deadline=time.monotonic() - 0.001,
                     projection=lambda *_args, **_kwargs: projected.append(True),
                     metric=lambda **item: metrics.append(item))
    assert result == {"outcome": "error", "reason": "timeout"}
    assert projected == [] and metrics[0]["reason"] == "timeout"


def test_d06_cadence_shadow_schedule_does_not_read_flags_or_block(monkeypatch):
    monkeypatch.setattr("tenancy.is_flag_enabled", lambda *_args, **_kwargs: pytest.fail("scheduler must not read flags"))
    started = []
    class Thread:
        def __init__(self, **_kwargs):
            pass
        def start(self):
            started.append(True)
    class Timer:
        daemon = True
        def __init__(self, *_args, **_kwargs):
            pass
        def start(self):
            pass
        def cancel(self):
            pass
    monkeypatch.setattr("domains.test_lab.diagnostics.t2_d06_row_completeness.dsc_cadence_shadow.threading.Thread", Thread)
    monkeypatch.setattr("domains.test_lab.diagnostics.t2_d06_row_completeness.dsc_cadence_shadow.threading.Timer", Timer)
    schedule({"run_id": "drun_shadow", "manifest_fingerprint": "0" * 64, "scope": _scope()}, tenant_id="tenant-a")
    assert started == [True]


def test_d06_cadence_shadow_thread_start_failure_releases_single_flight_slot(monkeypatch):
    from domains.test_lab.diagnostics.t2_d06_row_completeness import dsc_cadence_shadow
    class Timer:
        daemon = True
        def __init__(self, *_args, **_kwargs):
            pass
        def start(self):
            pass
        def cancel(self):
            pass
    class BrokenThread:
        def __init__(self, **_kwargs):
            pass
        def start(self):
            raise RuntimeError("thread unavailable")
    monkeypatch.setattr(dsc_cadence_shadow.threading, "Timer", Timer)
    monkeypatch.setattr(dsc_cadence_shadow.threading, "Thread", BrokenThread)
    frozen = {"run_id": "drun_shadow_start_failure", "manifest_fingerprint": "0" * 64, "scope": _scope()}
    assert dsc_cadence_shadow.schedule(frozen, tenant_id="tenant-a") is None
    # The failed launch must not poison its keyed single-flight marker.
    assert ("tenant-a", "drun_shadow_start_failure", "v1") not in dsc_cadence_shadow._IN_FLIGHT


def test_d06_cadence_shadow_schedules_distinct_run_keys_without_global_suppression(monkeypatch):
    from domains.test_lab.diagnostics.t2_d06_row_completeness import dsc_cadence_shadow
    started = []
    class Timer:
        daemon = True
        def __init__(self, *_args, **_kwargs):
            pass
        def start(self):
            pass
        def cancel(self):
            pass
    class Thread:
        def __init__(self, **_kwargs):
            pass
        def start(self):
            started.append(True)
    monkeypatch.setattr(dsc_cadence_shadow.threading, "Timer", Timer)
    monkeypatch.setattr(dsc_cadence_shadow.threading, "Thread", Thread)
    with dsc_cadence_shadow._IN_FLIGHT_LOCK:
        dsc_cadence_shadow._IN_FLIGHT.clear()
    for run_id in ("drun_shadow_a", "drun_shadow_b"):
        schedule({"run_id": run_id, "manifest_fingerprint": "0" * 64, "scope": _scope()}, tenant_id="tenant-a")
    assert started == [True, True]
    with dsc_cadence_shadow._IN_FLIGHT_LOCK:
        dsc_cadence_shadow._IN_FLIGHT.clear()


def test_d06_cadence_shadow_locked_flag_consumes_shared_db_budget(monkeypatch):
    seen, called = [], []
    def locked_flag(_tenant, _key, *, timeout):
        seen.append(timeout)
        time.sleep(0.11)
        return True
    monkeypatch.setattr("tenancy.is_flag_enabled", locked_flag)
    frozen = {"run_id": "drun_shadow_locked_flag", "manifest_fingerprint": "0" * 64,
              "scope": _scope()}
    assert observe(
        frozen, tenant_id="tenant-a",
        projection=lambda *_args, **_kwargs: called.append("projection"),
        audit=lambda *_args, **_kwargs: called.append("audit"),
    ) is None
    assert seen and 0 < seen[0] <= 0.1
    assert called == []


def test_d06_cadence_shadow_slow_telemetry_is_deadline_bounded(monkeypatch):
    monkeypatch.setattr("tenancy.is_flag_enabled", lambda *_args, **_kwargs: True)
    frozen = {"run_id": "drun_shadow_telemetry", "manifest_fingerprint": "0" * 64, "scope": _scope()}
    request = {"asset_id": "asset_1", "snapshot_id": "snapshot_1", "table": "portfolio",
        "predicate": "table.temporal/observed_cadence", "axis_id": "column:period",
        "axis_column": {"table": "portfolio", "column": "period"},
        "grouping": [{"table": "portfolio", "column": "facility_id"}],
        "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
    response = {"projection_version": 1, "request_identity": request_fingerprint(request),
        "outcome": "fulfilled", "reason": None,
        "resolved_as_of": {"timestamp": "2026-01-01T00:00:00+00:00", "read_boundary_fingerprint": "c" * 64},
        "cadence": {"resolution_state": "observed", "value": {"state": "regular",
                    "observed_interval_class": {"unit": "month", "step": 1}},
                    "assertion_pin": {"artifact_id": "art_shadow", "payload_hash": "a" * 64,
                    "dependency_fingerprint": "b" * 64}}}
    audits = []
    metric_timeout = observe(frozen, tenant_id="tenant-a", projection=lambda *_a, **_k: response,
        metric=lambda **_item: time.sleep(0.06), audit=lambda *_item, **_kwargs: audits.append(True),
        deadline=time.monotonic() + 0.05)
    assert metric_timeout == {"outcome": "error", "reason": "timeout"} and audits == []
    audit_timeout = observe(frozen, tenant_id="tenant-a", projection=lambda *_a, **_k: response,
        metric=lambda **_item: None, audit=lambda *_item, **_kwargs: time.sleep(0.06),
        deadline=time.monotonic() + 0.05)
    assert audit_timeout == {"outcome": "error", "reason": "timeout"}


@pytest.mark.parametrize("outcome, reason", [
    ("selected_pair_absent", None), ("unavailable", "source_missing"),
    ("ambiguous", None), ("error", "deadline_exceeded"),
])
def test_d06_cadence_shadow_projection_response_is_closed(outcome, reason):
    request = {"asset_id": "asset", "snapshot_id": "snap", "table": "t", "predicate": "table.temporal/observed_cadence",
        "axis_id": "column:p", "axis_column": {"table": "t", "column": "p"},
        "grouping": [{"table": "t", "column": "f"}], "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
    response = {"projection_version": 1, "request_identity": request_fingerprint(request),
        "outcome": outcome, "reason": reason,
        "resolved_as_of": {"timestamp": "read", "read_boundary_fingerprint": "a" * 64}}
    assert _validate_projection(response, request)
    response["untrusted"] = "nope"
    assert not _validate_projection(response, request)


def test_d06_shadow_projector_honours_near_expired_db_budget(monkeypatch):
    """A 90 ms-established budget never starts a fresh 100 ms DB wait."""
    captured = []
    class SlowConnection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, *_args, **_kwargs):
            time.sleep(0.05)
            return self
        def set_progress_handler(self, *_args, **_kwargs): pass
    def get_conn(**kwargs):
        captured.append(kwargs["timeout"])
        return SlowConnection()
    monkeypatch.setattr(db, "get_conn", get_conn)
    request = {"asset_id": "asset", "snapshot_id": "snap", "table": "table",
        "predicate": "table.temporal/observed_cadence", "axis_id": "column:period",
        "axis_column": {"table": "table", "column": "period"},
        "grouping": [{"table": "table", "column": "facility"}],
        "consumer_id": "diagnostic:6:dsc-cadence-shadow-v1"}
    repository = type("Repository", (), {"root": "."})()
    result = project_materialized_dataset_structure(request, repository=repository, deadline=time.monotonic() + 0.04)
    assert captured and 0 < captured[0] < 0.1
    assert (result["outcome"], result["reason"]) == ("error", "deadline_exceeded")


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
