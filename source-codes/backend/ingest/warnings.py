"""Structured, non-blocking dictionary-validation warnings (ING-04).

Every finding is ``{column, code, message}``. None of these ever blocks an
item from reaching ``ready`` — only structural corruption does
(``ingest.errors.IngestCorruptionError``, raised earlier during parsing).
"""
from __future__ import annotations

from ingest.mapping import _norm

# UPL-11: parsing failures become a warning when more than ten percent of
# non-null values fail to parse as the inferred type.
PARSE_FAILURE_RATE_THRESHOLD = 0.10


def mapping_warnings(mapping_records: list[dict], dataset_columns: list[str]) -> list[dict]:
    """``dict_var_not_in_dataset`` and ``dict_field_unused``, derived from one
    table's mapping records (``ingest.mapping.compute_mapping``) plus the
    full set of actual dataset columns.

    For a ``tier='below_floor'`` record (nothing was applied), two different
    situations both produced "no match", and they read very differently to
    a user:

    - the canonical field's normalized name is NOT present anywhere in the
      dataset at all -> the dictionary is simply describing a variable this
      dataset doesn't have -> ``dict_var_not_in_dataset``.
    - the canonical field's normalized name IS present in the dataset, but
      that exact column was already claimed by an earlier dictionary row
      (ING-03 one-to-one enforcement) -> this row's definition/declared_type
      is preserved (still returned by ``compute_mapping``/``get_mapping``)
      but never applied to any inventory row -> ``dict_field_unused``,
      framed "preserved, not used", never silently discarded.
    """
    full_norms = {_norm(c) for c in dataset_columns}
    out: list[dict] = []
    for rec in mapping_records:
        if rec["tier"] != "below_floor":
            continue
        field = rec["canonical_field"]
        if _norm(field) in full_norms:
            out.append({
                "column": field, "code": "dict_field_unused",
                "message": (f"The dictionary's definition for '{field}' is preserved but not used — "
                            "its matching dataset column was already mapped by another dictionary entry."),
            })
        else:
            out.append({
                "column": field, "code": "dict_var_not_in_dataset",
                "message": f"The dictionary declares '{field}', which is not present in the uploaded dataset.",
            })
    return out


def type_conflict_warning(column: str, declared: str | None, observed: str) -> dict | None:
    """``type_conflict``: the dictionary's declared classification and the
    data's observed/inferred classification disagree. The dictionary wins
    (unchanged from the pre-Phase-4 behaviour) — this only surfaces it."""
    if declared and declared != observed:
        return {
            "column": column, "code": "type_conflict",
            "message": (f"The dictionary declares '{column}' as {declared}, but the data behaves like "
                        f"{observed}. The dictionary's classification is used."),
        }
    return None


def unsupported_value_warning(column: str, declared_type_raw: str, declared: str | None) -> dict | None:
    """``unsupported_value``: the dictionary gave a non-blank declared type
    string that doesn't resolve to any recognized classification (see
    ``ai/v2/service.py::_classification_from_declared``)."""
    if declared_type_raw and not declared:
        return {
            "column": column, "code": "unsupported_value",
            "message": (f"The dictionary's declared type '{declared_type_raw}' for '{column}' is not a "
                        "recognized type — the column's classification falls back to inference."),
        }
    return None


def column_all_null_warning(column: str, series) -> dict | None:
    """UPL-11: a fully-null column is reviewable, never a blocking error."""
    if len(series) and series.isna().all():
        return {
            "column": column, "code": "column_all_null",
            "message": f"Column '{column}' contains no non-null values.",
        }
    return None


def column_mixed_type_warning(column: str, series) -> dict | None:
    """UPL-11: surface heterogeneous native/parse signals without blocking."""
    clean = series.dropna()
    if clean.empty:
        return None
    native_types = {type(value).__name__ for value in clean.tolist()}
    numeric_rate = _parse_rate(clean, "numeric")
    date_rate = _parse_rate(clean, "date")
    mixed = len(native_types) > 1 or (0 < numeric_rate < 1) or (0 < date_rate < 1)
    if mixed:
        return {
            "column": column, "code": "column_mixed_type",
            "message": f"Column '{column}' contains mixed value types; review the inferred type.",
        }
    return None


def _parse_rate(series, inferred_type: str) -> float:
    import pandas as pd

    if inferred_type == "numeric":
        parsed = pd.to_numeric(series, errors="coerce")
    elif inferred_type == "date":
        parsed = pd.to_datetime(series, errors="coerce")
    else:
        return 1.0
    return float(parsed.notna().mean()) if len(series) else 1.0


def column_parse_failure_warning(column: str, series, inferred_type: str) -> dict | None:
    """UPL-11: warn when the inferred numeric/date/boolean type has >10% failures."""
    clean = series.dropna()
    if clean.empty or inferred_type in {"categorical", "text", "identifier"}:
        return None
    if inferred_type in {"numerical", "numeric"}:
        parsed = _parse_rate(clean, "numeric")
    elif inferred_type in {"datetime", "date"}:
        parsed = _parse_rate(clean, "date")
    elif inferred_type in {"binary", "boolean"}:
        accepted = {"true", "false", "0", "1", "yes", "no", "y", "n", "t", "f"}
        parsed = float(clean.map(lambda value: str(value).strip().casefold() in accepted).mean())
    else:
        return None
    failure_rate = 1.0 - parsed
    if failure_rate > PARSE_FAILURE_RATE_THRESHOLD:
        return {
            "column": column, "code": "column_parse_failure_rate",
            "message": (f"{failure_rate:.1%} of non-null values in '{column}' fail to parse "
                        f"as the inferred {inferred_type} type."),
        }
    return None
