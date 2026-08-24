"""Registration seam for supporting analyses (missingness, IV, ROC, etc.)."""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from .contracts import FrozenAnalysisManifest, SupportingAnalysisResult


@runtime_checkable
class SupportingAnalysisCapability(Protocol):
    capability_id: str
    name: str
    version: str
    description: str

    def readiness(self, snapshot_id: str, context: dict[str, Any]) -> dict[str, Any]: ...
    def build_manifest(self, snapshot_id: str, context: dict[str, Any]) -> FrozenAnalysisManifest: ...
    def execute(self, manifest: FrozenAnalysisManifest, context: dict[str, Any],
                emit: Callable[[dict[str, Any]], None] | None = None) -> SupportingAnalysisResult: ...


_CAPABILITIES: dict[str, SupportingAnalysisCapability] = {}


def register_capability(capability: SupportingAnalysisCapability) -> SupportingAnalysisCapability:
    capability_id = getattr(capability, "capability_id", "").strip()
    if not capability_id:
        raise ValueError("capability_id must be a non-empty string")
    if capability_id in _CAPABILITIES:
        raise ValueError(f"capability already registered: {capability_id}")
    _CAPABILITIES[capability_id] = capability
    return capability


def get_capability(capability_id: str) -> SupportingAnalysisCapability:
    try:
        return _CAPABILITIES[capability_id]
    except KeyError as exc:
        raise KeyError(f"Unknown supporting-analysis capability: {capability_id}") from exc


def list_capabilities() -> list[dict[str, Any]]:
    rows = [{
        "capability_id": capability.capability_id,
        "name": capability.name,
        "version": capability.version,
        "description": capability.description,
        **(capability.catalog() if callable(getattr(capability, "catalog", None)) else {}),
    } for capability in sorted(_CAPABILITIES.values(), key=lambda value: value.capability_id)]
    return rows


def clear_capabilities_for_tests() -> None:
    _CAPABILITIES.clear()
