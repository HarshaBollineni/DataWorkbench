"""Immutable governed analytical evidence, exact reuse, and bounded lineage."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import system_db as db
from workload_governor import serialized_artifact_write
from .types import get_artifact_type, summarize, validate_identity_contract, validate_payload
from analysis_runtime.contracts import AnalysisArtifactMetadata, stable_fingerprint


class ArtifactIntegrityError(RuntimeError):
    pass


class ArtifactConflictError(RuntimeError):
    """The same analytical identity produced a different deterministic payload."""


def _run_precommit_guard(guard: Callable[..., None] | None, conn: Any = None) -> None:
    if guard is None:
        return
    if conn is not None and len(inspect.signature(guard).parameters):
        guard(conn)
    else:
        guard()


@dataclass(frozen=True)
class ArtifactSaveOutcome:
    artifact: AnalysisArtifactMetadata
    outcome: str  # created | reused | conflicted | versioned
    mismatch: tuple[dict[str, str], ...] = ()


_IDENTITY_FIELDS = ("artifact_type", "asset_id", "snapshot_id", "comparison_snapshot_id", "table",
                    "feature", "features", "population_fingerprint", "target_fingerprint",
                    "methodology_fingerprint", "scope", "owner_id", "workflow_id",
                    "source_artifacts", "identity_inputs")


def artifact_storage_root() -> Path:
    return db.ANALYSIS_ARTIFACT_ROOT


class AnalysisArtifactRepository:
    """SQLite catalogue plus append-only JSON payloads.

    Existing producer keywords remain supported; newer producers can include
    identity inputs and role-bearing sources without core changes.
    """
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else artifact_storage_root()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _metadata(row: dict[str, Any]) -> AnalysisArtifactMetadata:
        refs = row.get("source_artifacts_json") or [{"artifact_id": value, "role": "source"}
                for value in (row.get("source_artifact_ids_json") or [])]
        return AnalysisArtifactMetadata(
            artifact_id=row["artifact_id"], artifact_type=row["artifact_type"], asset_id=row["asset_id"],
            snapshot_id=row["snapshot_id"], comparison_snapshot_id=row.get("comparison_snapshot_id"),
            population_fingerprint=row["population_fingerprint"], target_fingerprint=row.get("target_fingerprint"),
            feature=row.get("feature"), methodology_fingerprint=row["methodology_fingerprint"], scope=row["scope"],
            workflow_id=row.get("workflow_id"), payload_path=row["payload_path"], payload_hash=row["payload_hash"],
            status=row["status"], source_artifact_ids=tuple(row.get("source_artifact_ids_json") or []),
            run_id=row.get("run_id"), created_by=row.get("created_by"), created_at=row["created_at"],
            superseded_at=row.get("superseded_at"), superseded_by_artifact_id=row.get("superseded_by_artifact_id"),
            identity_fingerprint=row.get("identity_fingerprint"), identity=row.get("identity_json") or {},
            schema_version=row.get("schema_version") or 1, summary=row.get("summary_json") or {},
            summary_adapter_version=row.get("summary_adapter_version"), owner_id=row.get("owner_id"),
            source_artifacts=tuple(refs), integrity_status=row.get("integrity_status") or "unknown",
            integrity_checked_at=row.get("integrity_checked_at"),
            payload_media_type=row.get("payload_media_type") or "application/json",
            payload_filename=row.get("payload_filename"),
        )

    @staticmethod
    def _payload_bytes(payload: Any) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          default=str).encode("utf-8")

    @staticmethod
    def _source_refs(ids: Iterable[str], refs: Iterable[dict[str, Any]] | None) -> tuple[dict[str, str], ...]:
        values = list(refs or [{"artifact_id": value, "role": "source"} for value in ids])
        out, seen = [], set()
        for value in values:
            artifact_id, role = str(value.get("artifact_id") or "").strip(), str(value.get("role") or "source").strip()
            if not artifact_id or not role:
                raise ValueError("source artifact references require artifact_id and role")
            if artifact_id in seen:
                raise ValueError("duplicate source artifact reference")
            seen.add(artifact_id); out.append({"artifact_id": artifact_id, "role": role})
        return tuple(sorted(out, key=lambda item: (item["role"], item["artifact_id"])))

    def _identity(self, *, artifact_type: str, asset_id: str, snapshot_id: str, comparison_snapshot_id: str | None,
                  population_fingerprint: str, target_fingerprint: str | None, feature: str | None,
                  methodology_fingerprint: str, scope: str, workflow_id: str | None, owner_id: str | None,
                  table: str | None, features: tuple[str, ...], refs: tuple[dict[str, str], ...],
                  identity_inputs: dict[str, Any] | None) -> dict[str, Any]:
        value = {"artifact_type": artifact_type, "asset_id": asset_id, "snapshot_id": snapshot_id,
                 "comparison_snapshot_id": comparison_snapshot_id, "table": table, "feature": feature,
                 "features": list(features), "population_fingerprint": population_fingerprint,
                 "target_fingerprint": target_fingerprint, "methodology_fingerprint": methodology_fingerprint,
                 "scope": scope, "owner_id": owner_id, "workflow_id": workflow_id,
                 "source_artifacts": list(refs), "identity_inputs": identity_inputs or {}}
        return {key: value[key] for key in _IDENTITY_FIELDS}

    @staticmethod
    def _validate_identity(value: dict[str, Any]) -> None:
        for key in ("artifact_type", "asset_id", "snapshot_id", "population_fingerprint", "methodology_fingerprint", "scope"):
            if not isinstance(value[key], str) or not value[key].strip():
                raise ValueError(f"{key} must be a non-empty string")
        scope, owner, workflow = value["scope"], value["owner_id"], value["workflow_id"]
        if scope == "universal" and (owner or workflow):
            raise ValueError("universal artifacts must not have a local owner")
        if scope == "diagnostic_local" and not owner:
            raise ValueError("diagnostic_local artifacts require owner_id")
        if scope == "workflow_local" and not (owner or workflow):
            raise ValueError("workflow_local artifacts require workflow_id or owner_id")

    def _validate_sources(self, refs: tuple[dict[str, str], ...]) -> tuple[str, ...]:
        source_types = []
        for ref in refs:
            source = db.query_one("analysis_artifacts", artifact_id=ref["artifact_id"])
            if source is None:
                raise ValueError(f"source artifact does not exist: {ref['artifact_id']}")
            source_types.append(source["artifact_type"])
        return tuple(source_types)

    def find_exact(self, *, artifact_type: str, asset_id: str, snapshot_id: str, comparison_snapshot_id: str | None,
                   population_fingerprint: str, target_fingerprint: str | None, feature: str | None,
                   methodology_fingerprint: str, scope: str, workflow_id: str | None,
                   source_artifact_ids: tuple[str, ...] = (), owner_id: str | None = None, table: str | None = None,
                   features: tuple[str, ...] = (), source_artifacts: tuple[dict[str, Any], ...] | None = None,
                   identity_inputs: dict[str, Any] | None = None) -> AnalysisArtifactMetadata | None:
        refs = self._source_refs(source_artifact_ids, source_artifacts)
        identity = self._identity(artifact_type=artifact_type, asset_id=asset_id, snapshot_id=snapshot_id,
            comparison_snapshot_id=comparison_snapshot_id, population_fingerprint=population_fingerprint,
            target_fingerprint=target_fingerprint, feature=feature, methodology_fingerprint=methodology_fingerprint,
            scope=scope, workflow_id=workflow_id, owner_id=owner_id, table=table, features=features, refs=refs,
            identity_inputs=identity_inputs)
        row = db.query_one("analysis_artifacts", identity_fingerprint=stable_fingerprint(identity), status="active")
        if row:
            return self._metadata(row)
        # Legacy exact-match compatibility until historical rows are backfilled.
        rows = db.query("analysis_artifacts", artifact_type=artifact_type, asset_id=asset_id, snapshot_id=snapshot_id,
                        population_fingerprint=population_fingerprint, methodology_fingerprint=methodology_fingerprint,
                        scope=scope, status="active")
        for row in rows:
            # The compatibility comparison is intentionally restricted to
            # pre-governed rows. A modern row with a different full identity
            # (for example, different table or identity_inputs) must not be
            # treated as an exact legacy match merely because the old subset
            # of columns happens to agree.
            if row.get("identity_fingerprint"):
                continue
            item = self._metadata(row)
            if (item.comparison_snapshot_id == comparison_snapshot_id and item.target_fingerprint == target_fingerprint
                    and item.feature == feature and item.workflow_id == workflow_id
                    and item.source_artifact_ids == tuple(ref["artifact_id"] for ref in refs)):
                return item
        return None

    def _get_for_batch_validation(self, artifact_id: str) -> tuple[AnalysisArtifactMetadata, Any]:
        """Read a source without mutating its integrity audit fields.

        Batch preparation is deliberately side-effect free: an invalid source
        aborts before a payload file, catalogue row, or integrity-status update
        is written.
        """
        metadata = self.get_metadata(artifact_id)
        payload_bytes = self._payload_path(metadata).read_bytes()
        if hashlib.sha256(payload_bytes).hexdigest() != metadata.payload_hash:
            raise ArtifactIntegrityError(
                f"Artifact {artifact_id!r} payload hash does not match metadata"
            )
        if metadata.payload_media_type != "application/json":
            return metadata, payload_bytes
        try:
            return metadata, json.loads(payload_bytes)
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError(
                f"Artifact {artifact_id!r} payload is invalid JSON"
            ) from exc

    def _prepare_json_batch_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Validate and materialise one proposed JSON row without mutation."""
        if not isinstance(entry, dict):
            raise TypeError("batch entries must be dictionaries")
        values = dict(entry)
        if "payload" not in values:
            raise ValueError("batch entries require payload")
        payload = values.pop("payload")
        required = ("artifact_type", "asset_id", "snapshot_id", "population_fingerprint",
                    "methodology_fingerprint", "scope")
        missing = [key for key in required if key not in values]
        if missing:
            raise ValueError("batch entry missing required fields: " + ", ".join(missing))
        allowed = set(required) | {
            "version", "artifact_schema_version", "comparison_snapshot_id",
            "target_fingerprint", "feature", "workflow_id", "owner_id", "table",
            "features", "source_artifact_ids", "source_artifacts", "identity_inputs",
            "run_id", "created_by",
        }
        unknown = set(values) - allowed
        if unknown:
            raise TypeError("unsupported batch entry fields: " + ", ".join(sorted(unknown)))
        version = bool(values.get("version", False))
        schema_version = int(values.get("artifact_schema_version", 1))
        refs = self._source_refs(values.get("source_artifact_ids", ()), values.get("source_artifacts"))
        identity = self._identity(
            artifact_type=values["artifact_type"], asset_id=values["asset_id"],
            snapshot_id=values["snapshot_id"],
            comparison_snapshot_id=values.get("comparison_snapshot_id"),
            population_fingerprint=values["population_fingerprint"],
            target_fingerprint=values.get("target_fingerprint"), feature=values.get("feature"),
            methodology_fingerprint=values["methodology_fingerprint"], scope=values["scope"],
            workflow_id=values.get("workflow_id"), owner_id=values.get("owner_id"),
            table=values.get("table"), features=tuple(values.get("features", ())), refs=refs,
            identity_inputs=values.get("identity_inputs"),
        )
        self._validate_identity(identity)
        source_rows: dict[str, dict[str, Any]] = {}
        for ref in refs:
            source = db.query_one("analysis_artifacts", artifact_id=ref["artifact_id"])
            if source is None:
                raise ValueError(f"source artifact does not exist: {ref['artifact_id']}")
            source_rows[ref["artifact_id"]] = source
        source_types = tuple(source_rows[ref["artifact_id"]]["artifact_type"] for ref in refs)
        validate_identity_contract(
            values["artifact_type"], scope=values["scope"],
            target_fingerprint=values.get("target_fingerprint"),
            comparison_snapshot_id=values.get("comparison_snapshot_id"), source_types=source_types,
        )
        validate_payload(values["artifact_type"], payload, schema_version)
        descriptor = get_artifact_type(values["artifact_type"])
        if descriptor is not None and descriptor.write_validator is not None:
            descriptor.write_validator(payload, identity, refs, self._get_for_batch_validation)
        payload_bytes = self._payload_bytes(payload)
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        fingerprint = stable_fingerprint(identity)
        existing = None
        if not version:
            existing = db.query_one("analysis_artifacts", identity_fingerprint=fingerprint, status="active")
            if existing is None:
                existing_metadata = self.find_exact(
                    artifact_type=values["artifact_type"], asset_id=values["asset_id"],
                    snapshot_id=values["snapshot_id"],
                    comparison_snapshot_id=values.get("comparison_snapshot_id"),
                    population_fingerprint=values["population_fingerprint"],
                    target_fingerprint=values.get("target_fingerprint"), feature=values.get("feature"),
                    methodology_fingerprint=values["methodology_fingerprint"], scope=values["scope"],
                    workflow_id=values.get("workflow_id"),
                    source_artifact_ids=tuple(values.get("source_artifact_ids", ())),
                )
                existing = None if existing_metadata is None else {
                    "_metadata": existing_metadata,
                    "payload_hash": existing_metadata.payload_hash,
                }
            if existing is not None:
                metadata = existing.get("_metadata") or self._metadata(existing)
                if metadata.payload_hash != payload_hash:
                    raise ArtifactConflictError("identical artifact identity has a different payload hash")
                return {"metadata": metadata, "outcome": "reused", "refs": refs,
                        "source_statuses": {key: row["status"] for key, row in source_rows.items()}}
        summary, adapter_version = summarize(values["artifact_type"], payload)
        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        relative_path = f"{artifact_id}.json"
        row = {
            "artifact_id": artifact_id, "artifact_type": values["artifact_type"],
            "asset_id": values["asset_id"], "snapshot_id": values["snapshot_id"],
            "comparison_snapshot_id": values.get("comparison_snapshot_id"),
            "population_fingerprint": values["population_fingerprint"],
            "target_fingerprint": values.get("target_fingerprint"), "feature": values.get("feature"),
            "methodology_fingerprint": values["methodology_fingerprint"], "scope": values["scope"],
            "workflow_id": values.get("workflow_id"), "owner_id": values.get("owner_id"),
            "payload_path": relative_path, "payload_hash": payload_hash,
            "payload_media_type": "application/json", "payload_filename": relative_path,
            "status": "active", "source_artifact_ids_json": [ref["artifact_id"] for ref in refs],
            "source_artifacts_json": list(refs),
            "identity_fingerprint": None if version else fingerprint, "identity_json": identity,
            "schema_version": schema_version, "summary_json": summary,
            "summary_adapter_version": adapter_version, "integrity_status": "verified",
            "integrity_checked_at": db.now_ist(), "run_id": values.get("run_id"),
            "created_by": values.get("created_by"), "created_at": db.now_ist(),
            "superseded_at": None, "superseded_by_artifact_id": None,
        }
        return {"row": row, "payload_bytes": payload_bytes, "refs": refs,
                "fingerprint": fingerprint, "version": version,
                "source_statuses": {key: item["status"] for key, item in source_rows.items()}}

    @serialized_artifact_write
    def save_batch(self, entries: Iterable[dict[str, Any]], *,
                   supersessions: Iterable[dict[str, Any]] = (),
                   precommit_guard: Callable[[], None] | None = None,
                   database_callback: Callable[[Any, tuple[AnalysisArtifactMetadata, ...]], None] | None = None) -> tuple[ArtifactSaveOutcome, ...]:
        """Atomically persist a set of JSON artifacts and optional supersessions.

        Each entry is ``{"payload": payload, **save_keyword_arguments}`` using
        the JSON :meth:`save` contract.  A supersession is
        ``{"artifact_id": old_id, "by_artifact_id": new_id|None, "actor": actor}``
        or may use ``by_write_index`` (the zero-based entry position) instead
        of ``by_artifact_id``.  ``precommit_guard`` is called after complete
        side-effect-free preparation, while the writer slot is held, before
        files are staged.  It must raise to abort the whole batch.

        On failure this method rolls back catalogue rows, lineage edges,
        lifecycle events and supersessions, and removes every payload file it
        staged.  Existing exact artifacts are returned as ``reused``; duplicate
        exact entries in one batch reuse the first entry's result.
        """
        # Make a lost fence fail before even an exact-reuse/conflict lookup.
        # The later checks remain necessary to close the race immediately
        # before staging and committing the batch.
        if precommit_guard is not None:
                _run_precommit_guard(precommit_guard)
        prepared = [self._prepare_json_batch_entry(entry) for entry in entries]
        requested_supersessions = [dict(item) for item in supersessions]
        if not prepared and not requested_supersessions:
            # A decision batch can legitimately contain only a durable review
            # acknowledgement (for example, a row-grain confirmation while
            # that facet has no v2 authority predicate).  Let the caller's
            # workflow callback share the same immediate transaction even
            # when this batch has no immutable AAR rows to create.
            with db.get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                _run_precommit_guard(precommit_guard, conn)
                if database_callback is not None:
                    database_callback(conn, ())
                conn.commit()
            return ()
        first_by_fingerprint: dict[str, int] = {}
        for index, item in enumerate(prepared):
            if "row" not in item or item["version"]:
                continue
            first = first_by_fingerprint.get(item["fingerprint"])
            if first is None:
                first_by_fingerprint[item["fingerprint"]] = index
            else:
                leader = prepared[first]
                if leader["row"]["payload_hash"] != item["row"]["payload_hash"]:
                    raise ArtifactConflictError("identical artifact identity has a different payload hash")
                item["batch_reuse"] = first
        for item in requested_supersessions:
            if not isinstance(item.get("artifact_id"), str) or not item["artifact_id"]:
                raise ValueError("batch supersessions require artifact_id")
            if "by_write_index" in item and "by_artifact_id" in item:
                raise ValueError("batch supersession accepts one replacement reference")
            if "by_write_index" in item and (not isinstance(item["by_write_index"], int)
                                              or not 0 <= item["by_write_index"] < len(prepared)):
                raise ValueError("batch supersession by_write_index is invalid")
        _run_precommit_guard(precommit_guard)

        staged: list[Path] = []
        try:
            for index, item in enumerate(prepared):
                if "row" not in item or "batch_reuse" in item:
                    continue
                final_path = self.root / item["row"]["payload_path"]
                temp_path = self.root / f".{item['row']['artifact_id']}.{uuid.uuid4().hex}.tmp"
                try:
                    temp_path.write_bytes(item["payload_bytes"])
                    temp_path.replace(final_path)
                except Exception:
                    temp_path.unlink(missing_ok=True)
                    raise
                staged.append(final_path)
            resolved: dict[int, AnalysisArtifactMetadata] = {}
            outcomes: dict[int, str] = {}
            with db.get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                _run_precommit_guard(precommit_guard, conn)
                for item in prepared:
                    for source_id, expected_status in item["source_statuses"].items():
                        source = db.query_one("analysis_artifacts", conn=conn, artifact_id=source_id)
                        if source is None or source["status"] != expected_status:
                            raise ArtifactIntegrityError("artifact source changed before batch commit")
                for index, item in enumerate(prepared):
                    if "metadata" in item:
                        resolved[index], outcomes[index] = item["metadata"], item["outcome"]
                        continue
                    if "batch_reuse" in item:
                        continue
                    row = item["row"]
                    existing = None if item["version"] else db.query_one(
                        "analysis_artifacts", conn=conn,
                        identity_fingerprint=item["fingerprint"], status="active")
                    if existing is not None:
                        metadata = self._metadata(existing)
                        if metadata.payload_hash != row["payload_hash"]:
                            raise ArtifactConflictError("identical artifact identity has a different payload hash")
                        resolved[index], outcomes[index] = metadata, "reused"
                        (self.root / row["payload_path"]).unlink(missing_ok=True)
                        continue
                    db.insert_analysis_artifact(row, list(item["refs"]), conn=conn)
                    resolved[index] = self._metadata(row)
                    outcomes[index] = "versioned" if item["version"] else "created"
                for index, item in enumerate(prepared):
                    if "batch_reuse" in item:
                        resolved[index] = resolved[item["batch_reuse"]]
                        outcomes[index] = "reused"
                for request in requested_supersessions:
                    replacement = (resolved[request["by_write_index"]].artifact_id
                                   if "by_write_index" in request
                                   else request.get("by_artifact_id"))
                    self._supersede_in_transaction(conn, request["artifact_id"],
                                                    by_artifact_id=replacement, actor=request.get("actor"))
                # A caller may need to commit durable workflow state with the
                # immutable artifact batch.  Running this callback inside the
                # same immediate transaction gives it all-or-nothing catalogue
                # rows, lineage, supersession and caller rows.  Any callback
                # failure follows the normal exception path below, including
                # removal of every staged payload file.
                if database_callback is not None:
                    database_callback(conn, tuple(resolved[index] for index in range(len(prepared))))
                conn.commit()
            return tuple(ArtifactSaveOutcome(resolved[index], outcomes[index])
                         for index in range(len(prepared)))
        except Exception:
            for path in staged:
                path.unlink(missing_ok=True)
            raise

    @serialized_artifact_write
    def save(self, payload: Any, *, version: bool = False, artifact_schema_version: int = 1,
             artifact_type: str, asset_id: str, snapshot_id: str, population_fingerprint: str,
             methodology_fingerprint: str, scope: str, comparison_snapshot_id: str | None = None,
             target_fingerprint: str | None = None, feature: str | None = None, workflow_id: str | None = None,
             owner_id: str | None = None, table: str | None = None, features: tuple[str, ...] = (),
             source_artifact_ids: tuple[str, ...] = (), source_artifacts: tuple[dict[str, Any], ...] | None = None,
             identity_inputs: dict[str, Any] | None = None, run_id: str | None = None,
             created_by: str | None = None,
             precommit_guard: Callable[[], None] | None = None) -> ArtifactSaveOutcome:
        # A fenced worker must be checked even when this is an exact-reuse
        # fast path: reuse is still a publication decision.  The guard is
        # repeated immediately before the durable write below.
        _run_precommit_guard(precommit_guard)
        refs = self._source_refs(source_artifact_ids, source_artifacts)
        identity = self._identity(artifact_type=artifact_type, asset_id=asset_id, snapshot_id=snapshot_id,
            comparison_snapshot_id=comparison_snapshot_id, population_fingerprint=population_fingerprint,
            target_fingerprint=target_fingerprint, feature=feature, methodology_fingerprint=methodology_fingerprint,
            scope=scope, workflow_id=workflow_id, owner_id=owner_id, table=table, features=features, refs=refs,
            identity_inputs=identity_inputs)
        self._validate_identity(identity)
        source_types = self._validate_sources(refs)
        validate_identity_contract(
            artifact_type, scope=scope, target_fingerprint=target_fingerprint,
            comparison_snapshot_id=comparison_snapshot_id,
            source_types=source_types,
        )
        validate_payload(artifact_type, payload, artifact_schema_version)
        descriptor = get_artifact_type(artifact_type)
        if descriptor is not None and descriptor.write_validator is not None:
            # The hook is generic descriptor plumbing, rather than a
            # repository special case: producer-owned contracts can inspect
            # trusted, integrity-checked sources before persistence.
            descriptor.write_validator(payload, identity, refs, self.get)
        payload_bytes = self._payload_bytes(payload); payload_hash = hashlib.sha256(payload_bytes).hexdigest(); fingerprint = stable_fingerprint(identity)
        if not version:
            existing = db.query_one("analysis_artifacts", identity_fingerprint=fingerprint, status="active")
            if existing:
                metadata = self._metadata(existing)
                if metadata.payload_hash != payload_hash:
                    raise ArtifactConflictError("identical artifact identity has a different payload hash")
                return ArtifactSaveOutcome(metadata, "reused")
            legacy = self.find_exact(artifact_type=artifact_type, asset_id=asset_id, snapshot_id=snapshot_id,
                comparison_snapshot_id=comparison_snapshot_id, population_fingerprint=population_fingerprint,
                target_fingerprint=target_fingerprint, feature=feature, methodology_fingerprint=methodology_fingerprint,
                scope=scope, workflow_id=workflow_id, source_artifact_ids=source_artifact_ids)
            if legacy:
                if legacy.payload_hash != payload_hash:
                    raise ArtifactConflictError("identical legacy artifact identity has a different payload hash")
                return ArtifactSaveOutcome(legacy, "reused")
        if precommit_guard is not None:
            _run_precommit_guard(precommit_guard)
        summary, adapter_version = summarize(artifact_type, payload)
        artifact_id, relative_path = f"art_{uuid.uuid4().hex[:12]}", None
        relative_path = f"{artifact_id}.json"; final_path = self.root / relative_path
        temp_path = self.root / f".{artifact_id}.{uuid.uuid4().hex}.tmp"; temp_path.write_bytes(payload_bytes); temp_path.replace(final_path)
        row = {"artifact_id": artifact_id, "artifact_type": artifact_type, "asset_id": asset_id, "snapshot_id": snapshot_id,
            "comparison_snapshot_id": comparison_snapshot_id, "population_fingerprint": population_fingerprint,
            "target_fingerprint": target_fingerprint, "feature": feature, "methodology_fingerprint": methodology_fingerprint,
            "scope": scope, "workflow_id": workflow_id, "owner_id": owner_id, "payload_path": relative_path,
            "payload_hash": payload_hash, "payload_media_type": "application/json",
            "payload_filename": relative_path, "status": "active",
            "source_artifact_ids_json": [ref["artifact_id"] for ref in refs],
            "source_artifacts_json": list(refs), "identity_fingerprint": None if version else fingerprint,
            "identity_json": identity, "schema_version": artifact_schema_version, "summary_json": summary,
            "summary_adapter_version": adapter_version, "integrity_status": "verified", "integrity_checked_at": db.now_ist(),
            "run_id": run_id, "created_by": created_by, "created_at": db.now_ist(), "superseded_at": None,
            "superseded_by_artifact_id": None}
        try:
            # Guard and catalogue insert share one immediate transaction so a
            # lost worker lease cannot pass the guard then publish afterward.
            with db.get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                _run_precommit_guard(precommit_guard, conn)
                db.insert_analysis_artifact(row, list(refs), conn=conn)
                conn.commit()
        except sqlite3.IntegrityError:
            final_path.unlink(missing_ok=True); existing = db.query_one("analysis_artifacts", identity_fingerprint=fingerprint, status="active")
            if existing and not version:
                metadata = self._metadata(existing)
                if metadata.payload_hash == payload_hash:
                    return ArtifactSaveOutcome(metadata, "reused")
                raise ArtifactConflictError("concurrent identical identity write has a different payload hash")
            raise
        except Exception:
            final_path.unlink(missing_ok=True); raise
        return ArtifactSaveOutcome(self._metadata(row), "versioned" if version else "created")

    @serialized_artifact_write
    def save_blob(self, payload_bytes: bytes, summary_payload: Any, *,
                  payload_media_type: str, payload_extension: str,
                  payload_filename: str | None = None, version: bool = False,
                  artifact_schema_version: int = 1, artifact_type: str,
                  asset_id: str, snapshot_id: str, population_fingerprint: str,
                  methodology_fingerprint: str, scope: str,
                  comparison_snapshot_id: str | None = None,
                  target_fingerprint: str | None = None, feature: str | None = None,
                  workflow_id: str | None = None, owner_id: str | None = None,
                  table: str | None = None, features: tuple[str, ...] = (),
                  source_artifact_ids: tuple[str, ...] = (),
                  source_artifacts: tuple[dict[str, Any], ...] | None = None,
                  identity_inputs: dict[str, Any] | None = None,
                  run_id: str | None = None,
                  created_by: str | None = None) -> ArtifactSaveOutcome:
        """Persist an immutable non-JSON payload with a governed JSON summary.

        Binary evidence uses the same identity, collision, lineage, and hash
        contract as JSON evidence.  Only a conservative extension token is
        accepted; callers never control a storage path.
        """
        if not isinstance(payload_bytes, bytes) or not payload_bytes:
            raise ValueError("binary artifact payload must be non-empty bytes")
        extension = str(payload_extension or "").lower().lstrip(".")
        if extension not in {"parquet"}:
            raise ValueError("unsupported binary artifact extension")
        if payload_media_type != "application/vnd.apache.parquet":
            raise ValueError("unsupported binary artifact media type")
        descriptor = get_artifact_type(artifact_type)
        if descriptor is not None and not descriptor.allows_blob_payload:
            raise ValueError(f"artifact type {artifact_type!r} does not allow blob payloads")
        refs = self._source_refs(source_artifact_ids, source_artifacts)
        identity = self._identity(
            artifact_type=artifact_type, asset_id=asset_id, snapshot_id=snapshot_id,
            comparison_snapshot_id=comparison_snapshot_id,
            population_fingerprint=population_fingerprint,
            target_fingerprint=target_fingerprint, feature=feature,
            methodology_fingerprint=methodology_fingerprint, scope=scope,
            workflow_id=workflow_id, owner_id=owner_id, table=table,
            features=features, refs=refs, identity_inputs=identity_inputs,
        )
        self._validate_identity(identity)
        source_types = self._validate_sources(refs)
        validate_identity_contract(
            artifact_type, scope=scope, target_fingerprint=target_fingerprint,
            comparison_snapshot_id=comparison_snapshot_id, source_types=source_types,
        )
        validate_payload(artifact_type, summary_payload, artifact_schema_version)
        if descriptor is not None and descriptor.write_validator is not None:
            descriptor.write_validator(summary_payload, identity, refs, self.get)
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        fingerprint = stable_fingerprint(identity)
        if not version:
            existing = db.query_one(
                "analysis_artifacts", identity_fingerprint=fingerprint, status="active",
            )
            if existing:
                metadata = self._metadata(existing)
                if metadata.payload_hash != payload_hash:
                    raise ArtifactConflictError(
                        "identical artifact identity has a different payload hash"
                    )
                return ArtifactSaveOutcome(metadata, "reused")
        summary, adapter_version = summarize(artifact_type, summary_payload)
        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        relative_path = f"{artifact_id}.{extension}"
        final_path = self.root / relative_path
        temp_path = self.root / f".{artifact_id}.{uuid.uuid4().hex}.tmp"
        temp_path.write_bytes(payload_bytes)
        temp_path.replace(final_path)
        safe_filename = Path(payload_filename or relative_path).name
        row = {
            "artifact_id": artifact_id, "artifact_type": artifact_type,
            "asset_id": asset_id, "snapshot_id": snapshot_id,
            "comparison_snapshot_id": comparison_snapshot_id,
            "population_fingerprint": population_fingerprint,
            "target_fingerprint": target_fingerprint, "feature": feature,
            "methodology_fingerprint": methodology_fingerprint, "scope": scope,
            "workflow_id": workflow_id, "owner_id": owner_id,
            "payload_path": relative_path, "payload_hash": payload_hash,
            "payload_media_type": payload_media_type,
            "payload_filename": safe_filename, "status": "active",
            "source_artifact_ids_json": [ref["artifact_id"] for ref in refs],
            "source_artifacts_json": list(refs),
            "identity_fingerprint": None if version else fingerprint,
            "identity_json": identity, "schema_version": artifact_schema_version,
            "summary_json": summary, "summary_adapter_version": adapter_version,
            "integrity_status": "verified", "integrity_checked_at": db.now_ist(),
            "run_id": run_id, "created_by": created_by,
            "created_at": db.now_ist(), "superseded_at": None,
            "superseded_by_artifact_id": None,
        }
        try:
            db.insert_analysis_artifact(row, list(refs))
        except sqlite3.IntegrityError:
            final_path.unlink(missing_ok=True)
            existing = db.query_one(
                "analysis_artifacts", identity_fingerprint=fingerprint, status="active",
            )
            if existing and not version:
                metadata = self._metadata(existing)
                if metadata.payload_hash == payload_hash:
                    return ArtifactSaveOutcome(metadata, "reused")
                raise ArtifactConflictError(
                    "concurrent identical identity write has a different payload hash"
                )
            raise
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        return ArtifactSaveOutcome(
            self._metadata(row), "versioned" if version else "created"
        )

    def save_or_reuse(self, payload: Any, **kwargs: Any) -> tuple[AnalysisArtifactMetadata, bool]:
        try:
            outcome = self.save(payload, **kwargs)
            return outcome.artifact, outcome.outcome == "reused"
        except ArtifactConflictError:
            # Compatibility only for pre-governed callers whose historic scope
            # was an arbitrary table/capability string. New standard scopes use
            # the conflict contract above and can never silently reuse output.
            if kwargs.get("scope") in {"universal", "diagnostic_local", "workflow_local"}:
                raise
            existing = self.find_exact(**{key: kwargs.get(key) for key in (
                "artifact_type", "asset_id", "snapshot_id", "comparison_snapshot_id",
                "population_fingerprint", "target_fingerprint", "feature",
                "methodology_fingerprint", "scope", "workflow_id",
            )}, source_artifact_ids=kwargs.get("source_artifact_ids", ()))
            if existing is None:
                raise
            return existing, True

    def save_version(self, payload: Any, **kwargs: Any) -> AnalysisArtifactMetadata:
        return self.save(payload, version=True, **kwargs).artifact

    def get_metadata(self, artifact_id: str, *, conn: sqlite3.Connection | None = None) -> AnalysisArtifactMetadata:
        row = db.query_one("analysis_artifacts", conn=conn, artifact_id=artifact_id)
        if row is None: raise KeyError(f"Unknown analysis artifact: {artifact_id}")
        return self._metadata(row)

    def _payload_path(self, metadata: AnalysisArtifactMetadata) -> Path:
        root = self.root.resolve(); path = (root / metadata.payload_path).resolve()
        expected_suffix = (".json" if metadata.payload_media_type == "application/json"
                           else ".parquet" if metadata.payload_media_type == "application/vnd.apache.parquet"
                           else None)
        if path.parent != root or expected_suffix is None or path.suffix != expected_suffix:
            raise ArtifactIntegrityError(f"Artifact {metadata.artifact_id!r} has an invalid payload path")
        return path

    def _mark_integrity(self, artifact_id: str, status: str, *, conn: sqlite3.Connection | None = None) -> None:
        db.update("analysis_artifacts", {"artifact_id": artifact_id}, {
            "integrity_status": status, "integrity_checked_at": db.now_ist(),
        }, conn=conn)

    def get(self, artifact_id: str, *, conn: sqlite3.Connection | None = None) -> tuple[AnalysisArtifactMetadata, Any]:
        metadata = self.get_metadata(artifact_id, conn=conn)
        try: payload_bytes = self._payload_path(metadata).read_bytes()
        except FileNotFoundError as exc:
            self._mark_integrity(artifact_id, "missing", conn=conn); raise ArtifactIntegrityError(f"Artifact {artifact_id!r} payload is missing") from exc
        except ArtifactIntegrityError:
            self._mark_integrity(artifact_id, "invalid_path", conn=conn); raise
        if hashlib.sha256(payload_bytes).hexdigest() != metadata.payload_hash:
            self._mark_integrity(artifact_id, "tampered", conn=conn); raise ArtifactIntegrityError(f"Artifact {artifact_id!r} payload hash does not match metadata")
        if metadata.payload_media_type != "application/json":
            self._mark_integrity(artifact_id, "verified", conn=conn)
            return self.get_metadata(artifact_id, conn=conn), payload_bytes
        try: payload = json.loads(payload_bytes)
        except json.JSONDecodeError as exc:
            self._mark_integrity(artifact_id, "invalid_json", conn=conn); raise ArtifactIntegrityError(f"Artifact {artifact_id!r} payload is invalid JSON") from exc
        self._mark_integrity(artifact_id, "verified", conn=conn)
        return self.get_metadata(artifact_id, conn=conn), payload

    def read_bytes(self, artifact_id: str, *, conn: sqlite3.Connection | None = None) -> tuple[AnalysisArtifactMetadata, bytes]:
        metadata = self.get_metadata(artifact_id, conn=conn)
        try:
            payload = self._payload_path(metadata).read_bytes()
        except FileNotFoundError as exc:
            self._mark_integrity(artifact_id, "missing", conn=conn)
            raise ArtifactIntegrityError(f"Artifact {artifact_id!r} payload is missing") from exc
        if hashlib.sha256(payload).hexdigest() != metadata.payload_hash:
            self._mark_integrity(artifact_id, "tampered", conn=conn)
            raise ArtifactIntegrityError(
                f"Artifact {artifact_id!r} payload hash does not match metadata"
            )
        self._mark_integrity(artifact_id, "verified", conn=conn)
        return self.get_metadata(artifact_id, conn=conn), payload

    def list(self, *, asset_id: str | None = None, snapshot_id: str | None = None, artifact_type: str | None = None,
             status: str | None = None, scope: str | None = None, feature: str | None = None, run_id: str | None = None,
             workflow_id: str | None = None, feature_query: str | None = None) -> list[AnalysisArtifactMetadata]:
        values = {"asset_id": asset_id, "snapshot_id": snapshot_id, "artifact_type": artifact_type, "status": status,
                  "scope": scope, "feature": feature, "run_id": run_id, "workflow_id": workflow_id}
        rows = [self._metadata(row) for row in db.query("analysis_artifacts", order_by="created_at DESC, artifact_id DESC", **{k:v for k,v in values.items() if v is not None})]
        needle = (feature_query or "").strip().lower()
        return [row for row in rows if needle in (row.feature or "").lower()] if needle else rows

    def page(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> dict[str, Any]:
        limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
        feature_query = str(filters.pop("feature_query", "") or "").strip()
        values = {key: value for key, value in filters.items() if value is not None}
        rows, total = db.query_page(
            "analysis_artifacts", limit=limit, offset=offset,
            order_by=(("created_at", "DESC"), ("artifact_id", "DESC")),
            contains={"feature": feature_query} if feature_query else None,
            **values,
        )
        return {"artifacts": [self._metadata(row).to_dict() for row in rows],
                "limit": limit, "offset": offset, "total": total}

    def diagnose_mismatch(self, **candidate: Any) -> list[dict[str, Any]]:
        fields = ("snapshot_id", "comparison_snapshot_id", "population_fingerprint", "target_fingerprint", "feature", "methodology_fingerprint", "scope", "workflow_id")
        rows = self.list(asset_id=candidate.get("asset_id"), artifact_type=candidate.get("artifact_type"), status="active")
        return [{"artifact_id": item.artifact_id, "mismatches": [{"field": field, "reason": field.replace("_fingerprint", "").replace("_", " ") + " mismatch"} for field in fields if getattr(item, field) != candidate.get(field)]} for item in rows[:50] if any(getattr(item, field) != candidate.get(field) for field in fields)]

    def dependants(self, artifact_id: str, *, transitive: bool = False, limit: int = 500) -> list[AnalysisArtifactMetadata]:
        self.get_metadata(artifact_id); result, queue, seen = [], [artifact_id], {artifact_id}
        while queue and len(result) < limit:
            parent = queue.pop(0)
            edges = db.query("analysis_artifact_sources", source_artifact_id=parent,
                             order_by="artifact_id")
            for edge in edges:
                child_id = edge["artifact_id"]
                if child_id in seen: continue
                seen.add(child_id); child = self.get_metadata(child_id); result.append(child)
                if transitive: queue.append(child.artifact_id)
                if len(result) >= limit: break
        return result

    def ancestors(self, artifact_id: str, *, limit: int = 500) -> list[AnalysisArtifactMetadata]:
        result, queue, seen = [], [artifact_id], {artifact_id}
        while queue and len(result) < limit:
            for parent_id in self.get_metadata(queue.pop(0)).source_artifact_ids:
                if parent_id in seen: continue
                seen.add(parent_id); parent = self.get_metadata(parent_id); result.append(parent); queue.append(parent_id)
        return result

    def impact(self, artifact_id: str) -> list[AnalysisArtifactMetadata]: return self.dependants(artifact_id)

    def lineage(self, artifact_id: str, *, limit: int = 200) -> dict[str, Any]:
        return {"artifact": self.get_metadata(artifact_id).to_dict(), "ancestors": [r.to_dict() for r in self.ancestors(artifact_id, limit=limit)], "dependants": [r.to_dict() for r in self.dependants(artifact_id, transitive=True, limit=limit)]}

    def overview(self, *, asset_id: str | None = None, snapshot_id: str | None = None) -> dict[str, Any]:
        rows = self.list(asset_id=asset_id, snapshot_id=snapshot_id); active = [r for r in rows if r.status == "active"]
        runs = db.query("analysis_manifests", snapshot_id=snapshot_id) if snapshot_id else []
        warnings = [r for r in rows if r.integrity_status not in {"verified", "unknown"}]
        return {"active_artifacts": len(active), "universal_artifacts": sum(r.scope == "universal" for r in active), "diagnostic_local_artifacts": sum(r.scope == "diagnostic_local" for r in active), "workflow_local_artifacts": sum(r.scope == "workflow_local" for r in active), "superseded_artifacts": sum(r.status == "superseded" for r in rows), "retained_runs": len(runs), "represented_features": len({r.feature for r in rows if r.feature}), "artifact_types": len({r.artifact_type for r in rows}), "represented_snapshots": len({r.snapshot_id for r in rows}), "latest_creation_time": rows[0].created_at if rows else None, "integrity_status": "warning" if warnings else "healthy", "integrity_warnings": len(warnings)}

    def integrity_audit(self, *, limit: int = 200) -> dict[str, Any]:
        all_rows = self.list()
        audit_limit = max(1, min(limit, 1000))
        rows, issues = all_rows[:audit_limit], []
        for row in rows:
            try: self.get(row.artifact_id)
            except ArtifactIntegrityError as exc: issues.append({"artifact_id": row.artifact_id, "reason": str(exc)})
        # Orphan detection must compare against the complete catalogue even
        # when payload verification itself is bounded. Otherwise every valid
        # payload after the audit window is reported as an orphan.
        referenced = {r.payload_path for r in all_rows}
        orphans = [p.name for p in self.root.resolve().glob("*.json") if p.name not in referenced][:audit_limit]
        return {"checked": len(rows), "issues": issues, "orphan_payload_files": orphans,
                "status": "warning" if issues or orphans else "healthy",
                "bounded": len(all_rows) > audit_limit}

    def _supersede_in_transaction(self, conn: Any, artifact_id: str, *, by_artifact_id: str | None = None,
                                   actor: str | None = None) -> None:
        current = db.query_one("analysis_artifacts", conn=conn, artifact_id=artifact_id)
        if current is None:
            raise KeyError(f"Unknown analysis artifact: {artifact_id}")
        if current["status"] != "active":
            return
        if by_artifact_id is not None:
            if db.query_one("analysis_artifacts", conn=conn, artifact_id=by_artifact_id) is None:
                raise KeyError(f"Unknown analysis artifact: {by_artifact_id}")
            if by_artifact_id == artifact_id: raise ValueError("an artifact cannot supersede itself")
        now = db.now_ist()
        db.update("analysis_artifacts", {"artifact_id": artifact_id}, {"status": "superseded", "superseded_at": now, "superseded_by_artifact_id": by_artifact_id}, conn=conn)
        db.insert("analysis_artifact_events", {"event_id": f"artevt_{uuid.uuid4().hex[:12]}", "artifact_id": artifact_id, "event_type": "superseded", "actor": actor, "detail_json": {"superseded_by_artifact_id": by_artifact_id}, "created_at": now}, conn=conn)

    @serialized_artifact_write
    def supersede(self, artifact_id: str, *, by_artifact_id: str | None = None, actor: str | None = None,
                  precommit_guard: Callable[[], None] | None = None) -> AnalysisArtifactMetadata:
        with db.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _run_precommit_guard(precommit_guard, conn)
            self._supersede_in_transaction(conn, artifact_id, by_artifact_id=by_artifact_id, actor=actor)
            conn.commit()
        return self.get_metadata(artifact_id)
