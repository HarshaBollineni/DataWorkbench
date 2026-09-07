"""Read-only discovery and artifact APIs for supporting analyses."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

import system_db as db
from domains.aar.types import list_artifact_types
from domains.aar.repository import AnalysisArtifactRepository, ArtifactIntegrityError
from domains.aar.data_sourcing import persist_snapshot_profile_artifacts
from analysis_runtime.capabilities import list_capabilities
from analysis_runtime import runs
from analysis_runtime.snapshots import SnapshotLoader, SnapshotNotReadyError
from supporting_analyses import ensure_registered

router = APIRouter(prefix="/api/v2", tags=["supporting-analyses"])


_BACKFILL_LOCK = threading.Lock()
_BACKFILLING: set[str] = set()


def _schedule_profile_artifact_backfill(snapshot_id: str | None) -> str:
    """Keep catalogue reads fast while backfilling retained profile evidence once."""
    if not snapshot_id:
        return "not_applicable"
    snapshot = db.query_one("dq_items", item_id=snapshot_id)
    if not snapshot or snapshot.get("snapshot_status") != "active" or snapshot.get("ingest_status") != "ready":
        return "not_applicable"
    if AnalysisArtifactRepository().list(snapshot_id=snapshot_id, artifact_type="column_profile", status="active"):
        return "complete"
    with _BACKFILL_LOCK:
        if snapshot_id in _BACKFILLING:
            return "pending"
        _BACKFILLING.add(snapshot_id)
    def worker() -> None:
        try:
            persist_snapshot_profile_artifacts(snapshot_id, actor="system:artifact-backfill")
        finally:
            with _BACKFILL_LOCK:
                _BACKFILLING.discard(snapshot_id)
    threading.Thread(target=worker, name=f"artifact-backfill-{snapshot_id[-8:]}", daemon=True).start()
    return "pending"


class AnalysisManifestIn(BaseModel):
    capability_id: str
    table: str
    columns: list[str] = Field(default_factory=list)
    population: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    period_column: str | None = None
    grain: str | None = None
    analysis_context: str | None = None
    missing_value_codes: dict[str, list[Any]] = Field(default_factory=dict)


class ObservationDispositionIn(BaseModel):
    action: str
    reason: str | None = None


@router.get("/items/{item_id}/analyses/catalog")
def analysis_catalog(item_id: str) -> dict:
    ensure_registered()
    try:
        snapshot = SnapshotLoader().reference(item_id)
    except (KeyError, ValueError, SnapshotNotReadyError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 409,
                            detail=str(exc)) from exc
    return {"snapshot": snapshot.to_dict(), "capabilities": list_capabilities()}


@router.post("/items/{item_id}/analyses/manifests")
def create_analysis_manifest(item_id: str, body: AnalysisManifestIn) -> dict:
    ensure_registered()
    context = body.model_dump(exclude={"capability_id"})
    try:
        return runs.create_manifest(item_id, body.capability_id, context)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/analyses/manifests/{run_id}")
def get_analysis_manifest(run_id: str) -> dict:
    try:
        return runs.result(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/analyses/manifests/{run_id}/run")
def run_analysis_manifest(run_id: str) -> dict:
    ensure_registered()
    try:
        return runs.execute(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # deterministic engine failure is recorded, not a DQ finding
        raise HTTPException(status_code=500, detail="Supporting analysis failed; see the run record.") from exc


@router.get("/items/{item_id}/analyses/results")
def list_analysis_results(item_id: str, capability_id: str | None = None) -> dict:
    try:
        SnapshotLoader().reference(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"item_id": item_id, "runs": runs.list_results(item_id, capability_id)}


@router.post("/analysis-observations/{observation_id}/disposition")
def dispose_analysis_observation(observation_id: str,
                                 body: ObservationDispositionIn) -> dict:
    try:
        return runs.dispose_observation(observation_id, body.action, body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/analysis-artifacts")
def list_analysis_artifacts(asset_id: str | None = None,
                            snapshot_id: str | None = None,
                            artifact_type: str | None = None,
                            status: str | None = Query(default=None, pattern="^(active|superseded)$"),
                            scope: str | None = None, feature: str | None = None,
                            feature_query: str | None = None,
                            run_id: str | None = None, workflow_id: str | None = None,
                            limit: int = Query(default=50, ge=1, le=200),
                            offset: int = Query(default=0, ge=0)) -> dict:
    backfill_status = _schedule_profile_artifact_backfill(snapshot_id)
    result = AnalysisArtifactRepository().page(
        asset_id=asset_id, snapshot_id=snapshot_id, artifact_type=artifact_type,
        status=status, scope=scope, feature=feature, run_id=run_id,
        workflow_id=workflow_id, feature_query=feature_query, limit=limit, offset=offset,
    )
    return {**result, "backfill_status": backfill_status}


@router.get("/analysis-artifacts/overview")
def analysis_artifact_overview(asset_id: str | None = None, snapshot_id: str | None = None) -> dict:
    return {**AnalysisArtifactRepository().overview(asset_id=asset_id, snapshot_id=snapshot_id),
            "backfill_status": _schedule_profile_artifact_backfill(snapshot_id)}


@router.get("/analysis-artifacts/types")
def analysis_artifact_types() -> dict:
    return {"artifact_types": list_artifact_types()}


@router.get("/analysis-artifacts/integrity-audit")
def analysis_artifact_integrity_audit(limit: int = Query(default=200, ge=1, le=1000)) -> dict:
    return AnalysisArtifactRepository().integrity_audit(limit=limit)


@router.get("/analysis-artifacts/runs")
def list_repository_runs(snapshot_id: str, capability_id: str | None = None) -> dict:
    """Frozen retained supporting-analysis runs for repository consumers."""
    return {"snapshot_id": snapshot_id, "runs": runs.list_results(snapshot_id, capability_id)}


@router.get("/analysis-artifacts/{artifact_id}")
def get_analysis_artifact(artifact_id: str) -> dict:
    try:
        metadata, _payload = AnalysisArtifactRepository().get(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # A detail read verifies the immutable payload, but payload transfer is an
    # explicit audit action so normal catalogue/detail navigation stays bounded.
    return {"artifact": metadata.to_dict()}


@router.get("/analysis-artifacts/{artifact_id}/payload")
def get_analysis_artifact_payload(artifact_id: str) -> dict:
    try:
        metadata, payload = AnalysisArtifactRepository().get(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if metadata.payload_media_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail={"message": "Binary artifact payloads use the download endpoint",
                    "download_url": f"/api/v2/analysis-artifacts/{artifact_id}/download"},
        )
    return {"artifact_id": metadata.artifact_id, "payload": payload}


@router.get("/analysis-artifacts/{artifact_id}/download")
def download_analysis_artifact(artifact_id: str) -> Response:
    try:
        metadata, payload = AnalysisArtifactRepository().read_bytes(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = metadata.payload_filename or Path(metadata.payload_path).name
    return Response(
        content=payload, media_type=metadata.payload_media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analysis-artifacts/{artifact_id}/impact")
def analysis_artifact_impact(artifact_id: str) -> dict:
    try:
        rows = AnalysisArtifactRepository().impact(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"source_artifact_id": artifact_id,
            "dependants": [row.to_dict() for row in rows]}


@router.get("/analysis-artifacts/{artifact_id}/lineage")
def analysis_artifact_lineage(artifact_id: str, limit: int = Query(default=200, ge=1, le=500)) -> dict:
    try:
        return AnalysisArtifactRepository().lineage(artifact_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
