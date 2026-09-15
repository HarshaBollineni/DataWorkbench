"""Conservative column-role inference shared by sourcing and diagnostics."""
from __future__ import annotations

import re


def _label(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def infer_inventory_role(
    column_name: str | None,
    classification: str | None,
    target: str | None = None,
    *,
    unique_text: bool = False,
    temporal_evidence: str | None = None,
) -> str:
    """Suggest a bounded role; temporal names alone never establish time."""
    name = _label(column_name)
    normalized_target = _label(target)
    observed = str(classification or "").strip().lower()
    if normalized_target and name == normalized_target:
        return "Target"
    if re.search(r"(^| )(target|label|outcome|default)( |$)", name):
        return "Target"
    if observed == "identifier" or name.endswith(" id") or name == "id" or unique_text:
        return "Identifier"
    if temporal_evidence == "period":
        return "Period"
    if temporal_evidence == "date" or observed in {"date", "datetime", "timestamp"}:
        return "Date"
    if re.search(r"(^| )(score|rating|grade)( |$)", name):
        return "Score"
    return "Feature"
