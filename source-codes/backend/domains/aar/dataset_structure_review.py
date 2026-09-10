"""Slice-2 Dataset Structure review projection and mutable draft service.

This module deliberately reads only materialized AAR DSC-v1 assertions and
the durable job marker.  It never invokes an observer, loads a source table,
or writes an AAR assertion/decision.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import system_db as db
from analysis_runtime.contracts import stable_fingerprint
from analysis_runtime.dataset_structure_context import expected_cadence_instance_key, payload_hash
from .repository import AnalysisArtifactRepository
from .dataset_structure_producer import _assertion_batch_entry, _verified_authority_write_scope

_ASSERTION = "dataset_structure_assertion"
_PREDICATES = {
    "table.structure/entity_binding": "entities",
    "table.temporal/temporal_binding": "temporals",
    "table.structure/row_grain": "row_grains",
    "table.temporal/observed_cadence": "observed_cadences",
}
_SELECTABLE = {"entities", "temporals", "row_grains"}
_FIELDS = {
    "default_entity_candidate_id": "entities",
    "default_temporal_candidate_id": "temporals",
    "row_grain_candidate_id": "row_grains",
}
_DRAFT_CADENCE_ACTIONS = {"confirm", "replace", "clear", "mark_not_applicable"}
_WARNING_CODES = {"DSC_R_INSUFFICIENT_BASIS", "DSC_R_AMBIGUOUS_CANDIDATES",
                  "DSC_R_SOURCE_INTEGRITY_FAILED", "DSC_R_SOURCE_MISSING"}
_MAX_CANDIDATES_PER_FACET = 20


class DraftInputError(ValueError):
    pass


class IdempotencyReuse(ValueError):
    pass


def begin_metadata_correction_in_transaction(conn: Any, snapshot_id: str, *, tenant_id: str) -> bool:
    """Join the column-review UoW before its first metadata mutation."""
    now = _utc()
    row = conn.execute("SELECT tenant_id FROM dataset_structure_review_states WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if row is None or row["tenant_id"] != tenant_id:
        return False
    fenced = conn.execute("UPDATE dq_items SET profile_publication_attempt_token=? WHERE item_id=? AND sourcing_tenant_id=?",
                          ("dscpa_" + uuid.uuid4().hex, snapshot_id, tenant_id))
    if fenced.rowcount != 1:
        return False
    conn.execute("UPDATE dataset_structure_review_states SET state='metadata_review',updated_at=? WHERE snapshot_id=? AND tenant_id=?",
                 (now, snapshot_id, tenant_id))
    return True


def begin_metadata_correction(snapshot_id: str, *, tenant_id: str) -> bool:
    """Fence a DSC review before its authoritative column metadata changes.

    The mutable draft is deliberately untouched.  The following governed
    profile publication creates the new evidence generation; ``review`` then
    turns the preserved draft into ``needs_reconfirmation`` only after that
    generation has materialized.  Returning ``False`` means this snapshot has
    never entered DSC review, so there is no draft or authority to preserve.
    """
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Fence the former worker even when this save temporarily leaves the
        # snapshot short of normal publication readiness (for example while a
        # required metadata confirmation is being corrected).  The next
        # successful Data Sourcing publication replaces this token and queues
        # its own exact generation.
        changed = begin_metadata_correction_in_transaction(conn, snapshot_id, tenant_id=tenant_id)
        if not changed:
            conn.rollback()
            return False
        conn.commit()
    return True


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token(prefix: str, value: Any) -> str:
    # Only an opaque transport token is exposed.  It is not an AAR locator,
    # payload hash, path, or reusable internal identifier.
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return prefix + hashlib.sha256(encoded).hexdigest()[:24]


def _display(value: dict[str, Any], kind: str) -> str:
    columns = [str(x.get("column")) for x in value.get("columns", value.get("key_columns", []))
               if isinstance(x, dict) and isinstance(x.get("column"), str)]
    if kind == "entities":
        return "Entity identifier: " + ", ".join(columns)
    if kind == "temporals":
        return ("Date field: " if value.get("temporal_type") == "date" else "Period field: ") + ", ".join(columns)
    if kind == "row_grains":
        return "Row grain: " + " + ".join(columns)
    cadence = str(value.get("cadence") or "unknown")
    return "Observed cadence: " + cadence.replace("_", " ")


def _aggregate(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    usable = total = 0
    for item in evidence[:8]:
        basis = item.get("basis") if isinstance(item, dict) else None
        if isinstance(basis, dict):
            total += int(basis.get("total_count") or 0)
            usable += int(basis.get("usable_count") or 0)
    return {"evidence_count": len(evidence), "aggregate_basis": "materialized",
            "usable_observations": usable, "total_observations": total}


def _candidate(metadata: Any, payload: dict[str, Any], kind: str) -> dict[str, Any] | None:
    resolution = payload.get("resolution") if isinstance(payload.get("resolution"), dict) else {}
    status = resolution.get("status")
    claims = payload.get("claims") if isinstance(payload.get("claims"), list) else []
    effective = set(resolution.get("effective_claim_ids") or [])
    claim = next((x for x in claims if isinstance(x, dict) and x.get("claim_id") in effective), None)
    if claim is None and claims:
        claim = claims[0]
    value = claim.get("value") if isinstance(claim, dict) and isinstance(claim.get("value"), dict) else None
    if status not in {"proposed", "observed"} or value is None:
        return None
    warnings = sorted(code for code in (resolution.get("reason_codes") or []) if code in _WARNING_CODES)
    identity = {"artifact": metadata.artifact_id, "assertion": payload.get("assertion_id"),
                "payload": metadata.payload_hash, "dependency": payload.get("dependency_fingerprint"), "kind": kind}
    label, summary = _display(value, kind), _aggregate(payload)
    result = {"candidate_id": _token("cand_", identity), "display_label": label, "safe_label": label,
              "predicate": payload["predicate"], "instance_key": payload.get("instance_key", ""),
              "resolution_status": status, "evidence_summary": summary, "evidence": summary,
              "warnings": warnings, "_pin": identity, "_sensitivity": payload.get("sensitivity", "internal"),
              "_value": value}
    if kind == "observed_cadences":
        result["observed_cadence"] = str(value.get("cadence") or "unknown")
        interval = value.get("observed_interval_class")
        if isinstance(interval, dict) and interval.get("unit") in {"day", "week", "month", "quarter", "year"}:
            result["observed_interval"] = {"unit": interval["unit"], "step": int(interval.get("step") or 1)}
    return result


def _artifacts(item: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    tables: dict[str, dict[str, list[dict[str, Any]]]] = {}
    repo = AnalysisArtifactRepository()
    # Repository reads verify the retained artifact payload; no source snapshot
    # loader/producer is called by this code path.
    for metadata in repo.list(snapshot_id=item["item_id"], artifact_type=_ASSERTION, status="active", scope="universal"):
        if metadata.asset_id != item.get("dataset_family_id"):
            continue
        try:
            checked, payload = repo.get(metadata.artifact_id)
        except Exception:
            continue
        if checked.status != "active" or not isinstance(payload, dict) or payload.get("context_version") != "1":
            continue
        subject, predicate = payload.get("subject"), payload.get("predicate")
        if not isinstance(subject, dict) or subject.get("kind") != "table" or predicate not in _PREDICATES:
            continue
        table = subject.get("table")
        if not isinstance(table, str) or not table:
            continue
        kind = _PREDICATES[predicate]
        candidate = _candidate(checked, payload, kind)
        if candidate is not None:
            tables.setdefault(table, {name: [] for name in _PREDICATES.values()})[kind].append(candidate)
    return tables


def _projection_bindings(item: dict[str, Any], *, conn: Any = None) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], str]:
    """Read the complete active v1 projection set without side-effecting it.

    Unlike the normal read projection this can run under a caller's write UoW:
    it verifies payload bytes directly rather than using ``repo.get()``, whose
    integrity bookkeeping would need a second connection.  The binding digest
    fences every active candidate identity relevant to the review projection.
    """
    repo, tables, bindings = AnalysisArtifactRepository(), {}, []
    rows = db.query("analysis_artifacts", conn=conn, snapshot_id=item["item_id"],
                    artifact_type=_ASSERTION, status="active", scope="universal")
    for row in rows:
        if row.get("asset_id") != item.get("dataset_family_id"):
            continue
        metadata = repo._metadata(row)
        try:
            bytes_ = repo._payload_path(metadata).read_bytes()
            if hashlib.sha256(bytes_).hexdigest() != metadata.payload_hash:
                continue
            payload = json.loads(bytes_)
        except Exception:
            continue
        if not isinstance(payload, dict) or payload.get("context_version") != "1":
            continue
        subject, predicate = payload.get("subject"), payload.get("predicate")
        if not isinstance(subject, dict) or subject.get("kind") != "table" or predicate not in _PREDICATES:
            continue
        table = subject.get("table")
        if not isinstance(table, str) or not table:
            continue
        kind = _PREDICATES[predicate]
        candidate = _candidate(metadata, payload, kind)
        if candidate is None:
            continue
        tables.setdefault(table, {name: [] for name in _PREDICATES.values()})[kind].append(candidate)
        bindings.append({"artifact_id": metadata.artifact_id, "artifact_type": metadata.artifact_type,
                         "status": metadata.status, "payload_hash": metadata.payload_hash,
                         "predicate": predicate, "instance_key": payload.get("instance_key", ""),
                         "assertion_id": payload.get("assertion_id"),
                         "dependency_fingerprint": payload.get("dependency_fingerprint")})
    return tables, stable_fingerprint({"snapshot_id": item["item_id"], "asset_id": item["dataset_family_id"],
                                       "active_projection_bindings": sorted(bindings, key=lambda value: (value["artifact_id"], value["instance_key"]))})


def _materialization(item_id: str) -> tuple[dict[str, Any], int | None]:
    rows = db.query("dataset_structure_materialization_jobs", snapshot_id=item_id,
                    order_by="created_at DESC, job_id DESC")
    row = rows[0] if rows else None
    if row is None:
        return {"job_id": None, "status": "not_started", "generation": None,
                "freshness": "unavailable", "retry_after_ms": None}, None
    status = "failed" if row["status"] == "revoked" else row["status"]
    marker = db.query_one("dataset_structure_profile_publications", snapshot_id=item_id)
    item = db.query_one("dq_items", item_id=item_id) or {}
    fresh = (marker is not None and marker.get("generation") == row.get("publication_generation")
             and marker.get("fingerprint") == row.get("publication_fingerprint")
             and (row.get("publication_attempt_token") or "") == (item.get("profile_publication_attempt_token") or ""))
    return {"job_id": row["job_id"], "status": status, "generation": row.get("publication_generation"),
            "freshness": "current" if fresh else "stale", "retry_after_ms": 1000 if status in {"queued", "retry_wait"} else None}, row.get("publication_generation")


def _evidence_fingerprint(candidates: dict[str, dict[str, list[dict[str, Any]]]]) -> str:
    pins = []
    for table, groups in sorted(candidates.items()):
        for kind, values in sorted(groups.items()):
            pins.extend({"table": table, "kind": kind, "pin": item["_pin"]} for item in values)
    return stable_fingerprint({"review_contract_version": "1", "candidates": pins})


def _public_evidence(fingerprint: str) -> str:
    return _token("evidence_", {"version": 1, "fingerprint": fingerprint})


def _derive_state(materialization: dict[str, Any], candidates: dict[str, dict[str, list[dict[str, Any]]]],
                  table_names: set[str]) -> str:
    status = materialization["status"]
    if status == "not_started":
        return "not_started"
    if status in {"queued", "running", "retry_wait"}:
        return "materializing"
    if status == "failed":
        return "failed"
    required = ("entities", "temporals", "row_grains")
    return "limited" if (not table_names or any(not candidates.get(table, {}).get(name)
                        for table in table_names for name in required)) else "review_required"


def _upsert_state(item: dict[str, Any], *, state: str, generation: int | None, fingerprint: str,
                  preserve_stale: bool = True) -> dict[str, Any]:
    existing = db.query_one("dataset_structure_review_states", snapshot_id=item["item_id"])
    now = _utc()
    if existing is None:
        db.insert("dataset_structure_review_states", {"snapshot_id": item["item_id"], "tenant_id": item["sourcing_tenant_id"],
                  "asset_id": item["dataset_family_id"], "state": state, "current_generation": generation,
                  "current_evidence_fingerprint": fingerprint, "current_draft_id": None,
                  "review_contract_version": "1", "updated_at": now})
    else:
        changed = existing.get("current_evidence_fingerprint") not in {None, fingerprint}
        has_draft = bool(existing.get("current_draft_id"))
        next_state = ("needs_reconfirmation" if preserve_stale and has_draft
                      and (changed or existing.get("state") == "needs_reconfirmation") else state)
        db.update("dataset_structure_review_states", {"snapshot_id": item["item_id"]},
                  {"state": next_state, "current_generation": generation,
                   "current_evidence_fingerprint": fingerprint, "updated_at": now})
    return db.query_one("dataset_structure_review_states", snapshot_id=item["item_id"])


def _ensure_draft(item: dict[str, Any], state: dict[str, Any], *, editable: bool) -> dict[str, Any] | None:
    if state.get("current_draft_id"):
        current = db.query_one("dataset_structure_review_drafts", draft_id=state["current_draft_id"])
        if current:
            return current
    # A placeholder draft created while work is still materializing would make
    # the first successful evidence publication look stale.  Persist revision
    # zero only once the materialization has completed successfully.
    if not editable:
        return None
    now, draft_id = _utc(), "dscd_" + uuid.uuid4().hex
    row = {"draft_id": draft_id, "tenant_id": item["sourcing_tenant_id"], "snapshot_id": item["item_id"],
           "revision": 0, "base_evidence_fingerprint": state.get("current_evidence_fingerprint"),
           "selections_json": {"tables": []}, "state": "active", "superseded_by": None,
           "created_by": None, "created_at": now, "updated_at": now}
    db.insert("dataset_structure_review_drafts", row)
    db.update("dataset_structure_review_states", {"snapshot_id": item["item_id"]}, {"current_draft_id": draft_id})
    return db.query_one("dataset_structure_review_drafts", draft_id=draft_id)


def _public_candidate(candidate: dict[str, Any], *, recommended: bool, rank: int) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if not key.startswith("_")} | {"rank": rank, "recommended": recommended}


def _public_draft(draft: dict[str, Any] | None, fingerprint: str, *, editable: bool) -> dict[str, Any]:
    if draft is None:
        return {"draft_id": None, "revision": 0, "evidence_fingerprint": _public_evidence(fingerprint),
                "selections": {"tables": []}, "editable": False}
    selections = draft.get("selections_json") or {"tables": []}
    if isinstance(selections, str):
        try: selections = json.loads(selections)
        except ValueError: selections = {"tables": []}
    return {"draft_id": _token("draft_", {"draft": draft["draft_id"]}), "revision": int(draft["revision"]),
            "evidence_fingerprint": _public_evidence(fingerprint), "selections": selections,
            "editable": editable}


def prepare_backfill_projection(item: dict[str, Any], *, generation: int) -> dict[str, Any]:
    """Build the bounded post-materialization projection without writing it."""
    candidates, binding_fingerprint = _projection_bindings(item)
    fingerprint = _evidence_fingerprint(candidates)
    table_names = set(candidates) | {row["table_name"] for row in db.query("dq_item_tables", item_id=item["item_id"])}
    state = _derive_state({"status": "succeeded"}, candidates, table_names)
    return {"state": state, "fingerprint": fingerprint, "binding_fingerprint": binding_fingerprint,
            "generation": generation}


def commit_backfill_projection(conn: Any, item: dict[str, Any], projection: dict[str, Any], *, job_id: str) -> None:
    """Commit initial review state/draft and migration outcome in the job UoW.

    This never confirms or replaces authority.  A malformed pre-existing state
    is retried rather than falsely recording the migration as materialized.
    """
    snapshot_id, tenant_id = item["item_id"], item["sourcing_tenant_id"]
    current_candidates, current_binding_fingerprint = _projection_bindings(item, conn=conn)
    current_fingerprint = _evidence_fingerprint(current_candidates)
    if (current_binding_fingerprint != projection.get("binding_fingerprint")
            or current_fingerprint != projection.get("fingerprint")):
        raise DraftInputError("materialized review evidence changed")
    state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=? AND tenant_id=?", (snapshot_id, tenant_id)).fetchone()
    now = _utc()
    if state is None:
        conn.execute("INSERT INTO dataset_structure_review_states (snapshot_id,tenant_id,asset_id,state,current_generation,current_evidence_fingerprint,current_draft_id,review_contract_version,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (snapshot_id, tenant_id, item["dataset_family_id"], projection["state"], projection["generation"], projection["fingerprint"], None, "1", now))
        state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    else:
        state = dict(state)
        # A genuine existing confirmed workflow remains authoritative. Its
        # draft must already exist; backfill must not overwrite it.
        if state["state"] == "confirmed":
            if not state.get("current_draft_id"):
                raise DraftInputError("confirmed review has no retained draft")
        else:
            conn.execute("UPDATE dataset_structure_review_states SET state=?,current_generation=?,current_evidence_fingerprint=?,updated_at=? WHERE snapshot_id=? AND tenant_id=?", (projection["state"], projection["generation"], projection["fingerprint"], now, snapshot_id, tenant_id))
        state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    draft_id = state["current_draft_id"]
    if draft_id:
        draft = conn.execute("SELECT 1 FROM dataset_structure_review_drafts WHERE draft_id=? AND snapshot_id=? AND tenant_id=?", (draft_id, snapshot_id, tenant_id)).fetchone()
        if draft is None:
            raise DraftInputError("review draft is unavailable")
    else:
        draft_id = "dscd_" + uuid.uuid4().hex
        conn.execute("INSERT INTO dataset_structure_review_drafts (draft_id,tenant_id,snapshot_id,revision,base_evidence_fingerprint,selections_json,state,superseded_by,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (draft_id, tenant_id, snapshot_id, 0, projection["fingerprint"], '{"tables":[]}', "active", None, None, now, now))
        conn.execute("UPDATE dataset_structure_review_states SET current_draft_id=? WHERE snapshot_id=? AND tenant_id=? AND current_draft_id IS NULL", (draft_id, snapshot_id, tenant_id))
    result = conn.execute("UPDATE dataset_structure_backfill_runs SET status='materialized',closed_error_code=NULL,updated_at=? WHERE job_id=? AND tenant_id=? AND status='scheduled'", (now, job_id, tenant_id))
    if result.rowcount != 1:
        raise DraftInputError("backfill migration record is unavailable")


def complete_existing_backfill_projection(item: dict[str, Any], *, job_id: str, generation: int) -> None:
    """Retry-safe completion for a job that succeeded before backfill linked it."""
    projection = prepare_backfill_projection(item, generation=generation)
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute("SELECT * FROM dataset_structure_materialization_jobs WHERE job_id=?", (job_id,)).fetchone()
        current_item = conn.execute("SELECT * FROM dq_items WHERE item_id=?", (item["item_id"],)).fetchone()
        marker = conn.execute("SELECT * FROM dataset_structure_profile_publications WHERE snapshot_id=?", (item["item_id"],)).fetchone()
        completion = conn.execute("SELECT * FROM dataset_structure_profile_publication_completions WHERE snapshot_id=?", (item["item_id"],)).fetchone()
        if (job is None or current_item is None or marker is None or completion is None
                or job["status"] != "succeeded" or job["tenant_id"] != item["sourcing_tenant_id"]
                or job["snapshot_id"] != item["item_id"] or job["publication_generation"] != generation
                or current_item["snapshot_status"] != "active" or current_item["ingest_status"] != "ready"
                or current_item["sourcing_tenant_id"] != item["sourcing_tenant_id"]
                or current_item["dataset_family_id"] != item["dataset_family_id"]
                or (current_item["profile_publication_attempt_token"] or "") != job["publication_attempt_token"]
                or marker["tenant_id"] != job["tenant_id"] or marker["asset_id"] != job["asset_id"]
                or marker["generation"] != job["publication_generation"]
                or marker["fingerprint"] != job["publication_fingerprint"]
                or completion["state"] != "completed" or completion["tenant_id"] != job["tenant_id"]
                or completion["asset_id"] != job["asset_id"] or completion["generation"] != job["publication_generation"]
                or completion["fingerprint"] != job["publication_fingerprint"]
                or completion["completion_token"] != job["publication_completion_token"]):
            conn.rollback()
            raise DraftInputError("materialization is not ready for review projection")
        run = conn.execute("SELECT status FROM dataset_structure_backfill_runs WHERE job_id=? AND tenant_id=?", (job_id, item["sourcing_tenant_id"])).fetchone()
        if run is not None and run["status"] == "materialized":
            conn.commit()
            return
        commit_backfill_projection(conn, item, projection, job_id=job_id)
        conn.commit()


def _confirmed_authority(item: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Bounded status projection; it deliberately omits AAR IDs/pins/values."""
    names = {"table.structure/default_entity_binding": "default_entity",
             "table.temporal/default_temporal_binding": "default_temporal",
             "table.temporal/expected_cadence": "expected_cadence"}
    result: dict[str, dict[str, str]] = {}
    repo = AnalysisArtifactRepository()
    for metadata in repo.list(snapshot_id=item["item_id"], artifact_type=_ASSERTION, status="active", scope="universal"):
        try:
            _metadata, payload = repo.get(metadata.artifact_id)
        except Exception:
            continue
        table = payload.get("subject", {}).get("table") if isinstance(payload.get("subject"), dict) else None
        facet = names.get(payload.get("predicate"))
        if payload.get("context_version") == "2" and isinstance(table, str) and facet:
            result.setdefault(table, {})[facet] = str(payload.get("resolution", {}).get("status") or "unknown")
    return result


def review(item: dict[str, Any]) -> dict[str, Any]:
    materialization, generation = _materialization(item["item_id"])
    candidates = _artifacts(item)
    fingerprint = _evidence_fingerprint(candidates)
    table_names = sorted(set(candidates) | {r["table_name"] for r in db.query("dq_item_tables", item_id=item["item_id"])})
    editable = materialization["status"] == "succeeded" and materialization["freshness"] == "current"
    # The initial GET is a write-on-read only for bounded workflow state.  It
    # must be safe when two tabs/processes arrive together: state upsert,
    # active draft re-read and revision-zero creation share one immediate
    # transaction, with the unique (snapshot, revision) constraint as the
    # final fence.
    desired_state = _derive_state(materialization, candidates, set(table_names))
    now = _utc()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=?", (item["item_id"],)).fetchone()
        if state is None:
            conn.execute("INSERT OR IGNORE INTO dataset_structure_review_states (snapshot_id,tenant_id,asset_id,state,current_generation,current_evidence_fingerprint,current_draft_id,review_contract_version,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (item["item_id"], item["sourcing_tenant_id"], item["dataset_family_id"], desired_state, generation, fingerprint, None, "1", now))
            state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=?", (item["item_id"],)).fetchone()
        else:
            changed = state["current_evidence_fingerprint"] not in {None, fingerprint}
            # A metadata correction has fenced the prior publication.  While
            # replacement evidence is in flight, retain the draft but do not
            # label it stale prematurely; stale is determined only against a
            # completed replacement projection.
            if state["state"] == "metadata_review" and materialization["freshness"] != "current":
                next_state = "metadata_review"
            elif materialization["status"] in {"queued", "running", "retry_wait"}:
                next_state = "materializing"
            elif state["current_draft_id"] and (changed or state["state"] == "needs_reconfirmation"):
                next_state = "needs_reconfirmation"
            elif state["state"] == "confirmed":
                next_state = "confirmed"
            else:
                next_state = desired_state
            conn.execute("UPDATE dataset_structure_review_states SET state=?,current_generation=?,current_evidence_fingerprint=?,updated_at=? WHERE snapshot_id=?", (next_state, generation, fingerprint, now, item["item_id"]))
            state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=?", (item["item_id"],)).fetchone()
        draft = None
        if state["current_draft_id"]:
            draft = conn.execute("SELECT * FROM dataset_structure_review_drafts WHERE draft_id=? AND snapshot_id=? AND tenant_id=?", (state["current_draft_id"], item["item_id"], item["sourcing_tenant_id"])).fetchone()
        if draft is None and editable:
            draft_id = "dscd_" + uuid.uuid4().hex
            conn.execute("INSERT OR IGNORE INTO dataset_structure_review_drafts (draft_id,tenant_id,snapshot_id,revision,base_evidence_fingerprint,selections_json,state,superseded_by,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (draft_id, item["sourcing_tenant_id"], item["item_id"], 0, fingerprint, '{\"tables\":[]}', "active", None, None, now, now))
            draft = conn.execute("SELECT * FROM dataset_structure_review_drafts WHERE snapshot_id=? AND revision=0", (item["item_id"],)).fetchone()
            conn.execute("UPDATE dataset_structure_review_states SET current_draft_id=? WHERE snapshot_id=? AND current_draft_id IS NULL", (draft["draft_id"], item["item_id"]))
            state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=?", (item["item_id"],)).fetchone()
            draft = conn.execute("SELECT * FROM dataset_structure_review_drafts WHERE draft_id=?", (state["current_draft_id"],)).fetchone()
        conn.commit()
    state, draft = dict(state), (None if draft is None else dict(draft))
    public_tables, confirmed = [], _confirmed_authority(item)
    for table in table_names:
        groups = candidates.get(table, {name: [] for name in _PREDICATES.values()})
        public_groups, recommendations = {}, {}
        candidate_limits = {}
        for kind in _PREDICATES.values():
            ordered = sorted(groups.get(kind, []), key=lambda c: (-c["evidence"]["usable_observations"], c["candidate_id"]))
            visible = ordered[:_MAX_CANDIDATES_PER_FACET]
            public_groups[kind] = [_public_candidate(value, recommended=(index == 0 and kind in _SELECTABLE), rank=index + 1)
                                   for index, value in enumerate(visible)]
            candidate_limits[kind] = {"returned": len(visible), "truncated": len(ordered) > len(visible),
                                      "limit": _MAX_CANDIDATES_PER_FACET}
            recommendations[{"entities": "default_entity", "temporals": "default_temporal", "row_grains": "row_grain", "observed_cadences": "observed_cadence"}[kind]] = (ordered[0]["candidate_id"] if ordered and kind in _SELECTABLE else None)
        # A proposal exists only when a retained regular observation binds to
        # this exact temporal axis (and, where present, the exact entity
        # grouping).  It is a draft recommendation, never an authority value.
        recommendations["expected_cadence"] = None
        for observed in sorted(groups.get("observed_cadences", []), key=lambda c: c["candidate_id"]):
            observed_value = observed.get("_value") or {}
            interval = observed.get("observed_interval")
            if observed_value.get("cadence") != "regular" or not isinstance(interval, dict):
                continue
            axis = next((candidate for candidate in groups.get("temporals", [])
                         if (candidate.get("_value") or {}).get("axis_id") == observed_value.get("axis_id")), None)
            if axis is None:
                continue
            grouping_columns = {(column.get("table"), column.get("column")) for column in observed_value.get("grouping", []) if isinstance(column, dict)}
            group = next((candidate for candidate in groups.get("entities", [])
                          if {(column.get("table"), column.get("column")) for column in (candidate.get("_value") or {}).get("columns", []) if isinstance(column, dict)} == grouping_columns), None)
            if grouping_columns and group is None:
                continue
            recommendations["expected_cadence"] = {"axis_candidate_id": axis["candidate_id"],
                                                     "grouping_candidate_id": None if group is None else group["candidate_id"],
                                                     "value": {"unit": interval["unit"], "step": interval["step"]}}
            break
        warnings = sorted({code for values in groups.values() for candidate in values for code in candidate["warnings"]})
        public_tables.append({"table": table, "state": "limited" if any(not groups.get(kind) for kind in _SELECTABLE) else "review_required",
                              "candidates": public_groups, "candidate_limits": candidate_limits,
                              "recommendations": recommendations, "confirmed_decisions": confirmed.get(table, {}), "warnings": warnings})
    return {"review_contract_version": "1", "snapshot": {"snapshot_id": item["item_id"]},
            "structure_review_state": state["state"], "materialization": materialization,
            "draft": _public_draft(draft, fingerprint, editable=editable), "tables": public_tables,
            "diagnostic_assistance": [{"diagnostic_id": "D06", "state": "not_adopted",
                                         "message": "This diagnostic does not yet use Dataset Structure selections."}]}


def review_facet(item: dict[str, Any], *, table_id: str, facet: str, q: str | None = None,
                 cursor: str | None = None, offset: int | None = None, limit: int = 25) -> dict[str, Any]:
    """Return a safe, deterministic page of candidates beyond the GET summary cap.

    Contract: ``table_id`` is the reviewed table name, ``facet`` is one of the
    closed facet names, ``q`` is an optional case-insensitive safe-label
    substring, and either opaque numeric ``cursor`` or non-negative ``offset``
    selects a page.  The response never includes internal pins or raw values.
    """
    if facet not in set(_PREDICATES.values()) or not isinstance(table_id, str) or not table_id:
        raise DraftInputError("table_id or facet is invalid")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25:
        raise DraftInputError("limit must be between 1 and 25")
    if q is not None and (not isinstance(q, str) or len(q) > 80):
        raise DraftInputError("q is invalid")
    if cursor is not None:
        if offset is not None or not cursor.startswith("offset:") or not cursor[7:].isdigit():
            raise DraftInputError("cursor is invalid")
        start = int(cursor[7:])
    else:
        start = 0 if offset is None else offset
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        raise DraftInputError("offset is invalid")
    all_tables = {row["table_name"] for row in db.query("dq_item_tables", item_id=item["item_id"])} | set(_artifacts(item))
    if table_id not in all_tables:
        raise DraftInputError("table_id is invalid")
    needle = (q or "").casefold().strip()
    candidates = _artifacts(item).get(table_id, {}).get(facet, [])
    ordered = sorted(candidates, key=lambda candidate: (-candidate["evidence"]["usable_observations"], candidate["candidate_id"]))
    if needle:
        ordered = [candidate for candidate in ordered if needle in candidate["safe_label"].casefold()]
    total, values = len(ordered), ordered[start:start + limit]
    next_offset = start + len(values)
    return {"table_id": table_id, "facet": facet, "q": q or "", "candidates": [
                _public_candidate(value, recommended=(start + index == 0 and facet in _SELECTABLE), rank=start + index + 1)
                for index, value in enumerate(values)],
            "page": {"offset": start, "limit": limit, "returned": len(values), "total": total,
                     "next_offset": next_offset if next_offset < total else None,
                     "next_cursor": f"offset:{next_offset}" if next_offset < total else None}}


def _canonical_selections(value: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"tables"} or not isinstance(value["tables"], list):
        raise DraftInputError("selections must contain only tables")
    candidate_map: dict[str, tuple[str, str]] = {}
    for table in current["tables"]:
        for kind in _SELECTABLE:
            for candidate in table["candidates"][kind]:
                candidate_map[candidate["candidate_id"]] = (table["table"], kind)
    normalised, seen = [], set()
    for selection in value["tables"]:
        if not isinstance(selection, dict) or "table" not in selection or not isinstance(selection["table"], str):
            raise DraftInputError("each selection requires a table")
        table = selection["table"]
        if table in seen or table not in {row["table"] for row in current["tables"]}:
            raise DraftInputError("selection table is invalid")
        seen.add(table)
        action_fields = {f"{field}_{suffix}" for field in _FIELDS for suffix in ("action", "acknowledged")}
        allowed = {"table"} | set(_FIELDS) | action_fields | {"expected_cadence"}
        if set(selection) - allowed:
            raise DraftInputError("Slice 2 accepts entity, temporal, and row-grain selections only")
        saved = {"table": table}
        for field, kind in _FIELDS.items():
            if field not in selection:
                continue
            candidate_id = selection[field]
            if candidate_id is None:
                # Explicit "No applicable selection" is user input, unlike an
                # omitted field which means leave that facet untouched.
                saved[field] = None
            else:
                if not isinstance(candidate_id, str) or candidate_map.get(candidate_id) != (table, kind):
                    raise DraftInputError("candidate does not match current materialized evidence")
                saved[field] = candidate_id
            action_key, acknowledgement_key = f"{field}_action", f"{field}_acknowledged"
            if action_key in selection or acknowledgement_key in selection:
                action = selection.get(action_key, "confirm" if candidate_id is not None else "clear")
                if action not in _DRAFT_CADENCE_ACTIONS:
                    raise DraftInputError("selection action is invalid")
                if action != "confirm" and selection.get(acknowledgement_key) is not True:
                    raise DraftInputError("clear and mark_not_applicable require acknowledgement")
                if action == "confirm" and candidate_id is None:
                    raise DraftInputError("confirm requires a selected candidate")
                saved[action_key] = action
                if action != "confirm": saved[acknowledgement_key] = True
        if "expected_cadence" in selection:
            cadence = selection["expected_cadence"]
            if not isinstance(cadence, dict) or set(cadence) - {"action", "axis_candidate_id", "grouping_candidate_id", "value", "acknowledged"}:
                raise DraftInputError("expected cadence selection is invalid")
            action = cadence.get("action")
            if action not in _DRAFT_CADENCE_ACTIONS:
                raise DraftInputError("expected cadence action is invalid")
            if action in {"clear", "mark_not_applicable"}:
                if cadence.get("acknowledged") is not True:
                    raise DraftInputError("clear and mark_not_applicable require acknowledgement")
                saved["expected_cadence"] = {"action": action, "acknowledged": True}
            else:
                axis, grouping, cadence_value = cadence.get("axis_candidate_id"), cadence.get("grouping_candidate_id"), cadence.get("value")
                if candidate_map.get(axis) != (table, "temporals") or (grouping is not None and candidate_map.get(grouping) != (table, "entities")):
                    raise DraftInputError("expected cadence axis or grouping is invalid")
                if not isinstance(cadence_value, dict) or set(cadence_value) != {"unit", "step"} or cadence_value.get("unit") not in {"day", "week", "month", "quarter", "year"} or not isinstance(cadence_value.get("step"), int) or isinstance(cadence_value.get("step"), bool) or cadence_value["step"] <= 0:
                    raise DraftInputError("expected cadence value is invalid")
                saved["expected_cadence"] = {"action": action, "axis_candidate_id": axis, "grouping_candidate_id": grouping, "value": {"unit": cadence_value["unit"], "step": cadence_value["step"]}}
        normalised.append(saved)
    return {"tables": sorted(normalised, key=lambda row: row["table"])}


def _stale_selections(value: Any, table_names: set[str]) -> dict[str, Any]:
    """Bounded shape validation which deliberately does not reject stale IDs."""
    if not isinstance(value, dict) or set(value) != {"tables"} or not isinstance(value["tables"], list):
        raise DraftInputError("selections must contain only tables")
    rows, seen = [], set()
    for selection in value["tables"]:
        if (not isinstance(selection, dict) or not isinstance(selection.get("table"), str)
                or len(selection["table"]) > 512):
            raise DraftInputError("each selection requires a table")
        table = selection["table"]
        action_fields = {f"{field}_{suffix}" for field in _FIELDS for suffix in ("action", "acknowledged")}
        if table not in table_names or table in seen or set(selection) - ({"table"} | set(_FIELDS) | action_fields | {"expected_cadence"}):
            raise DraftInputError("selection table is invalid")
        seen.add(table)
        row = {"table": table}
        for field in _FIELDS:
            if field not in selection:
                continue
            candidate_id = selection[field]
            if candidate_id is None:
                row[field] = None
            else:
                if not isinstance(candidate_id, str) or not candidate_id.startswith("cand_") or len(candidate_id) > 100:
                    raise DraftInputError("candidate selection is invalid")
                row[field] = candidate_id
            action_key, acknowledgement_key = f"{field}_action", f"{field}_acknowledged"
            action = selection.get(action_key, "confirm" if candidate_id is not None else "clear")
            if action not in _AUTHORITY_ACTIONS:
                raise DraftInputError("selection action is invalid")
            if action == "confirm":
                if not isinstance(candidate_id, str):
                    raise DraftInputError("confirm requires a selected candidate")
            else:
                if candidate_id is not None or selection.get(acknowledgement_key) is not True:
                    raise DraftInputError("clear and mark_not_applicable require acknowledgement")
                row[action_key], row[acknowledgement_key] = action, True
            if action == "confirm" and action_key in selection:
                row[action_key] = action
        if "expected_cadence" in selection:
            cadence = selection["expected_cadence"]
            allowed_cadence = {"action", "axis_candidate_id", "grouping_candidate_id", "value", "acknowledged"}
            if not isinstance(cadence, dict) or set(cadence) - allowed_cadence:
                raise DraftInputError("expected cadence selection is invalid")
            action = cadence.get("action")
            if action not in _AUTHORITY_ACTIONS:
                raise DraftInputError("expected cadence action is invalid")
            if action == "confirm":
                axis, grouping, cadence_value = cadence.get("axis_candidate_id"), cadence.get("grouping_candidate_id"), cadence.get("value")
                if (not isinstance(axis, str) or not axis.startswith("cand_") or len(axis) > 100
                        or (grouping is not None and (not isinstance(grouping, str) or not grouping.startswith("cand_") or len(grouping) > 100))
                        or not isinstance(cadence_value, dict) or set(cadence_value) != {"unit", "step"}
                        or cadence_value.get("unit") not in {"day", "week", "month", "quarter", "year"}
                        or not isinstance(cadence_value.get("step"), int) or isinstance(cadence_value.get("step"), bool)
                        or not 0 < cadence_value["step"] <= 1000000):
                    raise DraftInputError("expected cadence selection is invalid")
                row["expected_cadence"] = {"action": action, "axis_candidate_id": axis,
                                           "grouping_candidate_id": grouping,
                                           "value": {"unit": cadence_value["unit"], "step": cadence_value["step"]}}
            else:
                if cadence.get("acknowledged") is not True:
                    raise DraftInputError("clear and mark_not_applicable require acknowledgement")
                # Empty decisions are closed: stale continuation may retain
                # intent, never an arbitrary unvalidated nested payload.
                if any(key in cadence for key in ("axis_candidate_id", "grouping_candidate_id", "value")):
                    raise DraftInputError("empty expected cadence must not contain a binding")
                row["expected_cadence"] = {"action": action, "acknowledged": True}
        rows.append(row)
    return {"tables": sorted(rows, key=lambda row: row["table"])}


# Slice 3 authority deliberately accepts a compact, closed extension of the
# Slice-2 draft shape.  Candidate IDs are presentation tokens only: each is
# resolved again below to the retained active assertion and its exact pin.
_AUTHORITY_ACTIONS = {"confirm", "clear", "mark_not_applicable"}


class DecisionInputError(DraftInputError):
    pass


class _StaleAuthorityRace(Exception):
    pass


def _publication_basis(item: dict[str, Any]) -> dict[str, Any]:
    """Capture the complete publication fence used by this decision page."""
    marker = db.query_one("dataset_structure_profile_publications", snapshot_id=item["item_id"])
    completion = db.query_one("dataset_structure_profile_publication_completions", snapshot_id=item["item_id"])
    live_item = db.query_one("dq_items", item_id=item["item_id"])
    def pick(row: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
        return None if row is None else {key: row.get(key) for key in keys}
    return {
        "marker": pick(marker, ("snapshot_id", "asset_id", "tenant_id", "generation", "fingerprint")),
        "completion": pick(completion, ("snapshot_id", "asset_id", "tenant_id", "generation", "fingerprint",
                                         "completion_token", "state")),
        "item": pick(live_item, ("item_id", "dataset_family_id", "sourcing_tenant_id",
                                  "profile_publication_attempt_token")),
    }


def _publication_basis_matches(conn: Any, item: dict[str, Any], basis: dict[str, Any]) -> bool:
    """Compare the exact publication tuple inside the immutable write UoW."""
    def row(sql: str) -> dict[str, Any] | None:
        value = conn.execute(sql, (item["item_id"],)).fetchone()
        return None if value is None else dict(value)
    current = {
        "marker": row("SELECT snapshot_id,asset_id,tenant_id,generation,fingerprint FROM dataset_structure_profile_publications WHERE snapshot_id=?"),
        "completion": row("SELECT snapshot_id,asset_id,tenant_id,generation,fingerprint,completion_token,state FROM dataset_structure_profile_publication_completions WHERE snapshot_id=?"),
        "item": row("SELECT item_id,dataset_family_id,sourcing_tenant_id,profile_publication_attempt_token FROM dq_items WHERE item_id=?"),
    }
    if current != basis:
        return False
    marker, completion, live_item = current["marker"], current["completion"], current["item"]
    return bool(marker and completion and live_item
                and marker["asset_id"] == completion["asset_id"] == live_item["dataset_family_id"] == item["dataset_family_id"]
                and marker["tenant_id"] == completion["tenant_id"] == live_item["sourcing_tenant_id"] == item["sourcing_tenant_id"]
                and marker["generation"] == completion["generation"]
                and marker["fingerprint"] == completion["fingerprint"]
                and completion["state"] == "completed")


def _candidate_index(item: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, list[dict[str, Any]]]]]]:
    raw = _artifacts(item)
    indexed: dict[str, dict[str, Any]] = {}
    for table, groups in raw.items():
        for kind, candidates in groups.items():
            for candidate in candidates:
                indexed[candidate["candidate_id"]] = {**candidate, "_table": table, "_kind": kind}
    return indexed, raw


def _action(selection: dict[str, Any], field: str, candidate_id: Any) -> str:
    action = selection.get(field + "_action", "confirm" if candidate_id is not None else "clear")
    if action not in _AUTHORITY_ACTIONS:
        raise DecisionInputError("decision action is invalid")
    if action == "confirm" and not isinstance(candidate_id, str):
        raise DecisionInputError("confirm requires one selected candidate")
    if action != "confirm" and candidate_id is not None:
        raise DecisionInputError("clear and mark_not_applicable cannot select a candidate")
    if action != "confirm" and selection.get(field + "_acknowledged") is not True:
        raise DecisionInputError("clear and mark_not_applicable require acknowledgement")
    return action


def _pin(candidate: dict[str, Any]) -> dict[str, str]:
    value = candidate["_pin"]
    try:
        return {"artifact_id": value["artifact"], "assertion_id": value["assertion"],
                "payload_hash": value["payload"], "dependency_fingerprint": value["dependency"]}
    except (KeyError, TypeError) as exc:
        raise DecisionInputError("candidate pin is incomplete") from exc


def _candidate_for(index: dict[str, dict[str, Any]], *, candidate_id: str, table: str, kind: str) -> dict[str, Any]:
    candidate = index.get(candidate_id)
    if candidate is None or candidate["_table"] != table or candidate["_kind"] != kind:
        raise DecisionInputError("candidate does not belong to this table or selection")
    pin = _pin(candidate)
    # Re-open retained metadata at decision time, not only the projection
    # token.  This closes stale/replaced/tampered evidence races.
    repo = AnalysisArtifactRepository()
    try:
        metadata, payload = repo.get(pin["artifact_id"])
    except Exception as exc:
        raise DecisionInputError("candidate evidence is unavailable") from exc
    if (metadata.status != "active" or metadata.payload_hash != pin["payload_hash"]
            or payload.get("assertion_id") != pin["assertion_id"]
            or payload.get("dependency_fingerprint") != pin["dependency_fingerprint"]):
        raise DecisionInputError("candidate evidence changed")
    return candidate


def _source_ref(candidate: dict[str, Any]) -> dict[str, str]:
    pin = _pin(candidate)
    return {"artifact_id": pin["artifact_id"], "role": "decision", "payload_hash": pin["payload_hash"]}


def _governed_profile_refs(item: dict[str, Any], table: str) -> tuple[list[dict[str, str]], str]:
    """Return exact retained governed-profile witnesses for an empty choice.

    A clear/N/A is a real user decision but it must not quietly borrow an
    arbitrary candidate as its evidence.  The active profile projection that
    was fenced by the publication marker is the witness instead.  The
    snapshot-profile fallback is retained for the compact legacy fixture and
    early one-table snapshots which predate table profile projection.
    """
    repo = AnalysisArtifactRepository()
    preferred = ("table_profile", "table_inventory_profile", "column_profile", "snapshot_profile")
    active = []
    for artifact_type in preferred:
        for metadata in repo.list(snapshot_id=item["item_id"], artifact_type=artifact_type,
                                  status="active", scope="universal"):
            if metadata.asset_id != item["dataset_family_id"]:
                continue
            if artifact_type != "snapshot_profile" and metadata.identity.get("table") != table:
                continue
            active.append(metadata)
        if active:
            break
    if not active:
        raise DecisionInputError("governed profile evidence is unavailable")
    active.sort(key=lambda metadata: metadata.artifact_id)
    return ([{"artifact_id": metadata.artifact_id, "role": "profile", "payload_hash": metadata.payload_hash}
             for metadata in active], "internal")


def _authority_payload(*, item: dict[str, Any], table: str, predicate: str, instance_key: str,
                       action: str, value: dict[str, Any], candidates: list[dict[str, Any]],
                       source_refs: list[dict[str, str]] | None = None,
                       source_sensitivity: str | None = None) -> dict[str, Any]:
    # A decision is intentionally supported only by already materialized DSC
    # evidence.  The evidence contains references/hashes, never source rows.
    refs = list(source_refs or [])
    if not refs:
        for candidate in candidates:
            ref = _source_ref(candidate)
            if ref not in refs:
                refs.append(ref)
    if not refs:
        raise DecisionInputError("a decision requires current materialized evidence")
    locator = {"snapshot": {"asset_id": item["dataset_family_id"], "snapshot_id": item["item_id"]},
               "subject": {"kind": "table", "table": table}, "predicate": predicate,
               "instance_key": instance_key, "action": action, "value": value,
               "pins": sorted(refs, key=lambda row: row["artifact_id"])}
    assertion_id = "dsca_" + payload_hash({"schema_version": 2, "context_version": "2", **locator})
    claim_id = "dscc_" + payload_hash({"assertion_id": assertion_id, "value": value, "action": action})
    evidence_id = "dsce_" + payload_hash({"assertion_id": assertion_id, "source_refs": refs})
    if action == "confirm":
        resolution = {"status": "confirmed", "effective_claim_ids": [claim_id], "reason_codes": [], "conflict_ids": []}
    elif action == "clear":
        resolution = {"status": "unknown", "effective_claim_ids": [], "reason_codes": ["DSC_R_DECISION_CLEARED"], "conflict_ids": []}
    else:
        resolution = {"status": "not_applicable", "effective_claim_ids": [],
                      "reason_codes": ["DSC_R_NOT_APPLICABLE_CONFIRMED"], "conflict_ids": []}
    sensitivity = source_sensitivity or max((candidate.get("_sensitivity", "internal") for candidate in candidates),
                                             default="internal",
                                             key=lambda value: ("external_safe", "internal", "confidential").index(value)
                                             if value in {"external_safe", "internal", "confidential"} else 1)
    return {"schema_version": 2, "artifact_type": _ASSERTION, "assertion_id": assertion_id,
            "context_version": "2", "snapshot": locator["snapshot"], "subject": locator["subject"],
            "predicate": predicate, "instance_key": instance_key, "multiplicity": "single",
            "claims": [{"claim_id": claim_id, "authority": "source_confirmed_structural", "value": value,
                        "evidence_ids": [evidence_id], "decision": {"decision_id": "dscd_" + payload_hash(locator),
                        "action": action, "scope": "source_confirmed_structural"}}],
            "evidence": [{"evidence_id": evidence_id, "kind": "structural_decision", "source_refs": refs,
                          "basis": {"population": table, "total_count": 1, "exclusions": {"physical_null": 0, "confirmed_special": 0, "parse_failure": 0}, "usable_count": 1, "computation": "exact"},
                          "measurements": [{"name": "selected_candidate_count", "count": len(refs)}], "sensitivity": sensitivity}],
            "conflicts": [], "resolution": resolution,
            "dependency_fingerprint": payload_hash({"authority": "dsc-v2", "pins": sorted(refs, key=lambda row: row["artifact_id"]), "value": value}),
            "sensitivity": sensitivity}


def _default_value(kind: str, candidate: dict[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {"kind": kind, "selection": "none"}
    pin = _pin(candidate)
    return {"kind": kind, "candidate_locator": {"predicate": candidate["predicate"], "instance_key": candidate["instance_key"]},
            "candidate_pin": pin}


def _cadence_value(axis: dict[str, Any] | None, grouping: dict[str, Any] | None, value: Any) -> tuple[dict[str, Any], str]:
    if axis is None:
        return {"kind": "expected_cadence", "selection": "none"}, "none"
    if not isinstance(value, dict) or set(value) != {"unit", "step"} or value.get("unit") not in {"day", "week", "month", "quarter", "year"} or not isinstance(value.get("step"), int) or isinstance(value.get("step"), bool) or value["step"] <= 0:
        raise DecisionInputError("expected cadence value is invalid")
    axis_pin = _pin(axis)
    axis_value = axis.get("_value") or {}
    axis_id = axis_value.get("axis_id") or axis.get("instance_key")
    grouping_value = None if grouping is None else {"locator": {"predicate": grouping["predicate"], "instance_key": grouping["instance_key"]}, "pin": _pin(grouping)}
    result = {"kind": "expected_cadence", "unit": value["unit"], "step": value["step"],
              "axis": {"axis_id": axis_id, "locator": {"predicate": axis["predicate"], "instance_key": axis["instance_key"]}, "pin": axis_pin},
              "grouping": grouping_value}
    return result, expected_cadence_instance_key(axis_id, None if grouping is None else grouping_value["locator"])


def _persist_reconfirmation(item: dict[str, Any], *, body: dict[str, Any], selections: dict[str, Any],
                            idempotency_key: str, digest: str, actor: str | None, current: dict[str, Any]) -> dict[str, Any]:
    """Keep a stale confirmation editable without publishing authority."""
    path = f"/api/v2/items/{item['item_id']}/dataset-structure/decisions"
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=? AND tenant_id=?", (item["item_id"], item["sourcing_tenant_id"])).fetchone()
        draft = None if state is None else conn.execute("SELECT * FROM dataset_structure_review_drafts WHERE draft_id=?", (state["current_draft_id"],)).fetchone()
        if state is None or draft is None:
            conn.rollback(); raise DecisionInputError("current draft is unavailable")
        now, next_id, revision = _utc(), "dscd_" + uuid.uuid4().hex, int(draft["revision"]) + 1
        response = {"status": "needs_reconfirmation", "draft_revision": revision,
                    "evidence_fingerprint": current["draft"]["evidence_fingerprint"],
                    "preserved_selections": selections, "changed_dependencies": [], "latest_candidates": []}
        conn.execute("UPDATE dataset_structure_review_drafts SET state='superseded',superseded_by=?,updated_at=? WHERE draft_id=?", (next_id, now, draft["draft_id"]))
        conn.execute("INSERT INTO dataset_structure_review_drafts (draft_id,tenant_id,snapshot_id,revision,base_evidence_fingerprint,selections_json,state,superseded_by,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (next_id, item["sourcing_tenant_id"], item["item_id"], revision, state["current_evidence_fingerprint"], json.dumps(selections, sort_keys=True, separators=(",", ":")), "active", None, actor, now, now))
        conn.execute("UPDATE dataset_structure_review_states SET current_draft_id=?,state='needs_reconfirmation',updated_at=? WHERE snapshot_id=?", (next_id, now, item["item_id"]))
        conn.execute("INSERT INTO dataset_structure_review_decision_batches (batch_id,tenant_id,snapshot_id,draft_id,draft_revision,idempotency_key,request_digest,outcome,decision_assertion_refs_json,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("dscb_" + uuid.uuid4().hex, item["sourcing_tenant_id"], item["item_id"], next_id, revision, idempotency_key, digest, "needs_reconfirmation", "[]", actor, now))
        conn.execute("INSERT INTO dataset_structure_review_idempotency (tenant_id,snapshot_id,idempotency_key,method,path,request_digest,response_json,status_code,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (item["sourcing_tenant_id"], item["item_id"], idempotency_key, "POST", path, digest, json.dumps(response, sort_keys=True, separators=(",", ":")), 200, now))
        conn.commit()
    return response


def save_decisions(item: dict[str, Any], body: dict[str, Any], *, idempotency_key: str, actor: str | None) -> dict[str, Any]:
    """Validate and atomically publish a Slice-3 source-confirmed batch.

    A stale basis is intentionally not exceptional: its exact user input is
    retained in a next draft revision and the caller gets a continuing 200.
    """
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise DecisionInputError("Idempotency-Key is required")
    # Key reuse is the one deliberately higher-priority write error: even an
    # otherwise malformed changed body must not be accepted or leak a second
    # semantic validation path for an already completed request.
    digest = stable_fingerprint(body)
    path = f"/api/v2/items/{item['item_id']}/dataset-structure/decisions"
    previous = db.query_one("dataset_structure_review_idempotency", tenant_id=item["sourcing_tenant_id"], idempotency_key=idempotency_key)
    if previous:
        if previous["method"] == "POST" and previous["path"] == path and previous["request_digest"] == digest:
            return previous["response_json"]
        raise IdempotencyReuse("idempotency_key_reused")
    if not isinstance(body, dict) or set(body) != {"confirm", "draft_revision", "decision_basis", "selections"}:
        raise DecisionInputError("confirm, draft_revision, decision_basis, and selections are required")
    if body["confirm"] is not True or not isinstance(body["draft_revision"], int):
        raise DecisionInputError("an explicit confirm and draft revision are required")
    basis = body["decision_basis"]
    if not isinstance(basis, dict) or set(basis) != {"evidence_fingerprint"} or not isinstance(basis["evidence_fingerprint"], str):
        raise DecisionInputError("decision_basis.evidence_fingerprint is required")
    # Snapshot the publication fence before any candidate/decision validation.
    # The same exact tuple is re-read under save_batch's BEGIN IMMEDIATE
    # guard; a republished profile cannot receive authority from an older UI.
    publication_basis = _publication_basis(item)
    current = review(item)
    if not current["draft"]["editable"]:
        raise DecisionInputError("decisions are available after current materialization succeeds")
    raw_selections = body["selections"]
    if not isinstance(raw_selections, dict) or set(raw_selections) != {"tables"} or not isinstance(raw_selections["tables"], list):
        raise DecisionInputError("selections must contain only tables")
    table_names = {row["table"] for row in current["tables"]}
    # Staleness is tested before resolving opaque candidates so a refreshed
    # page can faithfully retain old selections even after candidates vanish.
    state_row = db.query_one("dataset_structure_review_states", snapshot_id=item["item_id"])
    draft_row = None if state_row is None else db.query_one("dataset_structure_review_drafts", draft_id=state_row.get("current_draft_id"))
    stale = (basis["evidence_fingerprint"] != current["draft"]["evidence_fingerprint"]
             or draft_row is None or body["draft_revision"] != int(draft_row["revision"]))
    if stale:
        # This bounded copy deliberately allows now-stale candidate tokens but
        # rejects unknown tables/oversized values; it never emits authority.
        try:
            preserved = _stale_selections(raw_selections, table_names)
        except DraftInputError as exc:
            raise DecisionInputError(str(exc)) from exc
        return _persist_reconfirmation(item, body=body, selections=preserved, idempotency_key=idempotency_key,
                                       digest=digest, actor=actor, current=current)

    index, raw = _candidate_index(item)
    payloads: list[dict[str, Any]] = []
    normalised: list[dict[str, Any]] = []
    seen_tables: set[str] = set()
    for selection in raw_selections["tables"]:
        if not isinstance(selection, dict) or not isinstance(selection.get("table"), str):
            raise DecisionInputError("each selection requires a table")
        table = selection["table"]
        if table not in table_names or table in seen_tables:
            raise DecisionInputError("selection table is invalid")
        seen_tables.add(table)
        allowed = {"table", "default_entity_candidate_id", "default_temporal_candidate_id", "row_grain_candidate_id",
                   "default_entity_candidate_id_action", "default_temporal_candidate_id_action", "row_grain_candidate_id_action",
                   "default_entity_candidate_id_acknowledged", "default_temporal_candidate_id_acknowledged", "row_grain_candidate_id_acknowledged",
                   "expected_cadence"}
        if set(selection) - allowed:
            raise DecisionInputError("selection contains an unsupported authority field")
        saved: dict[str, Any] = {"table": table}
        selected: dict[str, dict[str, Any] | None] = {}
        for field, kind, predicate, value_kind in (
            ("default_entity_candidate_id", "entities", "table.structure/default_entity_binding", "default_entity_binding"),
            ("default_temporal_candidate_id", "temporals", "table.temporal/default_temporal_binding", "default_temporal_binding"),
        ):
            if field not in selection:
                continue
            candidate_id = selection[field]
            action = _action(selection, field, candidate_id)
            candidate = _candidate_for(index, candidate_id=candidate_id, table=table, kind=kind) if action == "confirm" else None
            support = [candidate] if candidate is not None else []
            source_refs, source_sensitivity = (None, None) if candidate is not None else _governed_profile_refs(item, table)
            payloads.append(_authority_payload(item=item, table=table, predicate=predicate, instance_key="default", action=action,
                                                value=_default_value(value_kind, candidate), candidates=support,
                                                source_refs=source_refs, source_sensitivity=source_sensitivity))
            saved[field] = candidate_id; saved[field + "_action"] = action; selected[kind] = candidate
            if action != "confirm": saved[field + "_acknowledged"] = True
        # Row-grain remains a v1 candidate predicate in the negotiated v2
        # registry.  It is validated as a cross-reference now, but no v2
        # authority assertion is invented for it until its own registry facet
        # is versioned; defaults can therefore never pin an incompatible grain.
        if "row_grain_candidate_id" in selection:
            candidate_id = selection["row_grain_candidate_id"]
            action = _action(selection, "row_grain_candidate_id", candidate_id)
            grain = _candidate_for(index, candidate_id=candidate_id, table=table, kind="row_grains") if action == "confirm" else None
            selected["row_grains"] = grain; saved["row_grain_candidate_id"] = candidate_id; saved["row_grain_candidate_id_action"] = action
            if action != "confirm": saved["row_grain_candidate_id_acknowledged"] = True
        entity, temporal, grain = selected.get("entities"), selected.get("temporals"), selected.get("row_grains")
        if grain is not None and (entity is not None or temporal is not None):
            key_columns = {(col.get("table"), col.get("column")) for col in (grain.get("_value") or {}).get("key_columns", []) if isinstance(col, dict)}
            required = set()
            for candidate in (entity, temporal):
                value = candidate.get("_value") or {}
                for column in value.get("columns", []):
                    if isinstance(column, dict): required.add((column.get("table"), column.get("column")))
            if not required <= key_columns:
                raise DecisionInputError("selected row grain is incompatible with selected entity or temporal binding")
        cadence = selection.get("expected_cadence")
        if cadence is not None:
            if not isinstance(cadence, dict) or set(cadence) - {"action", "axis_candidate_id", "grouping_candidate_id", "value", "acknowledged"}:
                raise DecisionInputError("expected cadence selection is invalid")
            action = cadence.get("action")
            if action not in _AUTHORITY_ACTIONS:
                raise DecisionInputError("expected cadence action is invalid")
            if action != "confirm" and cadence.get("acknowledged") is not True:
                raise DecisionInputError("clear and mark_not_applicable require acknowledgement")
            axis = _candidate_for(index, candidate_id=cadence.get("axis_candidate_id"), table=table, kind="temporals") if action == "confirm" else None
            grouping_id = cadence.get("grouping_candidate_id")
            grouping = (_candidate_for(index, candidate_id=grouping_id, table=table, kind="entities") if isinstance(grouping_id, str) else None)
            if action == "confirm" and grouping_id is not None and grouping is None:
                raise DecisionInputError("expected cadence grouping is invalid")
            value, instance_key = _cadence_value(axis, grouping, cadence.get("value"))
            if action == "confirm":
                observed = raw.get(table, {}).get("observed_cadences", [])
                matches = [candidate for candidate in observed if (candidate.get("_value") or {}).get("cadence") == "regular"
                           and (candidate.get("_value") or {}).get("axis_id") == value["axis"]["axis_id"]]
                if not matches:
                    # User declarations that differ from an observation are
                    # valid, but must still bind to an exact temporal axis.
                    # Absence of a regular observation is represented by a
                    # bounded warning in the response, never a fabricated proposal.
                    saved.setdefault("warnings", []).append("expected_cadence_without_regular_observation")
                elif value["unit"] != (matches[0].get("observed_interval") or {}).get("unit") or value["step"] != (matches[0].get("observed_interval") or {}).get("step"):
                    saved.setdefault("warnings", []).append("expected_cadence_differs_from_observation")
            support = [candidate for candidate in (axis, grouping) if candidate is not None]
            source_refs, source_sensitivity = (None, None) if support else _governed_profile_refs(item, table)
            payloads.append(_authority_payload(item=item, table=table, predicate="table.temporal/expected_cadence", instance_key=instance_key,
                                                action=action, value=value, candidates=support,
                                                source_refs=source_refs, source_sensitivity=source_sensitivity))
            saved["expected_cadence"] = ({"action": action, "acknowledged": True}
                                         if action != "confirm" else cadence)
        normalised.append(saved)
    normalised.sort(key=lambda row: row["table"])
    if not payloads and not any("row_grain_candidate_id" in row for row in normalised):
        raise DecisionInputError("at least one structural decision is required")

    repo = AnalysisArtifactRepository()
    supersessions = []
    for write_index, payload in enumerate(payloads):
        for metadata in repo.list(snapshot_id=item["item_id"], artifact_type=_ASSERTION, status="active", scope="universal"):
            try:
                _meta, old = repo.get(metadata.artifact_id)
            except Exception:
                continue
            if (old.get("context_version") == "2" and old.get("subject", {}).get("table") == payload["subject"]["table"]
                    and old.get("predicate") == payload["predicate"] and old.get("instance_key") == payload["instance_key"]):
                # Exact retries/reconfirmations reuse the immutable authority
                # artifact; a row must never be asked to supersede itself.
                if metadata.payload_hash != payload_hash(payload):
                    supersessions.append({"artifact_id": metadata.artifact_id, "by_write_index": write_index, "actor": actor})
    response: dict[str, Any] = {"status": "confirmed", "draft_revision": int(draft_row["revision"]) + 1,
                                "evidence_fingerprint": current["draft"]["evidence_fingerprint"],
                                "preserved_selections": {"tables": normalised}, "decision_count": len(payloads)}
    now, next_id, batch_id = _utc(), "dscd_" + uuid.uuid4().hex, "dscb_" + uuid.uuid4().hex
    def _commit_workflow(conn: Any, metadata: tuple[Any, ...]) -> None:
        refs = [item.artifact_id for item in metadata]
        response["decision_assertions"] = ["decision_" + hashlib.sha256(ref.encode("utf-8")).hexdigest()[:24] for ref in refs]
        conn.execute("UPDATE dataset_structure_review_drafts SET state='superseded',superseded_by=?,updated_at=? WHERE draft_id=?", (next_id, now, draft_row["draft_id"]))
        conn.execute("INSERT INTO dataset_structure_review_drafts (draft_id,tenant_id,snapshot_id,revision,base_evidence_fingerprint,selections_json,state,superseded_by,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (next_id, item["sourcing_tenant_id"], item["item_id"], response["draft_revision"], state_row["current_evidence_fingerprint"], json.dumps(response["preserved_selections"], sort_keys=True, separators=(",", ":")), "active", None, actor, now, now))
        conn.execute("UPDATE dataset_structure_review_states SET current_draft_id=?,state='confirmed',updated_at=? WHERE snapshot_id=?", (next_id, now, item["item_id"]))
        conn.execute("INSERT INTO dataset_structure_review_decision_batches (batch_id,tenant_id,snapshot_id,draft_id,draft_revision,idempotency_key,request_digest,outcome,decision_assertion_refs_json,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (batch_id, item["sourcing_tenant_id"], item["item_id"], next_id, response["draft_revision"], idempotency_key, digest, "confirmed", json.dumps(refs), actor, now))
        conn.execute("INSERT INTO dataset_structure_review_idempotency (tenant_id,snapshot_id,idempotency_key,method,path,request_digest,response_json,status_code,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (item["sourcing_tenant_id"], item["item_id"], idempotency_key, "POST", path, digest, json.dumps(response, sort_keys=True, separators=(",", ":")), 200, now))
    def _guard(conn: Any = None) -> None:
        # Re-check every decision basis in the repository's own BEGIN
        # IMMEDIATE transaction, immediately before catalogue publication.
        if conn is None:
            return
        state = conn.execute("SELECT current_draft_id,current_evidence_fingerprint FROM dataset_structure_review_states WHERE snapshot_id=? AND tenant_id=?", (item["item_id"], item["sourcing_tenant_id"])).fetchone()
        draft = None if state is None else conn.execute("SELECT revision FROM dataset_structure_review_drafts WHERE draft_id=?", (state["current_draft_id"],)).fetchone()
        if (not _publication_basis_matches(conn, item, publication_basis)
                or state is None or draft is None or int(draft["revision"]) != body["draft_revision"]
                or state["current_evidence_fingerprint"] != state_row["current_evidence_fingerprint"]):
            raise _StaleAuthorityRace()
        for payload in payloads:
            claim = payload["claims"][0]["value"]
            pins = []
            if isinstance(claim.get("candidate_pin"), dict): pins.append((claim["candidate_locator"], claim["candidate_pin"]))
            if isinstance(claim.get("axis"), dict): pins.append((claim["axis"]["locator"], claim["axis"]["pin"]))
            if isinstance(claim.get("grouping"), dict): pins.append((claim["grouping"]["locator"], claim["grouping"]["pin"]))
            for locator, pin in pins:
                row = conn.execute("SELECT status,artifact_type,snapshot_id,asset_id,payload_hash,payload_path FROM analysis_artifacts WHERE artifact_id=?", (pin["artifact_id"],)).fetchone()
                if row is None or row["status"] != "active" or row["artifact_type"] != _ASSERTION or row["snapshot_id"] != item["item_id"] or row["asset_id"] != item["dataset_family_id"] or row["payload_hash"] != pin["payload_hash"]:
                    raise _StaleAuthorityRace()
                try:
                    source = json.loads((repo.root / row["payload_path"]).read_text(encoding="utf-8"))
                except Exception:
                    raise _StaleAuthorityRace()
                if source.get("assertion_id") != pin["assertion_id"] or source.get("dependency_fingerprint") != pin["dependency_fingerprint"] or source.get("predicate") != locator["predicate"] or source.get("instance_key") != locator["instance_key"]:
                    raise _StaleAuthorityRace()
    with _verified_authority_write_scope():
        try:
            repo.save_batch([_assertion_batch_entry(payload, created_by=actor) for payload in payloads],
                            supersessions=supersessions, precommit_guard=_guard, database_callback=_commit_workflow)
        except _StaleAuthorityRace:
            return _persist_reconfirmation(item, body=body, selections={"tables": normalised}, idempotency_key=idempotency_key,
                                           digest=digest, actor=actor, current=current)
    return response


def save_draft(item: dict[str, Any], body: dict[str, Any], *, idempotency_key: str, actor: str | None) -> dict[str, Any]:
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise DraftInputError("Idempotency-Key is required")
    if not isinstance(body, dict) or set(body) != {"draft_revision", "evidence_fingerprint", "selections"}:
        raise DraftInputError("draft_revision, evidence_fingerprint, and selections are required")
    digest = stable_fingerprint(body)
    path = f"/api/v2/items/{item['item_id']}/dataset-structure/draft"
    previous = db.query_one("dataset_structure_review_idempotency", tenant_id=item["sourcing_tenant_id"], idempotency_key=idempotency_key)
    if previous:
        if previous["method"] == "PATCH" and previous["path"] == path and previous["request_digest"] == digest:
            return previous["response_json"]
        raise IdempotencyReuse("idempotency_key_reused")
    current = review(item)
    if not current["draft"]["editable"]:
        raise DraftInputError("drafts are available after current materialization succeeds")
    if not isinstance(body.get("draft_revision"), int):
        raise DraftInputError("draft_revision must be an integer")
    selections = ({"tables": []} if not isinstance(body.get("selections"), dict) else body["selections"])
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM dataset_structure_review_idempotency WHERE tenant_id=? AND idempotency_key=?", (item["sourcing_tenant_id"], idempotency_key)).fetchone()
        if existing:
            if existing["method"] == "PATCH" and existing["path"] == path and existing["request_digest"] == digest:
                conn.commit(); return json.loads(existing["response_json"])
            conn.rollback(); raise IdempotencyReuse("idempotency_key_reused")
        state = conn.execute("SELECT * FROM dataset_structure_review_states WHERE snapshot_id=? AND tenant_id=?", (item["item_id"], item["sourcing_tenant_id"])).fetchone()
        if state is None or not state["current_draft_id"]:
            conn.rollback(); raise DraftInputError("drafts are available after current materialization succeeds")
        draft = conn.execute("SELECT * FROM dataset_structure_review_drafts WHERE draft_id=? AND snapshot_id=? AND tenant_id=?", (state["current_draft_id"], item["item_id"], item["sourcing_tenant_id"])).fetchone()
        if draft is None:
            conn.rollback(); raise DraftInputError("current draft is unavailable")
        evidence_is_current = body.get("evidence_fingerprint") == current["draft"]["evidence_fingerprint"]
        revision_matches = body["draft_revision"] == int(draft["revision"])
        stale = not evidence_is_current or not revision_matches
        # A stale autosave keeps its normalized input in the next editable
        # revision.  This is workflow continuation, never a revision 409.
        if evidence_is_current:
            selections = _canonical_selections(selections, current)
        else:
            selections = _stale_selections(selections, {table["table"] for table in current["tables"]})
        now, next_id, revision = _utc(), "dscd_" + uuid.uuid4().hex, int(draft["revision"]) + 1
        outcome = "needs_reconfirmation" if stale else current["structure_review_state"]
        response = {"status": outcome, "draft_revision": revision,
                    "evidence_fingerprint": current["draft"]["evidence_fingerprint"],
                    "preserved_selections": selections}
        conn.execute("UPDATE dataset_structure_review_drafts SET state='superseded',superseded_by=?,updated_at=? WHERE draft_id=?", (next_id, now, draft["draft_id"]))
        conn.execute("INSERT INTO dataset_structure_review_drafts (draft_id,tenant_id,snapshot_id,revision,base_evidence_fingerprint,selections_json,state,superseded_by,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (next_id, item["sourcing_tenant_id"], item["item_id"], revision,
                      state["current_evidence_fingerprint"],
                      json.dumps(selections, sort_keys=True, separators=(",", ":")), "active", None, actor, now, now))
        conn.execute("UPDATE dataset_structure_review_states SET current_draft_id=?,state=?,updated_at=? WHERE snapshot_id=?", (next_id, outcome, now, item["item_id"]))
        conn.execute("INSERT INTO dataset_structure_review_idempotency (tenant_id,snapshot_id,idempotency_key,method,path,request_digest,response_json,status_code,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (item["sourcing_tenant_id"], item["item_id"], idempotency_key, "PATCH", path, digest,
                      json.dumps(response, sort_keys=True, separators=(",", ":")), 200, now))
        conn.commit()
    return response
