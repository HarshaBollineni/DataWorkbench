"""Test Lab and diagnostics routes for API v2."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ai.v2 import service
from background_streams import observe_background_events
from routers.v2_common import sanitize_internal_errors as _plt04_sanitize
from routers.v2_common import stream_events as _sse

router = APIRouter()
logger = logging.getLogger(__name__)


def _diag_err(exc: Exception):
    """Map deliberate diagnostics refusals onto their public status codes."""
    from dq_diagnostics.engines.base import EngineNotImplementedError
    from dq_diagnostics.manifest import ManifestError
    from dq_diagnostics.register import WorkflowPendingError

    if isinstance(exc, WorkflowPendingError):
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
        "finished_at": run.get("finished_at"), "verdict": (summary or {}).get("verdict"),
        "rollup": metrics.get("rollup"), "result_count": len(results),
        "has_results": bool(results),
        "manifest_fingerprint": (run.get("manifest_json") or {}).get("manifest_fingerprint")}


def _diagnostic_run_history(item_id: str, diagnostic_id: int, store,
                            entry_limit: int | None = None) -> dict:
    runs = store.query("diag_runs", item_id=item_id, diagnostic_id=diagnostic_id,
                       order_by="created_at DESC, run_id DESC")
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


@router.get("/items/{item_id}/diagnostics/board")
@_plt04_sanitize
def diagnostics_board(item_id: str):
    """The Coverage board: EXACTLY one card per register row (9, D-17) with
    its chip and reason, plus the area-level GAP strip (FWK-14).

    ``chip.status`` is one of ``ready`` · ``not_applicable`` · ``blocked`` ·
    ``workflow_pending``. ``can_run`` is false for everything but ``ready``,
    so a pending diagnostic has no run affordance at all (6-T10).
    """
    import system_db as _s
    from dq_diagnostics import readiness as readiness_mod
    from dq_diagnostics import register as register_mod
    from dq_diagnostics.register import REFUSAL_WORKFLOW_PENDING, WorkflowPendingError

    try:
        item = service.require_item(item_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)

    areas = {a["area_id"]: a for a in _s.query("framework_test_areas")}
    from dq_diagnostics import manifest_population_stability as psi_manifest
    cards = []
    for row in register_mod.list_register():
        did = row["diagnostic_id"]
        try:
            state = readiness_mod.readiness(item_id, did, "bootstrap")
            chip = state.to_dict()
        except WorkflowPendingError:
            chip = {"status": "workflow_pending", "reason": REFUSAL_WORKFLOW_PENDING, "detail": {}}
        last = None; history = {"total": 0, "counts": {}, "latest_completed": None, "runs": []}
        if row["workflow_status"] == "executable":
            history = _diagnostic_run_history(item_id, did, _s, entry_limit=5)
            last = history["latest_completed"]
        cards.append({
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
            "last_run": last,
            "run_count": history["total"],
            "run_counts": history["counts"],
            "recent_runs": history["runs"][:5],
            # Draft discovery rides with the already-loaded Coverage board so
            # the resume/start-afresh choice can open without a click-time fetch.
            "open_draft": psi_manifest.latest_draft(item_id) if did == 14 else None,
        })
    coverage = register_mod.coverage_map()
    return {
        "item_id": item_id,
        "item": {"item_id": item["item_id"], "name": item.get("name"),
                 "kind": item.get("kind"), "use_case": item.get("use_case"),
                 "ingest_status": item.get("ingest_status")},
        "cards": cards,
        "gap_areas": [a for a in coverage["areas"] if a["status"] != "covered"],
        "coverage": {"executable": coverage["executable"],
                     "workflow_pending": coverage["workflow_pending"]},
    }


@router.get("/items/{item_id}/diagnostics/{diagnostic_id}/runs")
@_plt04_sanitize
def diagnostic_run_history(item_id: str, diagnostic_id: int):
    """Immutable run history for one diagnostic on one item, newest first."""
    import system_db as _s
    try:
        service.require_item(item_id)
        if not _s.query_one("diagnostic_register", diagnostic_id=diagnostic_id):
            raise KeyError(f"Unknown diagnostic: {diagnostic_id}")
        return {"item_id": item_id, "diagnostic_id": diagnostic_id,
                **_diagnostic_run_history(item_id, diagnostic_id, _s)}
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/items/{item_id}/diagnostics/manifest")
@_plt04_sanitize
def build_diagnostic_manifest(item_id: str, body: ManifestIn):
    """Build (and persist as a DRAFT ``diag_runs`` row) the scope gate:
    rules in scope, resolved role map with score+reason, thresholds with
    their source, scope preview. Returns the manifest."""
    try:
        from dq_diagnostics.register import require_executable
        require_executable(body.diagnostic_id)
        from dq_diagnostics.dispatch import adapter
        selected = adapter(body.diagnostic_id)
        if body.diagnostic_id == 4:
            return selected["manifest"].build_manifest(item_id, body.diagnostic_id,
                actor="system", tenant_id="bootstrap", use_case_override=body.use_case)
        if body.diagnostic_id == 14:
            if body.start_afresh:
                selected["manifest"].discard_drafts(item_id, actor="system")
            elif selected["manifest"].latest_draft(item_id):
                from dq_diagnostics.manifest import ManifestError
                raise ManifestError("An open PSI draft already exists; explicitly resume it or start afresh.")
            return selected["manifest"].build_manifest(item_id, actor="system",
                current_snapshot_id=body.current_snapshot_id)
        return selected["manifest"].build_manifest(item_id, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/items/{item_id}/diagnostics/{diagnostic_id}/draft")
@_plt04_sanitize
def resumable_diagnostic_draft(item_id: str, diagnostic_id: int):
    """Discover an open setup before launch; never resumes it implicitly."""
    try:
        if diagnostic_id != 14:
            return {"draft": None}
        from dq_diagnostics import manifest_population_stability as psi_manifest
        return {"draft": psi_manifest.latest_draft(item_id)}
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.patch("/diagnostics/manifests/{run_id}")
@_plt04_sanitize
def patch_diagnostic_manifest(run_id: str, body: ManifestPatch):
    """One scope-gate edit -> one ``diag_run_decisions`` row (CFR-12).
    ``kind`` ∈ role_override · threshold_tune · scope_exclusion ·
    role_verification_change. Refused once the manifest is frozen."""
    try:
        from dq_diagnostics import manifest as manifest_mod
        run = manifest_mod.get_run(run_id)
        from dq_diagnostics.dispatch import adapter
        return adapter(run["diagnostic_id"])["manifest"].patch_manifest(
            run_id, body.model_dump(exclude_none=True), actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}")
@_plt04_sanitize
def get_diagnostic_manifest(run_id: str):
    from dq_diagnostics import manifest as manifest_mod
    try:
        run = manifest_mod.get_run(run_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)
    if run["diagnostic_id"] == 2 and run["status"] == manifest_mod.DRAFT:
        from dq_diagnostics import manifest_feature_target as feature_manifest
        run["manifest_json"] = feature_manifest.refresh_draft_scope(run_id)
    if run["diagnostic_id"] == 14 and run["status"] == manifest_mod.DRAFT:
        from dq_diagnostics import manifest_population_stability as psi_manifest
        run["manifest_json"] = psi_manifest.refresh_draft_scope(run_id)
    return {"run": {k: run[k] for k in ("run_id", "item_id", "diagnostic_id", "status",
                                        "created_at", "started_at", "finished_at")},
            "manifest": run["manifest_json"],
            "decisions": manifest_mod.list_decisions(run_id)}


@router.post("/diagnostics/manifests/{run_id}/run")
@_plt04_sanitize
def run_diagnostic_manifest(run_id: str, body: RunIn | None = None):
    """Freeze the manifest and execute.

    ``{"stream": true}`` freezes only and hands back the SSE URL — the
    stream then executes. The default (``stream`` false) runs synchronously
    and returns the final summary, so a non-streaming client still gets its
    results. Either way execution happens exactly once per run: the stream
    replays a completed run rather than re-running it.
    """
    from dq_diagnostics import manifest as manifest_mod
    body = body or RunIn()
    try:
        run = manifest_mod.get_run(run_id)
        diagnostic_id = run["diagnostic_id"]
        # FWK-17 — refuse a pending diagnostic with the exact refusal text.
        from dq_diagnostics.register import require_executable
        require_executable(diagnostic_id)
        from dq_diagnostics.dispatch import adapter
        selected = adapter(diagnostic_id)
        runner, freeze, work_count = selected["runner"], selected["manifest"].freeze, selected["work_count"]
        if body.stream:
            if diagnostic_id == 2 and run["status"] == manifest_mod.DRAFT:
                # Freeze against the final persisted Step 3 schema roles.
                from dq_diagnostics import manifest_feature_target as feature_manifest
                feature_manifest.refresh_draft_scope(run_id)
            manifest = freeze(run_id) if run["status"] == manifest_mod.DRAFT \
                else run["manifest_json"]
            return {"run_id": run_id, "status": "running",
                    "stream_url": f"/api/v2/diagnostics/runs/{run_id}/stream",
                    "rules": work_count(manifest)}
        return runner.execute_now(run_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/draft")
@_plt04_sanitize
def create_psi_bin_draft(run_id: str, feature: str, generate_new: bool = False):
    try:
        from dq_diagnostics.manifest_population_stability import create_bin_draft
        return create_bin_draft(run_id, feature, actor="system", ignore_repository_match=generate_new)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}/psi-bins/draft-stream")
@_plt04_sanitize
def create_psi_bin_drafts_stream(run_id: str):
    """Generate outstanding PSI drafts in a bounded, resumable server-side batch."""
    from dq_diagnostics.manifest_population_stability import generate_bin_draft_events
    events = observe_background_events(
        f"psi-bin-drafts:{run_id}",
        lambda: generate_bin_draft_events(run_id, actor="system"),
    )
    return _sse(events)


@router.get("/diagnostics/manifests/{run_id}/psi-bins/{feature}/review")
@_plt04_sanitize
def review_psi_bins(run_id: str, feature: str, artifact_id: str | None = None):
    try:
        from dq_diagnostics.manifest_population_stability import bin_review
        return bin_review(run_id, feature, artifact_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/revise")
@_plt04_sanitize
def revise_psi_bins(run_id: str, feature: str, body: PsiBinRevisionIn):
    try:
        from dq_diagnostics.manifest_population_stability import revise_from_fine
        return revise_from_fine(run_id, feature, body.artifact_id, body.definition, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/preview")
@_plt04_sanitize
def preview_psi_bin_revision(run_id: str, feature: str, body: PsiBinRevisionIn):
    try:
        from dq_diagnostics.manifest_population_stability import preview_fine_revision
        return preview_fine_revision(run_id, feature, body.artifact_id, body.definition)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}/psi-split-options/{feature}")
@_plt04_sanitize
def psi_split_feature_options(run_id: str, feature: str, limit: int = 200):
    try:
        from dq_diagnostics.manifest_population_stability import split_feature_options
        return split_feature_options(run_id, feature, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/freeze")
@_plt04_sanitize
def freeze_psi_bin_draft(run_id: str, feature: str, body: PsiBinFreezeIn):
    try:
        from dq_diagnostics.manifest_population_stability import freeze_bin_draft
        return freeze_bin_draft(run_id, feature, body.draft_artifact_id, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/approve-batch")
@_plt04_sanitize
def approve_psi_bins_batch(run_id: str, body: PsiBinBatchApprovalIn):
    try:
        from dq_diagnostics.manifest_population_stability import approve_bin_batch
        return approve_bin_batch(run_id, body.features, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/manifests/{run_id}/psi-bins/{feature}/candidate-values")
@_plt04_sanitize
def psi_candidate_values(run_id: str, feature: str):
    try:
        from dq_diagnostics.manifest_population_stability import candidate_values
        return candidate_values(run_id, feature)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/manual")
@_plt04_sanitize
def create_manual_psi_bin_draft(run_id: str, feature: str, body: PsiManualBinsIn):
    try:
        from dq_diagnostics.manifest_population_stability import create_manual_bin_draft
        return create_manual_bin_draft(run_id, feature, body.groups, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/numeric-override")
@_plt04_sanitize
def create_numeric_psi_bin_override(run_id: str, feature: str, body: PsiNumericBinsIn):
    try:
        from dq_diagnostics.manifest_population_stability import create_numeric_bin_override
        return create_numeric_bin_override(run_id, feature, body.cuts, body.special_values,
                                           body.rationale, actor="system")
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/manifests/{run_id}/psi-bins/{feature}/promote-universal")
@_plt04_sanitize
def promote_psi_iv_bins(run_id: str, feature: str, body: PsiBinPromotionIn):
    try:
        from dq_diagnostics.manifest_population_stability import promote_psi_iv_bins as promote
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
    from dq_diagnostics import manifest as manifest_mod
    import system_db as _s

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
            yield from runner.run(run_id)
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled error while streaming diagnostic run %s", run_id)
            _s.update("diag_runs", {"run_id": run_id}, {"status": manifest_mod.FAILED,
                                                        "finished_at": _s.now_ist()})
            yield {"phase": "error", "agent": locals().get("agent", "diagnostic_engine"),
                   "thought": "internal error"}
    # Execution is process-owned.  The browser is only an observer: closing or
    # reconnecting EventSource cannot abandon persistence or final run status.
    observed = observe_background_events(f"diagnostic-run:{run_id}", events)
    return _sse(observed)


@router.get("/items/{item_id}/diagnostics/results")
@_plt04_sanitize
def diagnostic_results(item_id: str, run_id: str | None = None,
                       diagnostic_id: int | None = None):
    """Decision-type-shaped results + per-rule findings for display."""
    import system_db as _s
    try:
        service.require_item(item_id)
        if not run_id:
            filters = {"item_id": item_id, "status": "done"}
            if diagnostic_id is not None:
                filters["diagnostic_id"] = diagnostic_id
            rows = _s.query("diag_runs", **filters, order_by="finished_at DESC")
            latest = rows[0] if rows else None
            if not latest:
                return {"item_id": item_id, "run": None, "results": [], "decisions": [],
                        "manifest": None}
            run_id = latest["run_id"]
        from dq_diagnostics.dispatch import adapter
        source_run = _s.query_one("diag_runs", run_id=run_id)
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
            from dq_diagnostics.runner_feature_target import (
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
            from dq_diagnostics.runner_population_stability import ensure_contextual_issue
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
        from dq_diagnostics.runner_population_stability import ensure_contextual_finding
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
        from dq_diagnostics.runner_feature_target import ensure_candidate_finding
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
            from dq_diagnostics.runner_feature_target import ensure_candidate_finding
            finding_id = ensure_candidate_finding(result_id, reason=body.reason, actor="system")
        elif result["diagnostic_id"] == 14:
            from dq_diagnostics.runner_population_stability import ensure_contextual_finding
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
    from dq_diagnostics.binning_reviews import impact
    try:
        return impact(result_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/results/{result_id}/binning-preview")
@_plt04_sanitize
def preview_diagnostic_binning(result_id: str, body: BinningReviewIn):
    from dq_diagnostics.binning_reviews import preview
    try:
        return preview(result_id, body.definition)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.post("/diagnostics/results/{result_id}/binning-review")
@_plt04_sanitize
def review_diagnostic_binning(result_id: str, body: BinningReviewIn):
    from dq_diagnostics.binning_reviews import review
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
    from dq_diagnostics.binning_reviews import create_numeric_override
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
    from dq_diagnostics import runner_cross_field as runner
    try:
        service.require_item(item_id)
        return runner.coverage_summary(item_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)


@router.get("/diagnostics/runs/{run_id}/report")
@_plt04_sanitize
def diagnostic_run_report(run_id: str, fmt: str = "pdf"):
    """The report, derived from the structured result (CFR-10) — never a
    second source of truth."""
    try:
        import system_db as _s
        from dq_diagnostics.dispatch import adapter
        run = _s.query_one("diag_runs", run_id=run_id)
        if not run: raise KeyError(f"Unknown run: {run_id}")
        runner = adapter(run["diagnostic_id"])["runner"]
        if fmt == "text":
            return Response(content=runner.report_text(run_id), media_type="text/plain")
        if run["diagnostic_id"] == 14:
            return Response(content=runner.report_text(run_id), media_type="text/plain")
        report_metadata = None
        if run["diagnostic_id"] == 6:
            payload, report_metadata = runner.report_document(run_id)
        else:
            payload = runner.report_pdf(run_id)
    except Exception as exc:  # noqa: BLE001
        _diag_err(exc)
    filename = (f"row-completeness-{run_id}.pdf" if run["diagnostic_id"] == 6
                else f"cross-field-{run_id}.pdf")
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if report_metadata:
        headers["X-Analysis-Artifact-Id"] = report_metadata["report_artifact"]["artifact_id"]
        headers["X-Analysis-Artifact-Hash"] = report_metadata["report_artifact"]["payload_hash"]
    return Response(content=payload, media_type="application/pdf", headers=headers)
