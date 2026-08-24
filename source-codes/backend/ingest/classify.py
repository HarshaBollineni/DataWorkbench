"""Generic, schema-agnostic column classification (ING-10).

Replaces the deleted ``TYPE_PRIORITY`` name-hint table (formerly
``ai/v2/service.py`` lines ~39-47), which matched column NAMES against
literal domain fragments (things like dscr, cltv, noi, or an underscore-id
suffix). No column-name literal and no domain fragment lives here or
anywhere in this package — a source-inspection regression test
(``backend/tests/test_ingest.py``, 4-T8) greps this package and
``ai/v2/service.py`` for the old table's literal fragments and fails if any
reappear.

Classification uses only GENERIC signals, in this priority order:
  1. an explicit target-column flag (the target is a user *choice*, made via
     the dataset's own columns at drop time — not a hardcoded name).
  2. a KB-tagged semantic role, if one is supplied (seam only — see below).
  3. the dictionary's ``declared_type`` string, already resolved by
     ``ai/v2/service.py::_classification_from_declared`` into a concrete
     classification. That function parses generic type VOCABULARY tokens
     ("int", "date", "flag", "ordinal", ...) out of a free-text type
     string — it is not a column-name literal and is intentionally kept.
  4. dtype (pandas' own numeric/datetime detection).
  5. cardinality-based fallbacks: a near-unique column reads as an
     identifier; low-cardinality columns read as binary/categorical;
     everything else is free text.

TODO(Phase 6 / Knowledge Base): once the KB module ships per-column semantic
role tags (RCA Stage 2 governed rules), thread a ``kb_role`` value through
here and prefer it over the generic dtype/cardinality heuristics below —
the ``kb_role`` parameter is already accepted for that purpose, currently
always ``None`` from Phase 4 call sites.
"""
from __future__ import annotations

import warnings

import pandas as pd

# A column is "near-unique" (identifier-shaped) when almost every non-null
# value is distinct. Generic, cardinality-based — no name involved.
_IDENTIFIER_UNIQUE_RATIO = 0.98
_IDENTIFIER_MIN_ROWS = 5
# Above this many distinct values a non-numeric column reads as free text
# rather than a bounded categorical set.
_CATEGORICAL_MAX_UNIQUE = 30
_DATETIME_PARSE_SAMPLE = 200
_DATETIME_PARSE_SUCCESS_FLOOR = 0.9


def looks_like_datetime(series: pd.Series) -> bool:
    """Value-pattern datetime detection (no column-name sniffing).

    Real datetime dtype short-circuits true. Otherwise a sample of the
    non-null values is parsed with ``pd.to_datetime``; a high parse-success
    rate is treated as a date/time pattern. Only attempted for non-numeric
    dtypes — numeric values parse as datetimes far too eagerly (epoch
    seconds/nanoseconds) to be a meaningful signal.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if pd.api.types.is_numeric_dtype(series):
        return False
    clean = series.dropna()
    if clean.empty:
        return False
    sample = clean if len(clean) <= _DATETIME_PARSE_SAMPLE else clean.sample(
        _DATETIME_PARSE_SAMPLE, random_state=0)
    try:
        with warnings.catch_warnings():
            # Best-effort detection on a heterogeneous sample deliberately
            # tolerates mixed/ambiguous formats — pandas' format-inference
            # warning is expected noise here, not a bug to fix.
            warnings.simplefilter("ignore", UserWarning)
            parsed = pd.to_datetime(sample, errors="coerce")
    except (TypeError, ValueError):
        return False
    return bool(parsed.notna().mean() >= _DATETIME_PARSE_SUCCESS_FLOOR)


def looks_like_identifier(series: pd.Series) -> bool:
    """Cardinality-based identifier detection: almost every value is unique.

    Deliberately generic (nunique/count ratio), not a name check — this is
    what lets an identifier-shaped column still classify correctly now that
    the old underscore-id-suffix name-fragment hint is gone.
    """
    clean = series.dropna()
    if len(clean) < _IDENTIFIER_MIN_ROWS:
        return False
    ratio = clean.nunique() / len(clean)
    return ratio >= _IDENTIFIER_UNIQUE_RATIO


def classify(series: pd.Series, *, is_target: bool = False,
             declared: str | None = None, kb_role: str | None = None) -> str:
    """Return one generic classification for ``series``.

    ``declared`` is the already-resolved classification from the dictionary's
    ``declared_type`` string (via ``_classification_from_declared``) — pass
    ``None`` when there is no usable declared type. ``kb_role`` is the
    Phase-6 KB seam described in the module docstring.
    """
    if is_target:
        return "target"
    if kb_role:
        return kb_role
    if declared:
        return declared
    if looks_like_datetime(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        if looks_like_identifier(series):
            return "identifier"
        distinct = series.dropna().unique()
        if len(distinct) <= 2:
            return "binary"
        return "numerical"
    nunique = series.nunique(dropna=True)
    if nunique <= 2:
        return "binary"
    if looks_like_identifier(series):
        return "identifier"
    if nunique <= _CATEGORICAL_MAX_UNIQUE:
        return "categorical"
    return "text"
