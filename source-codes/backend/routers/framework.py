"""Framework metadata, results, reports, and agent routes for API v2."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from ai.v2 import agents_roster, service
from routers.v2_common import raise_api_error

router = APIRouter()


@router.get("/items/{item_id}/results")
def get_results(item_id: str, scope: str = "framework"):
    try:
        return service.results(item_id, scope)
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/items/{item_id}/report")
def item_report(item_id: str):
    try:
        from ai.v2 import report as report_svc

        payload, filename = report_svc.build_report(item_id)
        return Response(
            content=payload,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:  # noqa: BLE001
        raise_api_error(exc)


@router.get("/framework/overview")
def framework_overview():
    return service.framework_overview()


@router.get("/framework/areas")
def framework_areas():
    return service.framework_areas()


@router.get("/framework/tests")
def framework_tests():
    return service.framework_tests()


@router.get("/framework/matrix")
def framework_matrix():
    return service.framework_matrix()


@router.get("/agents")
def agents():
    return agents_roster.roster()
