"""Test Lab and diagnostics routes for API v2."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ai.v2 import service
from background_streams import observe_background_events
from routers.v2_common import sanitize_internal_errors as _plt04_sanitize
from routers.v2_common import stream_events as _sse
import tenancy

router = APIRouter()
logger = logging.getLogger(__name__)


def _diagnostic_principal(authorization) -> dict:
    """Resolve HTTP callers while keeping direct service tests explicit.

    FastAPI supplies ``None`` or the header string. Calling a decorated route
    directly leaves its ``Header`` parameter object in place; that internal
    path retains the historical bootstrap/system principal.
    """
    if authorization is not None and not isinstance(authorization, str):
        return {"username": "system", "tenant_id": "bootstrap", "authz_roles": []}
    return tenancy.resolve_principal(authorization)


def _require_governed_tenant(run: dict, principal: dict) -> None:
    tenant_id = str((run.get("manifest_json") or {}).get("tenant_id") or "bootstrap")
    if tenant_id != principal["tenant_id"]:
        raise KeyError("Unknown diagnostic run")


def _require_d11_tenant(run: dict, principal: dict) -> None:
    """Backward-compatible name retained for existing direct tests."""
    _require_governed_tenant(run, principal)


def _diag_err(exc: Exception):
    """Map deliberate diagnostics refusals onto their public status codes."""
    from dq_diagnostics.engines.base import EngineNotImplementedError
    from domains.test_lab.shared.run_state import ManifestError, RunAlreadyExecuting
    from dq_diagnostics.register import WorkflowPendingError

    if isinstance(exc, WorkflowPendingError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, RunAlreadyExecuting):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    if isinstance(exc, (ManifestError, EngineNotImplementedError, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


class ManifestIn(BaseModel):
    diagnostic_id: int
    use_case: str | None = None
    current_snapshot_id: str | None = None
    start_afresh: bool = False


class ManifestPatch(BaseModel):
    kind: str
    role: str | None = None
    column: str | None = None
    key: str | None = None
    value: object | None = None
    table: str | None = None
    reason: str | None = None
    enabled: bool | None = None
    features: list[str] | None = None
    feature: str | None = None
    orientation: str | None = None
    segment_column: str | None = None
    target_type: str | None = None
    positive_class: object | None = None
    expected_direction: str | None = None
    rationale: str | None = None
    canonical_feature: str | None = None
    representation_orientation: str | None = None
    include_in_kb: bool | None = None
    limit: int | None = None
    baseline: dict | None = None
    current: dict | None = None
    special_policy: str | None = None


class RunIn(BaseModel):
    stream: bool = False


class DispositionIn(BaseModel):
    action: str
    reason: str | None = None
    overwrite_existing: bool = False


class BinningReviewIn(BaseModel):
    definition: dict
    scope: str = "diagnostic_specific"
    confirm_universal: bool = False


class NumericIvOverrideIn(BaseModel):
    cuts: str
    special_values: str = ""
    rationale: str = ""
    scope: str = "diagnostic_specific"
    confirm_universal: bool = False


class PsiBinFreezeIn(BaseModel):
    draft_artifact_id: str


class PsiBinBatchApprovalIn(BaseModel):
    features: list[str]


class PsiManualBinsIn(BaseModel):
    groups: list[dict]


class PsiNumericBinsIn(BaseModel):
    cuts: str
    special_values: str = ""
    rationale: str = ""


class PsiBinPromotionIn(BaseModel):
    draft_artifact_id: str


class PsiBinRevisionIn(BaseModel):
    artifact_id: str
    definition: dict


class PsiResultPromotionIn(BaseModel):
    reason: str | None = None
    overwrite_existing: bool = False


def _run_history_entry(run: dict, store) -> dict:
    results = store.query("diag_results", run_id=run["run_id"])
    summary = next((result for result in results
                    if (result.get("metrics_json") or {}).get("result_kind")
                    in {"run_summary", "psi_run_summary"}), None)
    if summary is None:
        summary = next((result for result in results if (result.get("metrics_json") or {}).get("rollup") is not None), None)
    summary = summary or (results[0] if results else None)
    metrics = (summary or {}).get("metrics_json") or {}
    return {"run_id": run["run_id"], "status": run["status"],
        "created_at": run.get("created_at"), "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"), "heartbeat_at": run.get("heartbeat_at"),
        "status_detail": run.get("status_detail_json"),
        "verdict": (summary or {}).get("verdict"),
        "rollup": metrics.get("rollup"), "result_count": len(results),
        "has_results": bool(results),
        "manifest_fingerprint": (run.get("manifest_json") or {}).get("manifest_fingerprint")}


def _diagnostic_run_history(item_id: str, diagnostic_id: int, store,
                            entry_limit: int | None = None,
                            tenant_id: str | None = None) -> dict:
    runs = [run for run in store.query(
        "diag_runs", item_id=item_id, diagnostic_id=diagnostic_id,
        order_by="created_at DESC, run_id DESC",
    ) if run["status"] != "discarded" and (
        tenant_id is None
        or str((run.get("manifest_json") or {}).get("tenant_id") or "bootstrap") == tenant_id
    )]
    counts: dict[str, int] = {}
    for run in runs:
        counts[run["status"]] = counts.get(run["status"], 0) + 1
    selected = runs if entry_limit is None else runs[:entry_limit]
    entries = [_run_history_entry(run, store) for run in selected]
    latest_done_run = next((run for run in runs if run["status"] == "done"), None)
    latest_completed = next((entry for entry in entries if entry["status"] == "done"), None)
    if latest_completed is None and latest_done_run is not None:
        latest_completed = _run_history_entry(latest_done_run, store)
    return {"total": len(runs), "counts": counts, "latest_completed": latest_completed,
            "runs": entries}


def _latest_diagnostic_draft(item_id: str, diagnostic_id: int,
                             principal: dict) -> dict | None:
    """Resolve the diagnostic-specific draft contract behind one shared API."""
    from domains.test_lab.shared import run_state

    if diagnostic_id in {8, 11}:
        from dq_diagnostics.dispatch import adapter
        manifest_mod = adapter(diagnostic_id)["manifest"]
        return manifest_mod.latest_draft(
            item_id, tenant_id=principal["tenant_id"],
            actor=principal["username"],
        )
    if diagnostic_id == 14:
        from dq_diagnostics.dispatch import adapter
        manifest_mod = adapter(diagnostic_id)["manifest"]
        return manifest_mod.latest_draft(item_id)
    return run_state.latest_draft(
        item_id, diagnostic_id, actor=principal["username"],
    )


def _discard_diagnostic_drafts(item_id: str, diagnostic_id: int,
                               principal: dict) -> int:
    """Archive open drafts using custom cleanup where a diagnostic requires it."""
    from domains.test_lab.shared import run_state

    if diagnostic_id in {8, 11}:
        from dq_diagnostics.dispatch import adapter
        manifest_mod = adapter(diagnostic_id)["manifest"]
        return manifest_mod.discard_drafts(
            item_id, actor=principal["username"],
            tenant_id=principal["tenant_id"],
        )
    if diagnostic_id == 14:
        from dq_diagnostics.dispatch import adapter
        manifest_mod = adapter(diagnostic_id)["manifest"]
        return manifest_mod.discard_drafts(
            item_id, actor=principal["username"],
        )
    return run_state.discard_drafts(
        item_id, diagnostic_id, actor=principal["username"],
    )


def _board_summary(item_id: str, store, register_mod) -> dict:
    """Return the inexpensive, dataset-independent shape of the board."""
    item = service.require_item(item_id)
    areas = {a["area_id"]: a for a in store.query("framework_test_areas")}
    rows = register_mod.list_register()
    coverage = register_mod.coverage_map()
    return {
        "item_id": item_id,
        "item": {"item_id": item["item_id"], "name": item.get("name"),
                 "kind": item.get("kind"), "use_case": item.get("use_case"),
                 "ingest_status": item.get("ingest_status")},
        "cards": [{
            "diagnostic_id": row["diagnostic_id"],
            "name": row["name"],
            "area": row["area"],
            "area_name": (areas.get(row["area"]) or {}).get("name"),
            "mode": row["mode"],
            "stage": row["stage"],
            "det_stat": row["det_stat"],
            "decision_type": row["decision_type"],
            "kb_dependency": row["kb_dependency"],
            "metric": row["metric"],
            "what_it_computes": row["what_it_computes"],
            "workflow_status": row["workflow_status"],
            "l2_areas": row["l2_areas_json"] or [],
            "enabled_by": row["enabled_by"],
            "loading": True,
        } for row in rows],
        "gap_areas": [a for a in coverage["areas"] if a["status"] != "covered"],
        "coverage": {"executable": coverage["executable"],
                     "workflow_pending": coverage["workflow_pending"]},
    }


def _board_card(item_id: str, row: dict, areas: dict, principal: dict, store) -> dict:
    """Resolve one diagnostic's readiness, draft, and result history."""
    from dq_diagnostics import readiness as readiness_mod
    from dq_diagnostics.register import REFUSAL_WORKFLOW_PENDING, WorkflowPendingError

    did = row["diagnostic_id"]
    try:
        state = readiness_mod.readiness(item_id, did, principal["tenant_id"])
        chip = state.to_dict()
    except WorkflowPendingError:
        chip = {"status": "workflow_pending", "reason": REFUSAL_WORKFLOW_PENDING, "detail": {}}
    open_draft = _latest_diagnostic_draft(item_id, did, principal)
    history = {"total": 0, "counts": {}, "latest_completed": None, "runs": []}
    if row["workflow_status"] == "executable":
        history = _diagnostic_run_history(
            item_id, did, store, entry_limit=5,
            tenant_id=principal["tenant_id"] if did in {8, 11} else None,
        )
    return {
        "diagnostic_id": did,
        "name": row["name"],
        "area": row["area"],
        "area_name": (areas.get(row["area"]) or {}).get("name"),
        "mode": row["mode"],
        "stage": row["stage"],
        "det_stat": row["det_stat"],
        "decision_type": row["decision_type"],
        "kb_dependency": row["kb_dependency"],
        "metric": row["metric"],
        "what_it_computes": row["what_it_computes"],
        "workflow_status": row["workflow_status"],
        "l2_areas": row["l2_areas_json"] or [],
        "enabled_by": row["enabled_by"],
        "chip": chip,
        "can_run": chip["status"] == "ready",
        "last_run": history["latest_completed"],
        "run_count": history["total"],
        "run_counts": history["counts"],
        "recent_runs": history["runs"][:5],
        "open_draft": open_draft,
        "loading": False,
    }


@router.get("/items/{item_id}/diagnostics/board/summary")
@_plt04_sanitize
def diagnostics_board_summary(item_id: str,
                              authorization: str | None = Header(default=None)):
    """Fast board shell; card-specific state is fetched independently."""
    import system_db as _s
    from dq_diagnostics import register as register_mod
    _diagnostic_principal(authorization)
    try:
        return _board_summary(item_id, _s, register_mod)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/items/{item_id}/diagnostics/board/cards/{diagnostic_id}")
@_plt04_sanitize
def diagnostics_board_card(item_id: str, diagnostic_id: int,
                           authorization: str | None = Header(default=None)):
    """Return one card so the UI can reveal diagnostics as each is ready."""
    import system_db as _s
    from dq_diagnostics import register as register_mod
    principal = _diagnostic_principal(authorization)
    try:
        service.require_item(item_id)
        row = register_mod.get_diagnostic(diagnostic_id)
        areas = {a["area_id"]: a for a in _s.query("framework_test_areas")}
        return _board_card(item_id, row, areas, principal, _s)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/items/{item_id}/diagnostics/board")
@_plt04_sanitize
def diagnostics_board(item_id: str,
                      authorization: str | None = Header(default=None)):
    """The complete Coverage board (retained for API compatibility)."""
    import system_db as _s
    from dq_diagnostics import register as register_mod
    principal = _diagnostic_principal(authorization)
    try:
        summary = _board_summary(item_id, _s, register_mod)
        areas = {a["area_id"]: a for a in _s.query("framework_test_areas")}
        summary["cards"] = [
            _board_card(item_id, row, areas, principal, _s)
            for row in register_mod.list_register()
        ]
        return summary
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/items/{item_id}/diagnostics/{diagnostic_id}/runs")
@_plt04_sanitize
def diagnostic_run_history(item_id: str, diagnostic_id: int,
                           authorization: str | None = Header(default=None)):
    """Immutable run history for one diagnostic on one item, newest first."""
    import system_db as _s
    try:
        service.require_item(item_id)
        if not _s.query_one("diagnostic_register", diagnostic_id=diagnostic_id):
            raise KeyError(f"Unknown diagnostic: {diagnostic_id}")
        principal = _diagnostic_principal(authorization)
        return {"item_id": item_id, "diagnostic_id": diagnostic_id,
                **_diagnostic_run_history(
                    item_id, diagnostic_id, _s,
                    tenant_id=principal["tenant_id"] if diagnostic_id in {8, 11} else None,
                )}
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/items/{item_id}/diagnostics/manifest")
@_plt04_sanitize
def build_diagnostic_manifest(
    item_id: str, body: ManifestIn,
    authorization: str | None = Header(default=None),
):
    """Build (and persist as a DRAFT ``diag_runs`` row) the scope gate:
    rules in scope, resolved role map with score+reason, thresholds with
    their source, scope preview. Returns the manifest."""
    try:
        from dq_diagnostics.register import require_executable
        require_executable(body.diagnostic_id)
        from dq_diagnostics.dispatch import adapter
        selected = adapter(body.diagnostic_id)
        principal = _diagnostic_principal(authorization)
        if body.diagnostic_id not in {8, 11, 14}:
            if body.start_afresh:
                _discard_diagnostic_drafts(item_id, body.diagnostic_id, principal)
            else:
                saved = _latest_diagnostic_draft(
                    item_id, body.diagnostic_id, principal,
                )
                if saved:
                    from domains.test_lab.shared.run_state import get_manifest
                    return get_manifest(saved["run_id"])
        if body.diagnostic_id == 4:
            return selected["manifest"].build_manifest(item_id, body.diagnostic_id,
                actor="system", tenant_id="bootstrap", use_case_override=body.use_case)
        if body.diagnostic_id in {8, 11}:
            if body.start_afresh:
                selected["manifest"].discard_drafts(
                    item_id, actor=principal["username"],
                    tenant_id=principal["tenant_id"],
                )
            else:
                saved = selected["manifest"].latest_draft(
                    item_id, tenant_id=principal["tenant_id"],
                    actor=principal["username"],
                )
                if saved:
                    return selected["manifest"].refresh_draft_scope(
                        saved["run_id"], actor=principal["username"],
                        tenant_id=principal["tenant_id"],
                    )
        if body.diagnostic_id == 14:
            if body.start_afresh:
                selected["manifest"].discard_drafts(item_id, actor="system")
            elif selected["manifest"].latest_draft(item_id):
                from domains.test_lab.shared.run_state import ManifestError
                raise ManifestError(
                    "An open PSI draft already exists; explicitly resume it or start afresh."
                )
        if body.diagnostic_id == 14:
            return selected["manifest"].build_manifest(item_id, actor="system",
                current_snapshot_id=body.current_snapshot_id)
        if body.diagnostic_id in {8, 11}:
            return selected["manifest"].build_manifest(
                item_id, actor=principal["username"],
                tenant_id=principal["tenant_id"],
            )
        return selected["manifest"].build_manifest(item_id, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/items/{item_id}/diagnostics/{diagnostic_id}/draft")
@_plt04_sanitize
def resumable_diagnostic_draft(
    item_id: str, diagnostic_id: int,
    authorization: str | None = Header(default=None),
):
    """Discover an open setup before launch; never resumes it implicitly."""
    try:
        service.require_item(item_id)
        principal = _diagnostic_principal(authorization)
        return {"draft": _latest_diagnostic_draft(
            item_id, diagnostic_id, principal,
        )}
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.delete("/diagnostics/manifests/{run_id}/draft")
@_plt04_sanitize
def discard_diagnostic_draft(
    run_id: str,
    authorization: str | None = Header(default=None),
):
    """Archive one unfinished setup; completed and running runs are immutable."""
    try:
        from domains.test_lab.shared import run_state
        principal = _diagnostic_principal(authorization)
        run = run_state.get_run(run_id)
        service.require_item(run["item_id"])
        if run["diagnostic_id"] in {8, 11}:
            _require_governed_tenant(run, principal)
        return run_state.discard_draft(
            run_id, actor=principal["username"],
        )
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.patch("/diagnostics/manifests/{run_id}")
@_plt04_sanitize
def patch_diagnostic_manifest(
    run_id: str, body: ManifestPatch,
    authorization: str | None = Header(default=None),
):
    """One scope-gate edit -> one ``diag_run_decisions`` row (CFR-12).
    ``kind`` ∈ role_override · threshold_tune · scope_exclusion ·
    role_verification_change. Refused once the manifest is frozen."""
    try:
        from domains.test_lab.shared import run_state as manifest_mod
        run = manifest_mod.get_run(run_id)
        from dq_diagnostics.dispatch import adapter
        actor = "system"
        if run["diagnostic_id"] in {8, 11}:
            principal = _diagnostic_principal(authorization)
            _require_governed_tenant(run, principal)
            actor = principal["username"]
        return adapter(run["diagnostic_id"])["manifest"].patch_manifest(
            run_id, body.model_dump(exclude_none=True), actor=actor)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}")
@_plt04_sanitize
def get_diagnostic_manifest(
    run_id: str, authorization: str | None = Header(default=None),
):
    from domains.test_lab.shared import run_state as manifest_mod
    try:
        run = manifest_mod.get_run(run_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)
    principal = None
    if run["diagnostic_id"] in {8, 11}:
        principal = _diagnostic_principal(authorization)
        try:
            _require_governed_tenant(run, principal)
        except Exception as exc:  # noqa: BLE001
            _diag_err(exc)
    if run["diagnostic_id"] == 2 and run["status"] == manifest_mod.DRAFT:
        from domains.test_lab.diagnostics.t1_d02_feature_target_separation import manifest as feature_manifest
        run["manifest_json"] = feature_manifest.refresh_draft_scope(run_id)
    if run["diagnostic_id"] == 14 and run["status"] == manifest_mod.DRAFT:
        from domains.test_lab.diagnostics.t4_d14_population_stability import manifest as psi_manifest
        run["manifest_json"] = psi_manifest.refresh_draft_scope(run_id)
    if run["diagnostic_id"] == 11 and run["status"] == manifest_mod.DRAFT:
        from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency import manifest as direction_manifest
        run["manifest_json"] = direction_manifest.refresh_draft_scope(
            run_id, actor=principal["username"], tenant_id=principal["tenant_id"],
        )
    if run["diagnostic_id"] == 8 and run["status"] == manifest_mod.DRAFT:
        from domains.test_lab.diagnostics.t2_d08_value_semantics import manifest as semantics_manifest
        run["manifest_json"] = semantics_manifest.refresh_draft_scope(
            run_id, tenant_id=principal["tenant_id"],
        )
    return {"run": {k: run[k] for k in ("run_id", "item_id", "diagnostic_id", "status",
                                        "created_at", "started_at", "finished_at")},
            "manifest": run["manifest_json"],
            "decisions": manifest_mod.list_decisions(run_id)}


@router.post("/diagnostics/manifests/{run_id}/run")
@_plt04_sanitize
def run_diagnostic_manifest(
    run_id: str, body: RunIn | None = None,
    authorization: str | None = Header(default=None),
):
    """Freeze the manifest and execute.

    ``{"stream": true}`` freezes only and hands back the SSE URL — the
    stream then executes. The default (``stream`` false) runs synchronously
    and returns the final summary, so a non-streaming client still gets its
    results. Either way execution happens exactly once per run: the stream
    replays a completed run rather than re-running it.
    """
    from domains.test_lab.shared import run_state as manifest_mod
    body = body or RunIn()
    try:
        run = manifest_mod.get_run(run_id)
        diagnostic_id = run["diagnostic_id"]
        actor = "system"
        if diagnostic_id in {8, 11}:
            principal = _diagnostic_principal(authorization)
            _require_governed_tenant(run, principal)
            actor = principal["username"]
        # FWK-17 — refuse a pending diagnostic with the exact refusal text.
        from dq_diagnostics.register import require_executable
        require_executable(diagnostic_id)
        from dq_diagnostics.dispatch import adapter
        selected = adapter(diagnostic_id)
        runner, freeze, work_count = (
            selected["runner"], selected["manifest"].freeze, selected["work_count"]
        )
        # Freeze both streaming and synchronous launches at the shared API
        # boundary. This closes the former gap where a synchronous exception
        # could freeze inside a runner and leave the row stranded as RUNNING.
        if run["status"] == manifest_mod.DRAFT:
            if diagnostic_id == 2:
                from domains.test_lab.diagnostics.t1_d02_feature_target_separation import manifest as feature_manifest
                feature_manifest.refresh_draft_scope(run_id)
            if diagnostic_id == 11:
                from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency import manifest as direction_manifest
                direction_manifest.refresh_draft_scope(
                    run_id, actor=actor, tenant_id=principal["tenant_id"],
                )
            if diagnostic_id == 8:
                from domains.test_lab.diagnostics.t2_d08_value_semantics import manifest as semantics_manifest
                semantics_manifest.refresh_draft_scope(
                    run_id, tenant_id=principal["tenant_id"],
                )
            if diagnostic_id == 14:
                from domains.test_lab.diagnostics.t4_d14_population_stability import manifest as psi_manifest
                psi_manifest.refresh_draft_scope(run_id)
            manifest = freeze(run_id, actor=actor)
        else:
            manifest = run["manifest_json"]
        if body.stream:
            return {"run_id": run_id, "status": "running",
                    "stream_url": f"/api/v2/diagnostics/runs/{run_id}/stream",
                    "rules": work_count(manifest)}
        if run["status"] == manifest_mod.DONE:
            return runner.execute_now(run_id, actor=actor)
        with manifest_mod.managed_run_execution(
            run_id, channel="synchronous", actor=actor,
        ) as lease:
            lease.heartbeat()
            return runner.execute_now(run_id, actor=actor)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/draft")
@_plt04_sanitize
def create_psi_bin_draft(run_id: str, feature: str, generate_new: bool = False):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import create_bin_draft
        return create_bin_draft(run_id, feature, actor="system", ignore_repository_match=generate_new)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}/psi-bins/draft-stream")
@_plt04_sanitize
def create_psi_bin_drafts_stream(run_id: str):
    """Generate outstanding PSI drafts in a bounded, resumable server-side batch."""
    from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import generate_bin_draft_events
    events = observe_background_events(
        f"psi-bin-drafts:{run_id}",
        lambda: generate_bin_draft_events(run_id, actor="system"),
    )
    return _sse(events)


@router.get("/diagnostics/manifests/{run_id}/psi-bins/{feature}/review")
@_plt04_sanitize
def review_psi_bins(run_id: str, feature: str, artifact_id: str | None = None):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import bin_review
        return bin_review(run_id, feature, artifact_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/revise")
@_plt04_sanitize
def revise_psi_bins(run_id: str, feature: str, body: PsiBinRevisionIn):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import revise_from_fine
        return revise_from_fine(run_id, feature, body.artifact_id, body.definition, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/preview")
@_plt04_sanitize
def preview_psi_bin_revision(run_id: str, feature: str, body: PsiBinRevisionIn):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import preview_fine_revision
        return preview_fine_revision(run_id, feature, body.artifact_id, body.definition)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}/psi-split-options/{feature}")
@_plt04_sanitize
def psi_split_feature_options(run_id: str, feature: str, limit: int = 200):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import split_feature_options
        return split_feature_options(run_id, feature, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}/directionality-split-options/{feature}")
@_plt04_sanitize
def directionality_split_feature_options(run_id: str, feature: str, limit: int = 200):
    """PSI-compatible split controls for a segmented D11 rerun."""
    try:
        from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency.manifest import split_feature_options
        return split_feature_options(run_id, feature, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/freeze")
@_plt04_sanitize
def freeze_psi_bin_draft(run_id: str, feature: str, body: PsiBinFreezeIn):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import freeze_bin_draft
        return freeze_bin_draft(run_id, feature, body.draft_artifact_id, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/approve-batch")
@_plt04_sanitize
def approve_psi_bins_batch(run_id: str, body: PsiBinBatchApprovalIn):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import approve_bin_batch
        return approve_bin_batch(run_id, body.features, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}/psi-bins/{feature}/candidate-values")
@_plt04_sanitize
def psi_candidate_values(run_id: str, feature: str):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import candidate_values
        return candidate_values(run_id, feature)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/manual")
@_plt04_sanitize
def create_manual_psi_bin_draft(run_id: str, feature: str, body: PsiManualBinsIn):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import create_manual_bin_draft
        return create_manual_bin_draft(run_id, feature, body.groups, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/numeric-override")
@_plt04_sanitize
def create_numeric_psi_bin_override(run_id: str, feature: str, body: PsiNumericBinsIn):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import create_numeric_bin_override
        return create_numeric_bin_override(run_id, feature, body.cuts, body.special_values,
                                           body.rationale, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/promote-universal")
@_plt04_sanitize
def promote_psi_iv_bins(run_id: str, feature: str, body: PsiBinPromotionIn):
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.manifest import promote_psi_iv_bins as promote
        return promote(run_id, feature, body.draft_artifact_id, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/runs/{run_id}/stream")
@router.post("/diagnostics/runs/{run_id}/stream")
def stream_diagnostic_run(run_id: str):
    """CFR-13 — ``start`` -> ``progress`` (rules evaluated / total) -> ``done``.

    An error surfaces as a sanitized ``error`` frame (PLT-04): the real
    exception is logged server-side and never travels to the client.
    """
    from domains.test_lab.shared import run_state as manifest_mod
    import system_db as _s

    # Governed diagnostics' authenticated JSON launch is the only operation
    # allowed to freeze their manifests. Native
    # EventSource requests cannot carry that bearer header, so this endpoint is
    # deliberately an observer only once the launch has moved the run out of
    # DRAFT.  Keep the legacy stream-start behavior for other diagnostics.
    requested_run = _s.query_one("diag_runs", run_id=run_id)
    if (requested_run and requested_run["diagnostic_id"] in {8, 11}
            and requested_run["status"] == manifest_mod.DRAFT):
        raise HTTPException(
            status_code=409,
            detail=(
                "Launch this governed diagnostic through the authenticated "
                "run action before opening its event stream."
            ),
        )

    def events():
        try:
            run = manifest_mod.get_run(run_id)
        except KeyError:
            yield {"phase": "error", "agent": "cross_field_engine",
                   "thought": f"Unknown run: {run_id}"}
            return
        try:
            from dq_diagnostics.dispatch import adapter
            selected = adapter(run["diagnostic_id"])
            runner, agent = selected["runner"], selected["agent"]
            if run["status"] == manifest_mod.DONE:
                results = _s.query("diag_results", run_id=run_id)
                summary = next((row for row in results
                                if (row.get("metrics_json") or {}).get("result_kind") == "run_summary"),
                               results[0] if results else None)
                rollup = (summary["metrics_json"] or {}).get("rollup") if summary else {}
                yield {"phase": "start", "agent": agent, "run_id": run_id,
                       "thought": "Replaying a completed run (results are already persisted)."}
                yield {"phase": "done", "agent": agent, "run_id": run_id,
                       "result_id": summary["result_id"] if summary else None,
                       "verdict": summary["verdict"] if summary else None,
                       "rollup": rollup, "thought": "Run already complete."}
                return
            manifest = run.get("manifest_json") or {}
            actor = manifest.get("frozen_by") or manifest.get("created_by") or "system"
            with manifest_mod.managed_run_execution(
                run_id, channel="stream", actor=actor,
            ) as lease:
                for event in runner.run(run_id, actor=actor):
                    if manifest_mod.get_run(run_id)["status"] == manifest_mod.RUNNING:
                        lease.heartbeat()
                    if event.get("phase") == "error":
                        manifest_mod.mark_run_failed(
                            run_id, "execution_failed", actor, owner=lease.owner,
                        )
                    yield event
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled error while streaming diagnostic run %s", run_id)
            yield {"phase": "error", "agent": locals().get("agent", "diagnostic_engine"),
                   "thought": "internal error"}
    # Execution is process-owned.  The browser is only an observer: closing or
    # reconnecting EventSource cannot abandon persistence or final run status.
    observed = observe_background_events(f"diagnostic-run:{run_id}", events)
    return _sse(observed)


@router.get("/items/{item_id}/diagnostics/results")
@_plt04_sanitize
def diagnostic_results(item_id: str, run_id: str | None = None,
                       diagnostic_id: int | None = None,
                       authorization: str | None = Header(default=None)):
    """Decision-type-shaped results + per-rule findings for display."""
    import system_db as _s
    try:
        service.require_item(item_id)
        if not run_id:
            filters = {"item_id": item_id, "status": "done"}
            if diagnostic_id is not None:
                filters["diagnostic_id"] = diagnostic_id
            rows = _s.query("diag_runs", **filters, order_by="finished_at DESC")
            principal = _diagnostic_principal(authorization)
            rows = [row for row in rows if (
                row["diagnostic_id"] not in {8, 11}
                or str((row.get("manifest_json") or {}).get("tenant_id") or "bootstrap")
                == principal["tenant_id"]
            )]
            latest = rows[0] if rows else None
            if not latest:
                return {"item_id": item_id, "run": None, "results": [], "decisions": [],
                        "manifest": None}
            run_id = latest["run_id"]
        from dq_diagnostics.dispatch import adapter
        source_run = _s.query_one("diag_runs", run_id=run_id)
        if source_run and source_run["diagnostic_id"] in {8, 11}:
            _require_governed_tenant(source_run, _diagnostic_principal(authorization))
        runner = adapter(source_run["diagnostic_id"])["runner"]
        from assets.staleness import annotate_payload
        payload = annotate_payload(runner.run_results(run_id))
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)
    return {"item_id": item_id, **payload}


@router.post("/diagnostics/findings/{finding_id}/disposition")
@_plt04_sanitize
def disposition_finding(finding_id: str, body: DispositionIn):
    """The SME decision (human decision #2). ``dismiss`` REQUIRES a reason —
    enforced here, not by a disabled button."""
    import system_db as _s
    if body.action not in {"confirm_issue", "dismiss"}:
        raise HTTPException(status_code=400, detail="action must be confirm_issue or dismiss")
    if not (body.reason or "").strip():
        raise HTTPException(status_code=400, detail="a rationale is required for a diagnostic disposition")
    finding = _s.query_one("diag_findings", finding_id=finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail=f"Unknown finding: {finding_id}")
    source_run = _s.query_one("diag_runs", run_id=finding["run_id"])
    row_completeness_decision = None
    issue_row_id = None
    if source_run and source_run.get("diagnostic_id") == 6:
        from dq_diagnostics.result_promotion import dispose_row_completeness_finding
        try:
            row_completeness_decision = dispose_row_completeness_finding(
                finding_id, body.action, body.reason or "", actor="system")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        issue_row_id = row_completeness_decision.get("issue_row_id")
    elif body.action == "confirm_issue":
        if source_run and source_run.get("diagnostic_id") == 2:
            from domains.test_lab.diagnostics.t1_d02_feature_target_separation.runner import (
                CandidateIssueConflict, ensure_candidate_issue,
            )
            try:
                issue_row_id = ensure_candidate_issue(
                    finding_id, overwrite=body.overwrite_existing)
            except CandidateIssueConflict as exc:
                raise HTTPException(status_code=409, detail={
                    "message": str(exc), "issue_row_id": exc.issue_row_id,
                    "overwrite_required": True,
                }) from exc
        elif source_run and source_run.get("diagnostic_id") == 14:
            from domains.test_lab.diagnostics.t4_d14_population_stability.runner import ensure_contextual_issue
            issue_row_id = ensure_contextual_issue(finding_id)
        else:
            # PASS / NOT-APPLICABLE findings are not promoted merely because
            # the low-level disposition route was called. Their supported
            # override path first creates a reason-bearing operator finding.
            if finding.get("outcome") in {"VIOLATION", "CANDIDATE", "CONTEXTUAL"} \
                    or finding.get("pattern") == "operator_override":
                from dq_diagnostics.result_promotion import ensure_generic_issue
                issue_row_id = ensure_generic_issue(finding_id)
    ts = (row_completeness_decision or {}).get("ts") or _s.now_ist()
    if row_completeness_decision is None:
        _s.insert("diag_dispositions", {
            "target_type": "finding", "target_id": finding_id, "action": body.action,
            "reason": (body.reason or "").strip() or None, "actor": "system", "ts": ts,
        })
    from analytics.events import event_object_id, record_event
    finding_rows = _s.execute(
        "SELECT i.*, dr.diagnostic_id AS source_diagnostic_id FROM dq_items i JOIN diag_runs dr ON dr.item_id=i.item_id "
        "JOIN diag_results r ON r.run_id=dr.run_id JOIN diag_findings f ON f.result_id=r.result_id "
        "WHERE f.finding_id=?",
        [finding_id])
    finding_item = finding_rows[0] if finding_rows else None
    if finding_item and not (row_completeness_decision or {}).get("idempotent"):
        asset = _s.query_one("dq_assets", asset_id=finding_item.get("dataset_family_id")) or {}
        record_event(event_type="finding_disposed", actor="system", at=ts,
                     object_type="snapshot", object_id=event_object_id("snapshot", finding_item["item_id"]),
                     workflow_context=asset.get("system_id"),
                     detail={"disposition": body.action,
                             "diagnostic_id": finding_item.get("source_diagnostic_id")})
    review_state = "confirmed" if body.action == "confirm_issue" else "dismissed"
    if row_completeness_decision is None:
        _s.update("diag_findings", {"finding_id": finding_id}, {"review_state": review_state})
    if row_completeness_decision is not None:
        return {**row_completeness_decision, "ts": ts}
    return {"finding_id": finding_id, "action": body.action, "review_state": review_state,
            "issue_row_id": issue_row_id,
            "reason": (body.reason or "").strip() or None, "ts": ts}


@router.post("/diagnostics/results/{result_id}/psi-promotion")
@_plt04_sanitize
def promote_psi_result(result_id: str, body: PsiResultPromotionIn):
    """Allow an SME to promote any evaluated PSI feature, including a stable override."""
    try:
        from domains.test_lab.diagnostics.t4_d14_population_stability.runner import ensure_contextual_finding
        finding_id = ensure_contextual_finding(result_id, reason=body.reason, actor="system")
        return disposition_finding(finding_id, DispositionIn(
            action="confirm_issue", reason=body.reason))
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/results/{result_id}/feature-target-promotion")
@_plt04_sanitize
def promote_feature_target_result(result_id: str, body: PsiResultPromotionIn):
    """Allow an SME to override Diagnostic 2's recommendation for any assessed feature."""
    try:
        from domains.test_lab.diagnostics.t1_d02_feature_target_separation.runner import ensure_candidate_finding
        finding_id = ensure_candidate_finding(result_id, reason=body.reason, actor="system")
        return disposition_finding(finding_id, DispositionIn(
            action="confirm_issue", reason=body.reason,
            overwrite_existing=body.overwrite_existing))
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/results/{result_id}/promotion")
@_plt04_sanitize
def promote_diagnostic_result(result_id: str, body: PsiResultPromotionIn):
    """Shared override route for every non-summary diagnostic result."""
    import system_db as _s
    try:
        result = _s.query_one("diag_results", result_id=result_id)
        if not result: raise KeyError(f"Unknown diagnostic result: {result_id}")
        if result["diagnostic_id"] == 2:
            from domains.test_lab.diagnostics.t1_d02_feature_target_separation.runner import ensure_candidate_finding
            finding_id = ensure_candidate_finding(result_id, reason=body.reason, actor="system")
        elif result["diagnostic_id"] == 14:
            from domains.test_lab.diagnostics.t4_d14_population_stability.runner import ensure_contextual_finding
            finding_id = ensure_contextual_finding(result_id, reason=body.reason, actor="system")
        else:
            from dq_diagnostics.result_promotion import ensure_override_finding
            finding_id = ensure_override_finding(result_id, reason=body.reason, actor="system")
        return disposition_finding(finding_id, DispositionIn(
            action="confirm_issue", reason=body.reason,
            overwrite_existing=body.overwrite_existing))
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/results/{result_id}/binning-impact")
@_plt04_sanitize
def diagnostic_binning_impact(result_id: str):
    from domains.test_lab.diagnostics.t1_d02_feature_target_separation.binning_reviews import impact
    try:
        return impact(result_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/results/{result_id}/binning-preview")
@_plt04_sanitize
def preview_diagnostic_binning(result_id: str, body: BinningReviewIn):
    from domains.test_lab.diagnostics.t1_d02_feature_target_separation.binning_reviews import preview
    try:
        return preview(result_id, body.definition)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/results/{result_id}/binning-review")
@_plt04_sanitize
def review_diagnostic_binning(result_id: str, body: BinningReviewIn):
    from domains.test_lab.diagnostics.t1_d02_feature_target_separation.binning_reviews import review
    try:
        return review(result_id, body.definition, body.scope,
                      confirm_universal=body.confirm_universal, actor="system")
    except RuntimeError as exc:
        if hasattr(exc, "impact"):
            raise HTTPException(status_code=409, detail={
                "message": str(exc), "confirmation_required": True,
                "impact": exc.impact,
            }) from exc
        _diag_err(exc)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/results/{result_id}/numeric-binning-override")
@_plt04_sanitize
def create_numeric_diagnostic_binning_override(result_id: str, body: NumericIvOverrideIn):
    from domains.test_lab.diagnostics.t1_d02_feature_target_separation.binning_reviews import create_numeric_override
    try:
        return create_numeric_override(
            result_id, body.cuts, body.special_values, body.rationale, body.scope,
            confirm_universal=body.confirm_universal, actor="system",
        )
    except RuntimeError as exc:
        if hasattr(exc, "impact"):
            raise HTTPException(status_code=409, detail={
                "message": str(exc), "confirmation_required": True,
                "impact": exc.impact,
            }) from exc
        _diag_err(exc)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/items/{item_id}/diagnostics/coverage-summary")
@_plt04_sanitize
def diagnostics_coverage_summary(item_id: str):
    """FWK-16 — coverage-honest roll-up. There is deliberately NO weighted
    health-score field on this payload (not zero, not null) while fewer than
    two core diagnostics are executable."""
    from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import runner
    try:
        service.require_item(item_id)
        return runner.coverage_summary(item_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/runs/{run_id}/report")
@_plt04_sanitize
def diagnostic_run_report(run_id: str, fmt: str = "pdf",
                          authorization: str | None = Header(default=None)):
    """The report, derived from the structured result (CFR-10) — never a
    second source of truth."""
    try:
        import system_db as _s
        from dq_diagnostics.dispatch import adapter
        run = _s.query_one("diag_runs", run_id=run_id)
        if not run: raise KeyError(f"Unknown run: {run_id}")
        if run["diagnostic_id"] in {8, 11}:
            _require_governed_tenant(run, _diagnostic_principal(authorization))
        runner = adapter(run["diagnostic_id"])["runner"]
        if fmt == "text":
            return Response(content=runner.report_text(run_id), media_type="text/plain")
        report_metadata = None
        if run["diagnostic_id"] in {2, 6, 8, 11, 14} and hasattr(runner, "report_document"):
            payload, report_metadata = runner.report_document(run_id)
        else:
            payload = runner.report_pdf(run_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)
    filename = (
        f"row-completeness-{run_id}.pdf"
        if run["diagnostic_id"] == 6
        else f"feature-target-separation-{run_id}.pdf"
        if run["diagnostic_id"] == 2
        else f"value-semantics-{run_id}.pdf"
        if run["diagnostic_id"] == 8
        else f"directional-monotonic-consistency-{run_id}.pdf"
        if run["diagnostic_id"] == 11
        else f"population-stability-{run_id}.pdf"
        if run["diagnostic_id"] == 14
        else f"cross-field-{run_id}.pdf"
    )
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if report_metadata:
        headers["X-Analysis-Artifact-Id"] = report_metadata["report_artifact"]["artifact_id"]
        headers["X-Analysis-Artifact-Hash"] = report_metadata["report_artifact"]["payload_hash"]
    return Response(content=payload, media_type="application/pdf", headers=headers)
