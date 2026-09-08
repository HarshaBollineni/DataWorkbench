"""Analysis Artifact Repository domain."""

from .repository import (
    AnalysisArtifactRepository,
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactSaveOutcome,
)
from .types import ArtifactTypeDescriptor, get_artifact_type, list_artifact_types, register_artifact_type
from .dataset_structure_context import (
    DatasetStructureObservationError,
    SUPPORTED_OBSERVER_PREDICATES,
    save_dataset_structure_assertion,
    save_dataset_structure_context,
)
from .dataset_structure_producer import observe_dataset_structure
from .dataset_structure_resolver import resolve_dataset_structure_context

__all__ = [
    "AnalysisArtifactRepository",
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactSaveOutcome",
    "ArtifactTypeDescriptor",
    "get_artifact_type",
    "list_artifact_types",
    "register_artifact_type",
    "SUPPORTED_OBSERVER_PREDICATES",
    "DatasetStructureObservationError",
    "observe_dataset_structure",
    "resolve_dataset_structure_context",
    "save_dataset_structure_assertion",
    "save_dataset_structure_context",
]
