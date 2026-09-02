"""T2-D06: Row-completeness reconciliation."""

from .models import (
    DIAGNOSTIC_ID,
    RULE_IDS,
    RULE_TITLES,
    InferenceDisclosure,
    ReconciliationPayload,
    ReportPayload,
    build_external_report_payload,
    mask_facility_identifier,
)
from .api import (
    FrozenRowCompletenessManifest,
    RowCompletenessReportMetadata,
    RowCompletenessResultResponse,
    RowCompletenessRunAccepted,
    RowCompletenessRunRequest,
    RowCompletenessRunStatus,
)
from .engine import (
    ENGINE_VERSION,
    METHODOLOGY_VERSION,
    RowCompletenessInputError,
    evaluate_row_completeness,
)

__all__ = [
    "DIAGNOSTIC_ID",
    "RULE_IDS",
    "RULE_TITLES",
    "InferenceDisclosure",
    "ReconciliationPayload",
    "ReportPayload",
    "build_external_report_payload",
    "mask_facility_identifier",
    "FrozenRowCompletenessManifest",
    "RowCompletenessReportMetadata",
    "RowCompletenessResultResponse",
    "RowCompletenessRunAccepted",
    "RowCompletenessRunRequest",
    "RowCompletenessRunStatus",
    "ENGINE_VERSION",
    "METHODOLOGY_VERSION",
    "RowCompletenessInputError",
    "evaluate_row_completeness",
]
