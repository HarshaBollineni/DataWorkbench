"""Asset selection, catalogue, and version comparison routes for API v2."""
from __future__ import annotations

from fastapi import APIRouter

from routers.v2_common import sanitize_internal_errors

router = APIRouter()


@router.post("/assets/{asset_id}/select")
@sanitize_internal_errors
def select_asset(asset_id: str):
    from analytics.events import event_object_id, record_event
    from assets import service as assets_service
    import system_db as system_db

    asset = assets_service.require_asset(asset_id)
    record_event(event_type="asset_selected", actor="system",
        at=system_db.now_ist(),
        object_type="asset",
        object_id=event_object_id("asset", asset_id),
        workflow_context=asset["system_id"],
        detail={"kind": asset.get("kind")},
    )
    return asset


@router.get("/assets/{asset_id}/versions/{version_a}/diff/{version_b}")
@sanitize_internal_errors
def version_diff(asset_id: str, version_a: int, version_b: int):
    from analytics.events import event_object_id, record_event
    from assets.diff import diff_versions
    import system_db as system_db

    result = diff_versions(asset_id, version_a, version_b)
    version = system_db.query_one(
        "dq_asset_versions", asset_id=asset_id, version_no=version_b)
    asset = system_db.query_one("dq_assets", asset_id=asset_id) or {}
    if version:
        record_event(event_type="version_diff_viewed", actor="system",
            at=system_db.now_ist(),
            object_type="version",
            object_id=event_object_id("version", version["version_id"]),
            workflow_context=asset.get("system_id"),
            detail={"version_a": version_a, "version_b": version_b},
        )
    return result


@router.get("/catalogue")
@sanitize_internal_errors
def catalogue():
    from analytics import catalogue as catalogue_model
    from analytics.events import event_object_id, record_event
    import system_db as system_db

    assets = catalogue_model.list_catalogue()
    for asset in assets:
        record_event(event_type="catalogue_viewed", actor="system",
            at=system_db.now_ist(),
            object_type="asset",
            object_id=event_object_id("asset", asset["asset_id"]),
            workflow_context=asset.get("system_id"),
        )
    return assets


@router.get("/catalogue/{asset_id}")
@sanitize_internal_errors
def catalogue_asset(asset_id: str):
    from analytics import catalogue as catalogue_model
    from analytics.events import event_object_id, record_event
    import system_db as system_db

    result = catalogue_model.get_catalogue(asset_id)
    record_event(event_type="catalogue_viewed", actor="system",
        at=system_db.now_ist(),
        object_type="asset",
        object_id=event_object_id("asset", asset_id),
        workflow_context=result.get("system_id"),
    )
    return result
