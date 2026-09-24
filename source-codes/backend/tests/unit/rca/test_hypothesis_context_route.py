from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_context_route_preserves_hypothesis_and_tenant_scope(monkeypatch):
    from routers import v3

    app = FastAPI()
    app.include_router(v3.router)
    client = TestClient(app)
    calls = []
    monkeypatch.setattr(v3, "_principal", lambda _: {
        "username": "reviewer", "tenant_id": "tenant-1",
    })

    def save(case_id, comment, actor, **kwargs):
        calls.append((case_id, comment, actor, kwargs))
        return {"case_id": case_id}

    monkeypatch.setattr(v3.rca, "add_investigation_context", save)
    endpoint = "/api/v3/rca/cases/case-1/investigation/context"
    assert client.post(endpoint, json={
        "comment": "Source changed", "hypothesis_id": "hyp-2",
    }).status_code == 200
    assert calls[-1] == ("case-1", "Source changed", "reviewer", {
        "tenant_id": "tenant-1", "hypothesis_id": "hyp-2",
    })
    assert client.post(endpoint, json={"comment": "Legacy context"}).status_code == 200
    assert calls[-1][3]["hypothesis_id"] is None


def test_exploration_route_preserves_choice_and_tenant(monkeypatch):
    from routers import v3

    app = FastAPI()
    app.include_router(v3.router)
    client = TestClient(app)
    calls = []
    monkeypatch.setattr(v3, "_principal", lambda _: {"username": "reviewer", "tenant_id": "tenant-1"})
    monkeypatch.setattr(v3.rca, "get_case", lambda case_id, tenant: {"case_id": case_id})

    def plan(case_id, actor, **kwargs):
        calls.append((case_id, actor, kwargs))
        return {"look_id": "look-1"}

    monkeypatch.setattr(v3.rca, "planner_propose_look", plan)
    for choice in ("follow_up", "alternative"):
        response = client.post(f"/api/v3/rca/cases/case-1/planner-look?exploration={choice}")
        assert response.status_code == 200
        assert calls[-1] == ("case-1", "reviewer", {
            "tenant_id": "tenant-1", "kill_target_suspect_id": None, "exploration": choice,
        })
