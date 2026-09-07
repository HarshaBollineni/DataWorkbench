"""Structural checks for behavior-preserving router decomposition."""
from __future__ import annotations

from fastapi import FastAPI


SOURCING_ROUTES = {
    ("GET", "/api/v2/assets/next-id"),
    ("GET", "/api/v2/assets"),
    ("POST", "/api/v2/assets/upload-target"),
    ("POST", "/api/v2/items"),
    ("GET", "/api/v2/items"),
    ("PATCH", "/api/v2/items/{item_id}"),
    ("POST", "/api/v2/items/{item_id}/files"),
    ("POST", "/api/v2/items/{item_id}/finalize"),
    ("POST", "/api/v2/items/{item_id}/reupload"),
    ("GET", "/api/v2/assets/{asset_id}/supersede-preview"),
    ("POST", "/api/v2/assets/{asset_id}/refresh"),
    ("POST", "/api/v2/items/{item_id}/process"),
    ("GET", "/api/v2/snapshots/{snapshot_id}/summary"),
    ("POST", "/api/v2/assets/{asset_id}/versions/{version_no}/restore"),
    ("GET", "/api/v2/items/{item_id}/ingest"),
    ("GET", "/api/v2/items/{item_id}/tables"),
    ("POST", "/api/v2/sources/inspect"),
    ("POST", "/api/v2/items/{item_id}/source-bundle"),
    ("PUT", "/api/v2/items/{item_id}/dictionary-review"),
    ("GET", "/api/v2/items/{item_id}/columns"),
    ("GET", "/api/v2/items/{item_id}/profile/stream"),
    ("GET", "/api/v2/items/{item_id}/inventory"),
    ("PUT", "/api/v2/items/{item_id}/inventory"),
    ("POST", "/api/v2/items/{item_id}/abandon"),
}

ISSUE_ROUTES = {
    ("GET", "/api/v2/items/{item_id}/issues"),
    ("GET", "/api/v2/issues/register"),
    ("GET", "/api/v2/issues/{issue_row_id}"),
    ("GET", "/api/v2/issues/{issue_row_id}/rca/stream"),
    ("POST", "/api/v2/issues/{issue_row_id}/rca/stream"),
    ("POST", "/api/v2/issues/{issue_row_id}/analysis"),
    ("POST", "/api/v2/issues/{issue_row_id}/analysis/interpret"),
    ("PATCH", "/api/v2/issues/tracked/{issue_id}"),
    ("POST", "/api/v2/issues/{issue_row_id}/close"),
    ("POST", "/api/v2/issues/{issue_row_id}/raise"),
}

DIAGNOSTIC_ROUTES = {
    ("GET", "/api/v2/items/{item_id}/diagnostics/board"),
    ("GET", "/api/v2/items/{item_id}/diagnostics/board/summary"),
    ("GET", "/api/v2/items/{item_id}/diagnostics/board/cards/{diagnostic_id}"),
    ("POST", "/api/v2/items/{item_id}/diagnostics/manifest"),
    ("PATCH", "/api/v2/diagnostics/manifests/{run_id}"),
    ("GET", "/api/v2/diagnostics/manifests/{run_id}"),
    ("POST", "/api/v2/diagnostics/manifests/{run_id}/run"),
    ("POST", "/api/v2/diagnostics/manifests/{run_id}/psi-bins/{feature}/draft"),
    ("POST", "/api/v2/diagnostics/manifests/{run_id}/psi-bins/{feature}/freeze"),
    ("GET", "/api/v2/diagnostics/runs/{run_id}/stream"),
    ("POST", "/api/v2/diagnostics/runs/{run_id}/stream"),
    ("GET", "/api/v2/items/{item_id}/diagnostics/results"),
    ("POST", "/api/v2/diagnostics/findings/{finding_id}/disposition"),
    ("GET", "/api/v2/diagnostics/results/{result_id}/binning-impact"),
    ("POST", "/api/v2/diagnostics/results/{result_id}/binning-preview"),
    ("POST", "/api/v2/diagnostics/results/{result_id}/binning-review"),
    ("GET", "/api/v2/items/{item_id}/diagnostics/coverage-summary"),
    ("GET", "/api/v2/diagnostics/runs/{run_id}/report"),
}

CATALOGUE_ROUTES = {
    ("POST", "/api/v2/assets/{asset_id}/select"),
    ("GET", "/api/v2/assets/{asset_id}/versions/{version_a}/diff/{version_b}"),
    ("GET", "/api/v2/catalogue"),
    ("GET", "/api/v2/catalogue/{asset_id}"),
}

FRAMEWORK_ROUTES = {
    ("GET", "/api/v2/items/{item_id}/results"),
    ("GET", "/api/v2/items/{item_id}/report"),
    ("GET", "/api/v2/framework/overview"),
    ("GET", "/api/v2/framework/areas"),
    ("GET", "/api/v2/framework/tests"),
    ("GET", "/api/v2/framework/matrix"),
    ("GET", "/api/v2/agents"),
}


def test_sourcing_routes_are_mounted_once_on_v2_router():
    from routers import sourcing, v2

    app = FastAPI()
    app.include_router(v2.router)
    openapi_paths = app.openapi()["paths"]

    assert all(
        method.lower() in openapi_paths[path]
        for method, path in SOURCING_ROUTES
    )
    included = [
        route for route in v2.router.routes
        if getattr(route, "original_router", None) is sourcing.router
    ]
    assert len(included) == 1


def test_issue_routes_are_mounted_once_on_v2_router():
    from routers import issues, v2

    app = FastAPI()
    app.include_router(v2.router)
    openapi_paths = app.openapi()["paths"]

    assert all(
        method.lower() in openapi_paths[path]
        for method, path in ISSUE_ROUTES
    )
    included = [
        route for route in v2.router.routes
        if getattr(route, "original_router", None) is issues.router
    ]
    assert len(included) == 1


def test_diagnostic_routes_are_mounted_once_on_v2_router():
    from routers import diagnostics, v2

    app = FastAPI()
    app.include_router(v2.router)
    openapi_paths = app.openapi()["paths"]

    assert all(
        method.lower() in openapi_paths[path]
        for method, path in DIAGNOSTIC_ROUTES
    )
    included = [
        route for route in v2.router.routes
        if getattr(route, "original_router", None) is diagnostics.router
    ]
    assert len(included) == 1


def test_catalogue_routes_are_mounted_once_on_v2_router():
    from routers import asset_catalogue, v2

    app = FastAPI()
    app.include_router(v2.router)
    openapi_paths = app.openapi()["paths"]

    assert all(
        method.lower() in openapi_paths[path]
        for method, path in CATALOGUE_ROUTES
    )
    included = [
        route for route in v2.router.routes
        if getattr(route, "original_router", None) is asset_catalogue.router
    ]
    assert len(included) == 1


def test_framework_routes_are_mounted_once_on_v2_router():
    from routers import framework, v2

    app = FastAPI()
    app.include_router(v2.router)
    openapi_paths = app.openapi()["paths"]

    assert all(
        method.lower() in openapi_paths[path]
        for method, path in FRAMEWORK_ROUTES
    )
    included = [
        route for route in v2.router.routes
        if getattr(route, "original_router", None) is framework.router
    ]
    assert len(included) == 1


def test_v2_module_keeps_existing_python_imports_available():
    from routers import asset_catalogue, diagnostics, framework, issues, sourcing, v2

    assert v2.create_item is sourcing.create_item
    assert v2.ProcessSnapshotIn is sourcing.ProcessSnapshotIn
    assert v2.raise_issue is issues.raise_issue
    assert v2.RaiseIssueIn is issues.RaiseIssueIn
    assert v2.diagnostics_board is diagnostics.diagnostics_board
    assert v2.ManifestIn is diagnostics.ManifestIn
    assert v2.abandon_upload is sourcing.abandon_upload
    assert v2.catalogue is asset_catalogue.catalogue
    assert v2.version_diff is asset_catalogue.version_diff
    assert v2.framework_overview is framework.framework_overview
    assert v2.item_report is framework.item_report
