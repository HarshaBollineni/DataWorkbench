import copy
import io

import pdfplumber
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from domains.rca.presentation import present_execution
from domains.rca.report import render_report
from domains.rca.report_content import hypothesis_groups, timeline, elapsed, cell_value


@pytest.mark.parametrize("metrics,label,unit", [
    ({"baseline": {"physical_null_rate": .05}, "current": {"physical_null_rate": .09}}, "Physical missing rate", "ratio"),
    ({"psi": .3}, "Population stability index", "number"),
    ({"outlier_rate": .02}, "Outlier rate", "ratio"),
    ({"duplicated_rows": 17}, "Records with duplicated keys", "count"),
    ({"early_corr": -.3, "recent_corr": .5}, "Correlation", "correlation"),
    ({"null_rate": .01, "duplicate_rate": .02}, "Missing rate", "ratio"),
])
def test_shared_metric_semantics(metrics, label, unit):
    execution = {"execution_id": "exec-1", "summary_json": {"result": {"metrics": metrics}}}
    original = copy.deepcopy(execution)
    card = present_execution(execution)["metrics"][0]
    assert card["label"] == label and card["unit"] == unit
    assert card["evidence_reference"] == "exec-1"
    assert original == execution


def test_invalid_unknown_and_failed_metrics_are_not_presented():
    for metrics in ({"outlier_rate": float("nan")}, {"outlier_rate": 2},
                    {"duplicated_rows": -1}, {"duplicated_rows": True},
                    {"fingerprint": 123}, {"current": {"physical_null_rate": .1}}):
        assert not present_execution({"summary_json": {"result": {"metrics": metrics}}})["metrics"]
    assert not present_execution({"summary_json": {"runtime": {"ok": False},
                                 "result": {"metrics": {"psi": .2}}}})["metrics"]


def _case(outcome="unresolved"):
    return {"case_id": "case-1", "item_id": "dataset-1", "state": "closed",
            "closure": {"outcome": outcome, "closed_at": "2026-09-24"},
            "conclusion": {"outcome_label": outcome, "approved_by": "reviewer",
                           "approval_rationale": "Evidence supports a partial explanation.",
                           "limiting_evidence": "Association does not establish causation."},
            "looks": [{"look_id": "look-1", "sql_or_helper_ref": "outlier_profile"}],
            "executions": {"look-1": {"execution_id": "exec-1", "status": "completed",
                "summary_json": {"result": {"summary": "Outlier evidence retained.",
                    "metrics": {"outlier_rate": .02}, "evidence_rows": [{"records": 2}]}}}},
            "aar_evidence": [{"artifact_id": "aar-1", "evidence_kind": "human_context",
                              "details": {"comment": "The source changed."}}]}


@pytest.mark.parametrize("outcome", ["unresolved", "root_cause_identified", "confirmed"])
def test_pdf_retains_outcome_evidence_and_long_prose(outcome):
    case = _case(outcome)
    case["conclusion"]["limiting_evidence"] = ("Association is not causation. " * 500) + "FINAL LIMITATION MARKER"
    original = copy.deepcopy(case)
    payload = render_report(case)
    assert payload.startswith(b"%PDF")
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        text = " ".join("\n".join(page.extract_text() or "" for page in pdf.pages).split())
        assert len(pdf.pages) > 2
    for value in (outcome, "FINAL LIMITATION MARKER", "reviewer", "aar-1", "The source changed.", "2.00%"):
        assert value in text
    assert case == original


def test_report_rejects_unfinished_case_and_handles_legacy_closure():
    case = _case()
    case["state"] = "investigation_loop"
    with pytest.raises(ValueError, match="after workflow closure"):
        render_report(case)
    case["state"] = "closed"
    del case["conclusion"]
    assert render_report(case).startswith(b"%PDF")
    text = render_report(case, "text").decode("utf-8")
    assert "1. Problem and conclusion" in text and "5. RCA closure timeline" in text
    with pytest.raises(ValueError, match="format"):
        render_report(case, "unknown")


def _modern_case(diagnostic="Population stability"):
    case = _case("root_cause_identified")
    case.update(created_at="2026-09-20T08:00:00+00:00", workflow_generation=2,
                case_file={"checklist_json": {"test_name": diagnostic, "metric": 0.23,
                    "table_name": "Portfolio", "threshold": 0.2, "violation_count": 964}})
    case["conclusion"].update(root_cause="Population composition explains the observed increase.",
        approved_at="2026-09-25T12:00:00+05:30", supporting_evidence_ids=["aar-1", "timing-only"])
    case["closure"]["closed_at"] = "2026-09-25T12:01:00+05:30"
    case["hypothesis_catalog"] = [
        {"hypothesis_id": "candidate-unused", "statement": "Untested source explanation", "number": 1},
        {"hypothesis_id": "hyp-selected", "statement": "Population composition changed", "number": 2},
    ]
    case["hypotheses"] = []  # The current workflow does not populate the legacy composer list.
    case["looks"] = [
        {"look_id": "look-1", "kind": "planned", "fork_json": {"hypothesis_id": "hyp-selected",
            "kind": "agent_driver_search", "combined_run_state": "completed", "plan": {"question": "What explains the reported change?"}}},
        {"look_id": "confirm", "kind": "planned", "fork_json": {"combined_parent_look_id": "look-1", "plan": {"question": "Does the explanation hold within segments?"}}},
    ]
    result = case["executions"]["look-1"]["summary_json"]["result"]
    result.update(metrics={"baseline": {"physical_null_rate": .05}, "current": {"physical_null_rate": .09}},
        evidence_rows=[{"population": population, "segment": "Physical missing" if index % 2 else "Regular",
            "rows": 1000 + index, "physical_null_count": 50, "physical_null_rate": .05,
            "declared_special_count": 2, "declared_special_rate": .002,
            "effective_missing_count": 52, "effective_missing_rate": .052,
            "population_fingerprint": "DO_NOT_PRINT_FINGERPRINT"} for index, population in enumerate(["baseline", "current"] * 7)],
        download_artifact_id="full-output-reference")
    case["executions"]["look-1"]["executed_at"] = "2026-09-25T06:05:00+00:00"
    case["executions"]["confirm"] = {"execution_id": "exec-confirm", "status": "completed",
        "executed_at": "2026-09-25T06:10:00+00:00", "summary_json": {"result": {"summary": "Within-segment evidence remains inconclusive."}}}
    case["aar_evidence"] += [
        {"artifact_id": "generation-start", "evidence_kind": "workflow_reset", "recorded_at": "2026-09-25T11:30:00+05:30", "created_by": "reviewer", "status": "completed"},
        {"artifact_id": "review-event", "evidence_kind": "llm_initial_review", "recorded_at": "2026-09-25T06:02:00Z", "status": "completed"},
        {"artifact_id": "combined-event", "evidence_kind": "combined_hypothesis_run", "recorded_at": "2026-09-25T06:12:00Z", "status": "completed",
         "details": {"look_id": "look-1", "interpretation": {"assessment": "inconclusive", "rationale": "This separator is associated, not causally established.", "evidence_points": ["Within-segment comparison attenuates the difference."]}}},
        {"artifact_id": "generated-code", "evidence_kind": "agent_code_generation", "status": "completed"},
        {"artifact_id": "timing-only", "evidence_kind": "operation_progress", "status": "completed"},
    ]
    return case


def test_modern_hypotheses_timeline_and_artifact_projection():
    case = _modern_case()
    groups = hypothesis_groups(case)
    assert groups[0]["hypothesis_id"] == "hyp-selected"
    assert groups[0]["number"] == 1 and len(groups[0]["looks"]) == 2
    assert groups[1]["number"] is None
    start, milestones = timeline(case, groups)
    assert start == "2026-09-25T11:30:00+05:30"
    assert elapsed(start, case["closure"]["closed_at"]) == "31m"
    assert [row["milestone"] for row in milestones] == [
        "RCA started", "Initial review", "Hypothesis 1 / run 1", "Conclusion approved", "RCA closed"]
    assert milestones[2]["at"] == "2026-09-25T06:12:00Z"
    assert milestones[2]["detail"] == "Inconclusive"
    assert elapsed(None, case["closure"]["closed_at"]) == "Not available"
    assert elapsed("2026-09-25", "2026-09-26") == "Not available"
    assert cell_value(.05, "physical_null_rate") == "5.00%"
    assert cell_value(1000, "rows") == "1,000"


@pytest.mark.parametrize("diagnostic", ["Population stability", "Row completeness", "Duplicate keys", "Business rule violations"])
def test_conclusion_led_pdf_has_real_tables_and_separate_artifacts(diagnostic, tmp_path):
    case = _modern_case(diagnostic)
    result = case["executions"]["look-1"]["summary_json"]["result"]
    if diagnostic == "Row completeness":
        result["metrics"] = {"null_rate": .05}
        result["evidence_rows"] = [{"period": "2026-Q1", "expected_rows": 1000,
            "missing_rows": 50, "missing_rate": .05}]
        expected_headers = ("Period", "Missing rows")
    elif diagnostic == "Duplicate keys":
        result["metrics"] = {"duplicated_rows": 50, "duplicate_rate": .05}
        result["evidence_rows"] = [{"key_group": "Repeated facility keys", "rows": 1000,
            "duplicate_rows": 50, "duplicate_rate": .05}]
        expected_headers = ("Key group", "Duplicate rows")
    elif diagnostic == "Business rule violations":
        result["metrics"] = {"violation_rate": .05}
        result["evidence_rows"] = [{"rule": "End date precedes start date", "checked_rows": 1000,
            "violating_rows": 50, "violation_rate": .05}]
        expected_headers = ("Rule", "Violating rows")
    else:
        expected_headers = ("Population", "Segment")
    original = copy.deepcopy(case)
    payload = render_report(case)
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        first_page = pdf.pages[0].extract_text()
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "Root-cause conclusion" in first_page and "Population composition explains" in first_page
        assert "Association does not establish causation" in first_page
        assert "Dataset snapshot" not in first_page
        assert "Hypothesis 1" in text and "No structured hypothesis" not in text
        assert "Inconclusive" in text and "Untested source explanation" in text
        assert "DO_NOT_PRINT_FINGERPRINT" not in text and "timing-only" not in text
        assert "31m" in text and "generated-code" in text and "full-output-reference" in text
        assert "5.00%" in text and "1,000" in text
        assert ("Showing the first 12 retained rows" in text) == (diagnostic == "Population stability")
        assert any(all(header in str(table) for header in expected_headers)
                   for page in pdf.pages for table in page.extract_tables())
        for page in pdf.pages:
            for char in page.chars:
                assert 32 <= char["x0"] <= char["x1"] <= page.width - 32
                assert 20 <= char["top"] < char["bottom"] <= page.height - 15
    assert case == original
    # Binary review artifact in isolated pytest output, not in application storage.
    (tmp_path / "completion.pdf").write_bytes(payload)


def test_multi_page_artifact_tables_repeat_headers_and_retain_last_row():
    case = _modern_case()
    case["aar_evidence"] += [{"artifact_id": f"additional-artifact-{i}",
        "evidence_kind": "agent_plan", "status": "completed"} for i in range(85)]
    with pdfplumber.open(io.BytesIO(render_report(case))) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
        artifact_pages = [page for page in pages if "additional-artifact-" in page]
        assert len(artifact_pages) >= 3
        assert all("Artifact / purpose" in page and "Retained reference" in page for page in artifact_pages)
        assert "additional-artifact-84" in "\n".join(pages)


def test_failed_analyses_do_not_become_findings_and_chat_is_supplementary():
    case = _modern_case()
    case["looks"][0]["fork_json"]["combined_run_state"] = "failed"
    case["executions"]["look-1"]["summary_json"]["runtime"] = {"ok": False, "error": "Execution timed out"}
    case["data_chat"] = {"turns": [{"question": "Could this be a source change?", "answer": "Additional source evidence is needed.",
        "status": "completed", "evidence_references": ["chat-source"], "limitations": ["Not a causal test."]}]}
    text = render_report(case, "text").decode()
    assert "Failed - no accepted finding" in text and "Execution timed out" in text
    assert "1,000" not in text  # Failed output is never presented as accepted evidence.
    assert text.index("Root-cause conclusion") < text.index("RCA closure timeline") < text.index("Additional data-chat evidence")
    assert "chat-source" in text and "Not a causal test." in text


def test_report_route_auth_scope_and_response(monkeypatch):
    from routers import v3
    from domains.rca import report
    app = FastAPI()
    app.include_router(v3.router)
    client = TestClient(app)
    calls = []
    def build(case_id, tenant_id, fmt):
        calls.append((case_id, tenant_id))
        return b"%PDF-test", "rca-completion-report.pdf"
    monkeypatch.setattr(report, "build_report", build)
    def denied(_):
        raise HTTPException(401, "Authentication required")
    monkeypatch.setattr(v3, "_principal", denied)
    assert client.get("/api/v3/rca/cases/case-1/report").status_code == 401
    assert calls == []
    monkeypatch.setattr(v3, "_principal", lambda _: {"tenant_id": "tenant-1"})
    response = client.get("/api/v3/rca/cases/case-1/report")
    assert response.status_code == 200
    assert calls == [("case-1", "tenant-1")]
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    def missing(*_):
        raise KeyError("Unknown RCA case")
    monkeypatch.setattr(report, "build_report", missing)
    assert client.get("/api/v3/rca/cases/foreign/report").status_code == 404
