"""Issue Management routes for API v2."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ai.v2 import issues as issues_svc
from routers.v2_common import raise_api_error, stream_events

router = APIRouter()


class CloseIssueIn(BaseModel):
    rationale: str


class RaiseIssueIn(BaseModel):
    title: str
    description: str = ""
    owner: str = ""
    priority: str = "Medium"
    target_date: str = ""


class AnalysesIn(BaseModel):
    analyses: list[dict]


class TrackedPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    owner: str | None = None
    priority: str | None = None
    target_date: str | None = None
    status: str | None = None


@router.get("/items/{item_id}/issues")
def item_issues(item_id: str):
    try:
        from assets.staleness import annotate_payload
        return annotate_payload(issues_svc.list_issues(item_id))
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/issues/register")
def issues_register(status: str | None = None, item_id: str | None = None,
                    criticality: str | None = None):
    from assets.staleness import annotate_stale
    return annotate_stale(issues_svc.register(status, item_id, criticality))


@router.get("/issues/{issue_row_id}")
def issue_detail(issue_row_id: str):
    try:
        from assets.staleness import annotate_payload
        return annotate_payload(issues_svc.get_issue(issue_row_id))
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/issues/{issue_row_id}/rca/stream")
@router.post("/issues/{issue_row_id}/rca/stream")
def rca_stream(issue_row_id: str):
    def events():
        yield {"phase": "start", "agent": "rca",
               "thought": "Reading the failed test result, saved profile, and framework remediation guidance."}
        try:
            issue = issues_svc.get_issue(issue_row_id)
            yield {"phase": "thinking", "agent": "rca",
                   "thought": f"Analysing '{issue['test_name']}' across {len(issue.get('columns') or [])} "
                              f"failing column(s) in {issue['table_name']}."}
            payload = issues_svc.rca(issue_row_id)
            yield {"phase": "done", "agent": "rca",
                   "thought": "Root-cause analysis ready.", **payload}
        except Exception as exc:  # noqa: BLE001
            yield {"phase": "error", "agent": "rca", "thought": str(exc)}
    return stream_events(events())


@router.post("/issues/{issue_row_id}/analysis")
def create_issue_analyses(issue_row_id: str, body: AnalysesIn):
    try:
        return issues_svc.create_analyses(issue_row_id, body.analyses)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/issues/{issue_row_id}/analysis/interpret")
def interpret_issue_analyses(issue_row_id: str):
    try:
        return issues_svc.interpret_analyses(issue_row_id)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.patch("/issues/tracked/{issue_id}")
def patch_tracked_issue(issue_id: str, body: TrackedPatch):
    try:
        return issues_svc.update_tracked(issue_id, body.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/issues/{issue_row_id}/close")
def close_issue(issue_row_id: str, body: CloseIssueIn):
    try:
        return issues_svc.close_issue(issue_row_id, body.rationale)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.post("/issues/{issue_row_id}/raise")
def raise_issue(issue_row_id: str, body: RaiseIssueIn):
    try:
        return issues_svc.raise_issue(
            issue_row_id, title=body.title, description=body.description,
            owner=body.owner, priority=body.priority, target_date=body.target_date)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)
