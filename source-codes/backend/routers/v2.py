"""Galileo v2 API contract."""
from __future__ import annotations

from fastapi import APIRouter
from routers import asset_catalogue, diagnostics, framework, issues, sourcing
from routers.asset_catalogue import catalogue, catalogue_asset, select_asset, version_diff
from routers.diagnostics import (
    BinningReviewIn,
    NumericIvOverrideIn,
    DispositionIn,
    ManifestIn,
    ManifestPatch,
    RunIn,
    _diag_err,
    build_diagnostic_manifest,
    diagnostic_binning_impact,
    create_numeric_diagnostic_binning_override,
    diagnostic_results,
    diagnostic_run_history,
    diagnostic_run_report,
    diagnostics_board,
    diagnostics_board_card,
    diagnostics_board_summary,
    diagnostics_coverage_summary,
    disposition_finding,
    get_diagnostic_manifest,
    patch_diagnostic_manifest,
    preview_diagnostic_binning,
    review_diagnostic_binning,
    run_diagnostic_manifest,
    stream_diagnostic_run,
)
from routers.framework import (
    agents,
    framework_areas,
    framework_matrix,
    framework_overview,
    framework_tests,
    get_results,
    item_report,
)
from routers.issues import (
    AnalysesIn,
    CloseIssueIn,
    RaiseIssueIn,
    TrackedPatch,
    close_issue,
    create_issue_analyses,
    interpret_issue_analyses,
    issue_detail,
    issues_register,
    item_issues,
    patch_tracked_issue,
    raise_issue,
    rca_stream,
)
from routers.sourcing import (
    DictionaryReviewIn,
    FinalizeIn,
    ItemIn,
    ItemPatch,
    ProcessSnapshotIn,
    ReuploadIn,
    UploadTargetIn,
    abandon_upload,
    create_item,
    create_upload_target,
    finalize_item,
    get_inventory,
    inspect_source,
    item_columns,
    item_ingest,
    item_tables,
    list_assets,
    list_items,
    next_asset_id,
    patch_item,
    process_snapshot,
    profile_stream,
    put_inventory,
    refresh_asset,
    restore_version,
    reupload_item,
    review_dictionary,
    snapshot_summary,
    supersede_preview,
    upload_file,
    upload_source_bundle,
)
router = APIRouter(prefix="/api/v2", tags=["galileo-v2"])
router.include_router(sourcing.router)
router.include_router(issues.router)
router.include_router(diagnostics.router)
router.include_router(asset_catalogue.router)
router.include_router(framework.router)

