"""Compatibility imports for the Dataset Structure Context public seam.

Persistence validation is implemented with the Phase-A producer because it
derives and validates producer-owned evidence identity.  Consumers should use
``domains.aar`` rather than depend on this historical module path.
"""
from .dataset_structure_producer import (
    DatasetStructureObservationError,
    SUPPORTED_OBSERVER_PREDICATES,
    save_dataset_structure_assertion,
    save_dataset_structure_context,
    validate_assertion_write,
    validate_context_write,
)

__all__ = [
    "DatasetStructureObservationError", "SUPPORTED_OBSERVER_PREDICATES",
    "save_dataset_structure_assertion", "save_dataset_structure_context",
    "validate_assertion_write", "validate_context_write",
]
