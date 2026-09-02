"""Shared runtime primitives for supporting analytical capabilities.

This package is intentionally separate from ``dq_diagnostics``: supporting
analyses produce reusable evidence, not extra rows in the diagnostic register.
"""

from .capabilities import (
    SupportingAnalysisCapability,
    get_capability,
    list_capabilities,
    register_capability,
)
from .contracts import (
    AnalysisArtifactMetadata,
    AnalysisScope,
    FrozenAnalysisManifest,
    SnapshotRef,
    SupportingAnalysisResult,
    stable_fingerprint,
    target_fingerprint,
)
from .snapshots import SnapshotLoader

_AAR_REPOSITORY_EXPORTS = {
    "AnalysisArtifactRepository",
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactSaveOutcome",
}
_AAR_TYPE_EXPORTS = {
    "ArtifactTypeDescriptor",
    "get_artifact_type",
    "list_artifact_types",
    "register_artifact_type",
}


def __getattr__(name: str):
    """Resolve legacy AAR exports without creating a package import cycle."""
    if name in _AAR_REPOSITORY_EXPORTS:
        from domains.aar import repository

        value = getattr(repository, name)
    elif name in _AAR_TYPE_EXPORTS:
        from domains.aar import types

        value = getattr(types, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _AAR_REPOSITORY_EXPORTS | _AAR_TYPE_EXPORTS)

__all__ = [
    "AnalysisArtifactMetadata",
    "AnalysisArtifactRepository",
    "AnalysisScope",
    "ArtifactIntegrityError",
    "ArtifactConflictError",
    "ArtifactSaveOutcome",
    "ArtifactTypeDescriptor",
    "FrozenAnalysisManifest",
    "SnapshotLoader",
    "SnapshotRef",
    "SupportingAnalysisCapability",
    "SupportingAnalysisResult",
    "get_capability",
    "list_capabilities",
    "register_capability",
    "register_artifact_type",
    "get_artifact_type",
    "list_artifact_types",
    "stable_fingerprint",
    "target_fingerprint",
]
