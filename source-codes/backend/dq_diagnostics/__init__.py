"""Phase 3 (0.4.0) — the diagnostic register + semantic layer + staging
skeleton (FWK-04/06/07/08/09/10/17/18; PLT-02; D-04/D-18).

`seed_register` is the boot entrypoint — main.py wires the call the same
way it wires seed_dq_framework()/seed_platform_and_taxonomy() today.
Everything else here is the read/query and execution-skeleton surface
later phases use instead of re-parsing dq_framework_data.json or
re-deriving the guard/staging rules of their own.
"""
from __future__ import annotations

from .delivery import ensure_delivery_defaults, family_deliveries, register_delivery
from .guards import (
    GuardedScope,
    GuardUnavailableError,
    apply_guards,
    class_eligibility,
    material_fields,
    value_semantics,
)
from .profiling_preconditions import grain_uniqueness
from .register import (
    REFUSAL_WORKFLOW_PENDING,
    WorkflowPendingError,
    coverage_map,
    get_diagnostic,
    list_register,
    require_executable,
    seed_register,
)
from .result import DECISION_TYPES, DiagnosticResult, InvalidDiagnosticResultError
from .runner import execute_statistical, run_stage_ordered
from .thresholds import ThresholdNotFoundError, effective_threshold, set_threshold

__all__ = [
    "REFUSAL_WORKFLOW_PENDING",
    "WorkflowPendingError",
    "coverage_map",
    "get_diagnostic",
    "list_register",
    "require_executable",
    "seed_register",
    "DECISION_TYPES",
    "DiagnosticResult",
    "InvalidDiagnosticResultError",
    "GuardedScope",
    "GuardUnavailableError",
    "apply_guards",
    "class_eligibility",
    "material_fields",
    "value_semantics",
    "execute_statistical",
    "run_stage_ordered",
    "ThresholdNotFoundError",
    "effective_threshold",
    "set_threshold",
    "ensure_delivery_defaults",
    "family_deliveries",
    "register_delivery",
    "grain_uniqueness",
]
