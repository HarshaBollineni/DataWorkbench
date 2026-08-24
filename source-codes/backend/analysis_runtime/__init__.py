"""Shared runtime primitives for supporting analytical capabilities.

This package is intentionally separate from ``dq_diagnostics``: supporting
analyses produce reusable evidence, not extra rows in the diagnostic register.
"""

from .artifacts import (AnalysisArtifactRepository, ArtifactConflictError,
                        ArtifactIntegrityError, ArtifactSaveOutcome)
from .artifact_types import (ArtifactTypeDescriptor, get_artifact_type,
                             list_artifact_types, register_artifact_type)
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
