"""RCA-owned publication seam into the Analytics Artifact Repository.

AAR owns immutable evidence, hashes, and lineage. ``rca_aar_links`` is only
the ordered workflow projection required to resume one active generation and
to discard it cleanly when the user explicitly starts afresh.
"""
from __future__ import annotations

import uuid
from time import perf_counter
from pathlib import Path
from typing import Any

import system_db as s
from analysis_runtime.contracts import stable_fingerprint
from domains.aar.repository import AnalysisArtifactRepository

_METHODOLOGY = stable_fingerprint({"producer": "rca", "evidence_contract": 1})


def _source_artifact_ids(value: Any) -> tuple[str, ...]:
    """Find explicit AAR references in bounded source evidence."""
    found: set[str] = set()

    def visit(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                visit(child, child_key)
        elif isinstance(node, list):
            for child in node:
                visit(child, key)
        elif isinstance(node, str) and (
            key in {"artifact_id", "artifact_ids", "source_artifact_ids"}
            or (key or "").endswith(("_artifact_id", "_artifact_ids"))
        ):
            found.add(node)

    visit(value)
    return tuple(sorted(
        artifact_id for artifact_id in found
        if s.query_one("analysis_artifacts", artifact_id=artifact_id) is not None
    ))


def build_case_context(case: dict, case_file: dict, issue: dict, item: dict) -> dict:
    recorded_at = s.now_ist()
    source_evidence = issue.get("source_evidence") or {}
    return {
        "schema_version": 1,
        "case_id": case["case_id"],
        "workflow_generation": int(case.get("workflow_generation") or 1),
        "recorded_at": recorded_at,
        "issue": {
            "issue_row_id": case["issue_row_id"],
            "test_name": issue.get("test_name"),
            "area_id": issue.get("area_id"),
            "criticality": issue.get("criticality"),
            "columns": issue.get("columns") or issue.get("columns_json") or [],
            "metric": issue.get("metric"),
            "threshold": issue.get("threshold") or issue.get("threshold_json"),
            "violation_count": issue.get("violation_count"),
            "source_evidence": source_evidence,
        },
        "dataset": {
            "asset_id": item.get("dataset_family_id") or case["item_id"],
            "snapshot_id": case["item_id"],
            "name": item.get("name"),
            "kind": item.get("kind"),
            "table": case["table_name"],
            "target_variable": item.get("target_variable"),
            "use_case": item.get("use_case"),
        },
        "intake": {
            "checklist": case_file.get("checklist_json") or {},
            "schema_snapshot": case_file.get("schema_snapshot_json") or {},
            "feature_state_snapshot": (
                (case_file.get("checklist_json") or {}).get("feature_state_snapshot") or {}
            ),
            "tag_snapshot": case_file.get("tags_snapshot_json") or [],
        },
    }


def _entry(case: dict, payload: dict, artifact_type: str, *,
           source_artifact_ids: tuple[str, ...], version: bool) -> dict:
    item = s.query_one("dq_items", item_id=case["item_id"]) or {}
    asset_id = (payload.get("dataset", {}).get("asset_id")
                or item.get("dataset_family_id") or case["item_id"])
    generation = int(case.get("workflow_generation") or 1)
    return {
        "payload": payload,
        "version": version,
        "artifact_type": artifact_type,
        "asset_id": asset_id,
        "snapshot_id": case["item_id"],
        "population_fingerprint": stable_fingerprint({
            "snapshot_id": case["item_id"], "table": case["table_name"]
        }),
        "methodology_fingerprint": _METHODOLOGY,
        "scope": "workflow_local",
        "workflow_id": case["case_id"],
        "owner_id": f"{case['case_id']}:generation:{generation}",
        "table": case["table_name"],
        "features": tuple((payload.get("issue") or {}).get("columns") or ()),
        "source_artifact_ids": source_artifact_ids,
        "identity_inputs": {
            "workflow_generation": generation,
            "event_id": payload.get("event_id"),
        },
        "created_by": payload.get("actor") or case.get("created_by"),
    }


def _save_linked(case: dict, entry: dict, *, evidence_kind: str,
                 stage: str, status: str, recorded_at: str) -> str:
    generation = int(case.get("workflow_generation") or 1)

    def link(conn, artifacts) -> None:
        if evidence_kind == "operation_progress":
            current = conn.execute("SELECT workflow_generation FROM rca_cases WHERE case_id=?",
                                   (case["case_id"],)).fetchone()
            if current is None or int(current[0] or 1) != generation:
                raise ValueError("RCA progress belongs to a superseded generation")
        artifact_id = artifacts[0].artifact_id
        existing = conn.execute(
            "SELECT 1 FROM rca_aar_links WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if existing:
            return
        sequence_no = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM rca_aar_links "
            "WHERE case_id=? AND workflow_generation=?",
            (case["case_id"], generation),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO rca_aar_links "
            "(case_id, workflow_generation, sequence_no, artifact_id, evidence_kind, stage, status, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (case["case_id"], generation, sequence_no, artifact_id,
             evidence_kind, stage, status, recorded_at),
        )

    from domains.rca import progress
    started = perf_counter()
    outcomes = AnalysisArtifactRepository().save_batch([entry], database_callback=link)
    progress.persistence_elapsed(perf_counter() - started)
    return outcomes[0].artifact.artifact_id


def ensure_case_context(case: dict, case_file: dict, issue: dict, item: dict) -> str:
    generation = int(case.get("workflow_generation") or 1)
    existing = s.query_one(
        "rca_aar_links", case_id=case["case_id"], workflow_generation=generation,
        evidence_kind="case_context_created",
    )
    if existing:
        return existing["artifact_id"]
    payload = build_case_context(case, case_file, issue, item)
    refs = _source_artifact_ids(payload["issue"]["source_evidence"])
    return _save_linked(
        case, _entry(case, payload, "rca_case_context",
                     source_artifact_ids=refs, version=False),
        evidence_kind="case_context_created", stage="intake", status="recorded",
        recorded_at=payload["recorded_at"],
    )


def record_event(case: dict, *, evidence_kind: str, stage: str, status: str,
                 actor: str, details: dict[str, Any] | None = None,
                 source_artifact_ids: tuple[str, ...] = ()) -> str:
    from domains.rca import progress
    progress.observe_event(evidence_kind, status)
    generation = int(case.get("workflow_generation") or 1)
    recorded_at = s.now_ist()
    payload = {
        "schema_version": 1, "case_id": case["case_id"],
        "workflow_generation": generation, "event_id": f"rcaevt_{uuid.uuid4().hex[:12]}",
        "evidence_kind": evidence_kind, "stage": stage, "status": status,
        "actor": actor, "recorded_at": recorded_at, "details": details or {},
    }
    context = s.query_one(
        "rca_aar_links", case_id=case["case_id"], workflow_generation=generation,
        evidence_kind="case_context_created",
    )
    refs = tuple(dict.fromkeys(
        ([context["artifact_id"]] if context else []) + list(source_artifact_ids)
    ))
    return _save_linked(
        case, _entry(case, payload, "rca_evidence_event",
                     source_artifact_ids=refs, version=True),
        evidence_kind=evidence_kind, stage=stage, status=status, recorded_at=recorded_at,
    )


def list_case_evidence(case_id: str, workflow_generation: int) -> list[dict]:
    repository = AnalysisArtifactRepository()
    out = []
    for link in s.query("rca_aar_links", order_by="sequence_no", case_id=case_id,
                        workflow_generation=workflow_generation):
        metadata, payload = repository.get(link["artifact_id"])
        details = (payload.get("details")
                   if metadata.artifact_type == "rca_evidence_event" else None)
        if str(link.get("evidence_kind") or "").endswith("sandbox_output"):
            details = {
                "look_id": (details or {}).get("look_id"),
                "hypothesis_id": (details or {}).get("hypothesis_id"),
                "download_artifact_id": metadata.artifact_id,
                "download_format": "text",
            }
        out.append({
            **link, "artifact_type": metadata.artifact_type,
            "payload_hash": metadata.payload_hash,
            "source_artifact_ids": list(metadata.source_artifact_ids),
            "summary": metadata.summary,
            "details": details,
            "created_by": metadata.created_by,
            "integrity_status": metadata.integrity_status,
        })
    return out


def discard_case_evidence(case_id: str) -> list[Path]:
    """Remove prior RCA-owned AAR rows; source diagnostic artifacts survive."""
    repository = AnalysisArtifactRepository()
    with s.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT artifact_id FROM rca_aar_links WHERE case_id=?", (case_id,)
        ).fetchall()
        artifact_ids = [row[0] for row in rows]
        paths: list[Path] = []
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            metadata = conn.execute(
                f"SELECT payload_path FROM analysis_artifacts WHERE artifact_id IN ({placeholders})",
                artifact_ids,
            ).fetchall()
            paths = [repository.root / row[0] for row in metadata]
            conn.execute(
                f"DELETE FROM analysis_artifact_sources WHERE artifact_id IN ({placeholders}) "
                f"OR source_artifact_id IN ({placeholders})", artifact_ids + artifact_ids,
            )
            conn.execute(
                f"DELETE FROM analysis_artifact_events WHERE artifact_id IN ({placeholders})",
                artifact_ids,
            )
            conn.execute(
                f"DELETE FROM analysis_artifacts WHERE artifact_id IN ({placeholders})",
                artifact_ids,
            )
        conn.execute("DELETE FROM rca_aar_links WHERE case_id=?", (case_id,))
        conn.commit()
    for path in paths:
        path.unlink(missing_ok=True)
    return paths
