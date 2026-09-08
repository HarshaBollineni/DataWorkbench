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
from .dataset_structure_context import (
    ASSERTION_ARTIFACT_TYPE,
    CONTEXT_ARTIFACT_TYPE,
    CONTEXT_VERSION,
    ERROR_CODES,
    PREDICATES,
    PROTOCOL_VERSION,
    REASON_CODES,
    RESOLUTION_STATES,
    SCHEMA_VERSION,
    SENSITIVITIES,
    DSCContractError,
    assemble_context,
    assemble_response,
    assertion_identity_key,
    canonical_payload_bytes,
    payload_hash,
    request_fingerprint,
    safe_assertion_summary,
    safe_context_summary,
    validate_assertion_payload,
    validate_context_payload,
    validate_resolution_request,
    validate_response_context,
)

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
    "ASSERTION_ARTIFACT_TYPE",
    "AnalysisArtifactRepository",
    "AnalysisScope",
    "ArtifactIntegrityError",
    "ArtifactConflictError",
    "ArtifactSaveOutcome",
    "ArtifactTypeDescriptor",
    "CONTEXT_ARTIFACT_TYPE",
    "CONTEXT_VERSION",
    "DSCContractError",
    "ERROR_CODES",
    "FrozenAnalysisManifest",
    "SnapshotLoader",
    "SnapshotRef",
    "SupportingAnalysisCapability",
    "SupportingAnalysisResult",
    "PREDICATES",
    "PROTOCOL_VERSION",
    "REASON_CODES",
    "RESOLUTION_STATES",
    "SCHEMA_VERSION",
    "SENSITIVITIES",
    "assemble_context",
    "assemble_response",
    "assertion_identity_key",
    "canonical_payload_bytes",
    "get_capability",
    "list_capabilities",
    "register_capability",
    "register_artifact_type",
    "payload_hash",
    "request_fingerprint",
    "safe_assertion_summary",
    "safe_context_summary",
    "get_artifact_type",
    "list_artifact_types",
    "stable_fingerprint",
    "target_fingerprint",
    "validate_assertion_payload",
    "validate_context_payload",
    "validate_resolution_request",
    "validate_response_context",
]
