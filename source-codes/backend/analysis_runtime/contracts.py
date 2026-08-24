"""Validated, serialization-friendly contracts for supporting analyses."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def stable_fingerprint(value: Any) -> str:
    """Return a deterministic SHA-256 fingerprint for a JSON-shaped value."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def target_fingerprint(target: str | None, *, target_type: str | None = None,
                       positive_class: Any = None) -> str | None:
    """Identify one governed target interpretation, not only its column.

    Binary target labels are canonicalized to strings because the target engines
    use their displayed label when resolving numeric/string-equivalent choices.
    ``positive_class`` must be the *effective* class (after resolving ``auto``).
    """
    if not target:
        return None
    identity: dict[str, Any] = {"target": target}
    if target_type:
        identity["target_type"] = target_type
    if target_type == "binary":
        if positive_class is None:
            raise ValueError("binary target fingerprint requires an effective positive class")
        identity["positive_class"] = str(positive_class)
    return stable_fingerprint(identity)


def _required(name: str, value: str | None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class SnapshotRef:
    snapshot_id: str
    asset_id: str
    system_id: str
    asset_name: str
    kind: str
    version_no: int
    snapshot_label: str | None
    snapshot_status: str
    ingest_status: str
    tables: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "asset_id", "system_id", "asset_name",
                     "kind", "snapshot_status", "ingest_status"):
            _required(name, getattr(self, name))
        if self.kind not in {"dataset", "database"}:
            raise ValueError("kind must be 'dataset' or 'database'")
        if self.version_no < 1:
            raise ValueError("version_no must be positive")
        if len(set(self.tables)) != len(self.tables):
            raise ValueError("tables must not contain duplicates")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["tables"] = list(self.tables)
        return value


@dataclass(frozen=True)
class AnalysisScope:
    table: str
    columns: tuple[str, ...] = ()
    population: dict[str, Any] = field(default_factory=dict)
    target: str | None = None
    target_type: str | None = None
    positive_class: str | int | float | bool | None = None

    def __post_init__(self) -> None:
        _required("table", self.table)
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("columns must not contain duplicates")
        if self.target is not None and not self.target.strip():
            raise ValueError("target cannot be blank")

    @property
    def population_fingerprint(self) -> str:
        return stable_fingerprint(self.population)

    @property
    def target_fingerprint(self) -> str | None:
        return target_fingerprint(
            self.target, target_type=self.target_type,
            positive_class=self.positive_class,
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["columns"] = list(self.columns)
        return value


@dataclass(frozen=True)
class FrozenAnalysisManifest:
    capability_id: str
    capability_version: str
    snapshot: SnapshotRef
    scope: AnalysisScope
    methodology: dict[str, Any]
    source_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required("capability_id", self.capability_id)
        _required("capability_version", self.capability_version)
        if len(set(self.source_artifact_ids)) != len(self.source_artifact_ids):
            raise ValueError("source_artifact_ids must not contain duplicates")

    @property
    def methodology_fingerprint(self) -> str:
        return stable_fingerprint({
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "methodology": self.methodology,
        })

    @property
    def fingerprint(self) -> str:
        return stable_fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "snapshot": self.snapshot.to_dict(),
            "scope": self.scope.to_dict(),
            "methodology": self.methodology,
            "source_artifact_ids": list(self.source_artifact_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FrozenAnalysisManifest":
        snapshot = value["snapshot"]
        scope = value["scope"]
        return cls(
            capability_id=value["capability_id"],
            capability_version=value["capability_version"],
            snapshot=SnapshotRef(**{**snapshot, "tables": tuple(snapshot.get("tables") or ())}),
            scope=AnalysisScope(**{**scope, "columns": tuple(scope.get("columns") or ())}),
            methodology=dict(value.get("methodology") or {}),
            source_artifact_ids=tuple(value.get("source_artifact_ids") or ()),
        )


@dataclass(frozen=True)
class AnalysisArtifactMetadata:
    artifact_id: str
    artifact_type: str
    asset_id: str
    snapshot_id: str
    comparison_snapshot_id: str | None
    population_fingerprint: str
    target_fingerprint: str | None
    feature: str | None
    methodology_fingerprint: str
    scope: str
    workflow_id: str | None
    payload_path: str
    payload_hash: str
    status: str
    source_artifact_ids: tuple[str, ...]
    run_id: str | None
    created_by: str | None
    created_at: str
    superseded_at: str | None = None
    superseded_by_artifact_id: str | None = None
    identity_fingerprint: str | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    summary: dict[str, Any] = field(default_factory=dict)
    summary_adapter_version: str | None = None
    owner_id: str | None = None
    source_artifacts: tuple[dict[str, Any], ...] = ()
    integrity_status: str = "unknown"
    integrity_checked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_artifact_ids"] = list(self.source_artifact_ids)
        value["source_artifacts"] = list(self.source_artifacts)
        return value


@dataclass(frozen=True)
class SupportingAnalysisResult:
    capability_id: str
    artifact: AnalysisArtifactMetadata
    status: str = "complete"
    summary: dict[str, Any] = field(default_factory=dict)
    observations: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    methodology: dict[str, Any] = field(default_factory=dict)
    review_state: str = "open"

    def __post_init__(self) -> None:
        if self.status not in {"complete", "partial", "action_required", "not_applicable", "failed"}:
            raise ValueError(f"invalid supporting-analysis status: {self.status!r}")
        if self.review_state not in {"open", "confirmed", "dismissed", "not_required"}:
            raise ValueError(f"invalid review_state: {self.review_state!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "status": self.status,
            "artifact": self.artifact.to_dict(),
            "summary": self.summary,
            "observations": list(self.observations),
            "warnings": list(self.warnings),
            "methodology": self.methodology,
            "review_state": self.review_state,
        }
