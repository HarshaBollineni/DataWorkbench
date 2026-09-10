"""Dataset Structure materialization and Slice-2 review HTTP boundary."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import tenancy
from domains.aar import materialization_jobs
from domains.aar import dataset_structure_review as review_service
from domains.aar import dataset_structure_backfill

router = APIRouter()


class MaterializationRetryIn(BaseModel):
    reason: str


class BackfillIn(BaseModel):
    cursor: str | None = None
    limit: int = 25


def _require_structure_admin(authorization: str | None) -> dict:
    """Fail closed until the narrow structure-admin role is explicitly granted."""
    principal = tenancy.resolve_principal(authorization)
    if not principal.get("tenant_resolved"):
        raise HTTPException(status_code=404, detail="Unknown dataset structure materialization.")
    if "data_sourcing.structure.admin" not in set(principal.get("authz_roles") or []):
        raise HTTPException(status_code=403, detail="Dataset structure administration is not permitted.")
    return principal


def _item_or_404(item_id: str, authorization: str | None) -> tuple[dict, dict]:
    principal = tenancy.resolve_principal(authorization)
    if not principal.get("tenant_resolved"):
        raise HTTPException(status_code=404, detail="Unknown dataset structure materialization.")
    # Authorization deliberately does not disclose publication/job state.
    import system_db as db
    item = db.query_one("dq_items", item_id=item_id)
    # Do not distinguish unknown, inactive, missing tenant, or cross tenant.
    if (item is None or item.get("snapshot_status") != "active" or item.get("ingest_status") != "ready"
            or not item.get("sourcing_tenant_id") or item.get("sourcing_tenant_id") != principal["tenant_id"]):
        raise HTTPException(status_code=404, detail="Unknown dataset structure materialization.")
    return principal, item


@router.post("/items/{item_id}/dataset-structure/materializations")
def retry_materialization(item_id: str, body: MaterializationRetryIn,
                          authorization: str | None = Header(default=None)):
    principal, _item = _item_or_404(item_id, authorization)
    if body.reason != "retry":
        raise HTTPException(status_code=422, detail="reason must be retry")
    materialization_jobs.reconcile(item_id, ensure_job=False)
    job, created = materialization_jobs.enqueue(item_id, reason="retry", tenant_id=principal["tenant_id"], repair_marker=True)
    if job is None:  # defensive: eligibility may have changed after reconciliation
        raise HTTPException(status_code=404, detail="Unknown dataset structure materialization.")
    return JSONResponse(materialization_jobs.public_status(job), status_code=202 if created else 200)


@router.post("/dataset-structure/backfill")
def run_structure_backfill(body: BackfillIn,
                           authorization: str | None = Header(default=None)):
    """Explicit bounded Slice-4 migration; never a startup reconciliation path."""
    principal = _require_structure_admin(authorization)
    try:
        response = dataset_structure_backfill.run_backfill(
            tenant_id=principal["tenant_id"], cursor=body.cursor, limit=body.limit,
        )
    except dataset_structure_backfill.BackfillConfigurationError:
        raise HTTPException(status_code=503, detail="Dataset structure backfill is not configured.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse(response, status_code=202 if response["counts"]["scheduled"] else 200)


@router.get("/items/{item_id}/dataset-structure/materializations/{job_id}")
def get_materialization(item_id: str, job_id: str,
                        authorization: str | None = Header(default=None)):
    principal, _item = _item_or_404(item_id, authorization)
    # Bounded reconciliation does not scan source data on this request.
    materialization_jobs.reconcile(item_id)
    row = materialization_jobs.status(job_id, snapshot_id=item_id, tenant_id=principal["tenant_id"])
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown dataset structure materialization.")
    return materialization_jobs.public_status(row)


@router.get("/items/{item_id}/dataset-structure/review")
def get_review(item_id: str, authorization: str | None = Header(default=None)):
    # Reuse the Foundation item guard so missing, not-ready, tenantless, and
    # cross-tenant snapshots retain indistinguishable 404 behaviour.
    _principal, item = _item_or_404(item_id, authorization)
    # The review service projects only retained materialized artifacts; it
    # deliberately does not reconcile/enqueue or scan source data on GET.
    return review_service.review(item)


@router.get("/items/{item_id}/dataset-structure/review/candidates")
def get_review_candidates(item_id: str, table_id: str = Query(...), facet: str = Query(...),
                          q: str | None = Query(default=None), cursor: str | None = Query(default=None),
                          offset: int | None = Query(default=None), limit: int = Query(default=25),
                          authorization: str | None = Header(default=None)):
    _principal, item = _item_or_404(item_id, authorization)
    try:
        return review_service.review_facet(item, table_id=table_id, facet=facet, q=q, cursor=cursor,
                                           offset=offset, limit=limit)
    except review_service.DraftInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/items/{item_id}/dataset-structure/draft")
def patch_review_draft(item_id: str, body: dict,
                       idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                       authorization: str | None = Header(default=None)):
    principal, item = _item_or_404(item_id, authorization)
    if not idempotency_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required")
    try:
        response = review_service.save_draft(item, body, idempotency_key=idempotency_key,
                                             actor=principal.get("username"))
    except review_service.IdempotencyReuse:
        raise HTTPException(status_code=409, detail="idempotency_key_reused")
    except review_service.DraftInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse(response, status_code=200)


@router.post("/items/{item_id}/dataset-structure/decisions")
def post_review_decisions(item_id: str, body: dict,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                          authorization: str | None = Header(default=None)):
    principal, item = _item_or_404(item_id, authorization)
    if not idempotency_key:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required")
    try:
        response = review_service.save_decisions(item, body, idempotency_key=idempotency_key,
                                                 actor=principal.get("username"))
    except review_service.IdempotencyReuse:
        raise HTTPException(status_code=409, detail="idempotency_key_reused")
    except review_service.DraftInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse(response, status_code=200)
