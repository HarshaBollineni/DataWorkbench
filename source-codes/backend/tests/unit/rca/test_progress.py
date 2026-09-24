from unittest.mock import patch

import pytest

from domains.rca import progress, service


def test_nested_timings_and_failure_preserve_exception():
    events = []
    case = {"case_id": "case-1", "workflow_generation": 2}

    @progress.action("Investigation")
    def run(case_id, actor, tenant_id="tenant"):
        with progress.phase("Planning"):
            progress.persistence_elapsed(0.25)
            with progress.phase("Generating code"):
                raise ValueError("model failed")

    def retain(state, status="running"):
        events.append((status, [dict(t) for t in state["timings"]], state["persistence"]))

    with patch.object(service, "require_case", return_value=case), patch.object(progress, "_publish", side_effect=retain):
        with pytest.raises(ValueError, match="model failed"):
            run("case-1", "tester")
    assert events[-1][0] == "failed"
    assert [t["phase"] for t in events[-1][1]] == ["Generating code", "Planning"]
    assert all(t["seconds"] >= 0 and t["status"] == "failed" for t in events[-1][1])
    assert events[-1][2] == 0.25
    assert progress._active.get() is None


def test_checkpoint_failure_does_not_change_analysis_result(caplog):
    @progress.action("Analysis")
    def run(case_id, actor):
        return 42
    with patch.object(service, "require_case", return_value={"case_id": "case-1"}), patch.object(progress, "_publish", side_effect=OSError("disk unavailable")):
        assert run("case-1", "tester") == 42
    assert "checkpoint could not be retained" in caplog.text


def test_read_only_operation_does_not_publish():
    @progress.action("Report", retain=False)
    def report(case_id, tenant_id):
        return b"pdf"
    with patch.object(service, "require_case", return_value={"case_id": "case-1"}), patch.object(progress, "_publish") as publish:
        assert report("case-1", "tenant-1") == b"pdf"
    publish.assert_not_called()


def test_timing_checkpoints_do_not_displace_chat_evidence():
    evidence = [{"artifact_id": "finding", "evidence_kind": "agent_interpretation",
                 "status": "completed", "details": {"interpretation": "Retained finding"}}]
    evidence += [{"artifact_id": f"timing-{index}", "evidence_kind": "operation_progress", "status": "completed"}
                 for index in range(40)]
    with patch.object(service.s, "query", return_value=[]):
        supplied, refs = service._chat_supplied_evidence({"case_id": "case"}, [], {}, evidence)
    assert refs == ("finding",)
    assert "Retained finding" in str(supplied)


def test_progress_route_checks_tenant_before_reading():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import v3
    app = FastAPI()
    app.include_router(v3.router)
    with patch.object(v3, "_principal", return_value={"tenant_id": "tenant-1"}), patch.object(service, "require_case", side_effect=KeyError("Unknown case")) as require, patch.object(progress, "read") as read:
        response = TestClient(app).get("/api/v3/rca/cases/foreign/progress")
    assert response.status_code == 404
    require.assert_called_once_with("foreign", "tenant-1")
    read.assert_not_called()
    with patch.object(v3, "_principal", return_value={"tenant_id": "tenant-1"}), patch.object(service, "require_case", return_value={"case_id": "case-1"}), patch.object(progress, "read", return_value={"case_id": "case-1", "operation": None}):
        response = TestClient(app).get("/api/v3/rca/cases/case-1/progress")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
