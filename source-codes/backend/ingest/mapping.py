"""Confidence-tiered dictionary-to-dataset field mapping (ING-03).

Pure logic, no database access — ``ingest.records`` persists what this
module computes.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# ING-03: similarity below this floor is never guessed/applied; at or above
# it, a match is surfaced as "confirm suggestion" but still never silently
# applied. There is no third band — the floor IS the fuzzy/below-floor
# boundary. Chosen and documented here (not a hidden magic number): 0.6
# balances catching real near-misses (e.g. "orig_date" vs "origination_date")
# against not surfacing unrelated columns as suggestions.
FUZZY_FLOOR = 0.6


def _norm(value: object) -> str:
    """Same normalization as ``ai/v2/service.py::_norm_name`` — case, space
    and underscore folding. This is the whole of what the contract's "high |
    exact / alias" tier means: a dictionary column and a dataset column that
    normalize to the same string ARE the same match. No separate synthetic
    alias table is introduced here."""
    return re.sub(r"_+", "_", re.sub(r"[\s\-]+", "_", str(value or "").strip().casefold())).strip("_")


def compute_mapping(dict_rows: list[tuple[str, dict]], dataset_columns: list[str]) -> list[dict]:
    """Confidence-tiered mapping of dictionary rows onto dataset columns.

    ``dict_rows``: the dictionary's declared columns IN FILE ORDER, as
    ``(canonical_field, entry)`` pairs where ``entry`` has ``definition``/
    ``declared_type`` (see ``ai/v2/service.py::_parse_dictionary``). File
    order decides which row gets first claim when two dictionary rows could
    plausibly match the same dataset column (one-to-one enforcement, 4-T3).

    ``dataset_columns``: the dataset's actual column names, in dataset
    order.

    Returns one record per dictionary row::

        {canonical_field, source_column, tier, score, status, confirmed_by,
         definition, declared_type}

    - ``tier='high'``        exact normalized match. ``status='applied'``,
      ``confirmed_by='auto'`` — pre-applied silently (ING-03).
    - ``tier='fuzzy'``       best still-available candidate scores
      ``>= FUZZY_FLOOR`` (``difflib.SequenceMatcher`` ratio on normalized
      names). ``status='confirm_suggestion'``, ``confirmed_by=None`` — never
      pre-applied; a human confirms it (surfaced in the Review screen).
    - ``tier='below_floor'`` best remaining candidate (if any) scores below
      ``FUZZY_FLOOR``. ``source_column`` stays ``None`` — "never guessed".
      ``status='unmapped'``, ``confirmed_by=None``. ``score`` records the
      best candidate seen (for Review-screen transparency) even though it
      was never applied.

    One-to-one: once a dataset column is claimed (tier 'high' or 'fuzzy') it
    is removed from the pool and unavailable to later rows in this same
    call. Duplicate dictionary rows for the identical canonical field are a
    hard-fail raised earlier, during dictionary parsing
    (``ai/v2/service.py::_parse_dictionary`` /
    ``_parse_json_dictionary`` -> ``ingest.errors.IngestCorruptionError``) —
    this function assumes ``dict_rows`` already passed that check.
    """
    pool: dict[str, str] = {}
    for col in dataset_columns:
        pool.setdefault(_norm(col), col)  # first dataset column wins any accidental norm collision

    records: list[dict] = []
    for canonical_field, entry in dict_rows:
        entry = entry or {}
        base = {
            "canonical_field": canonical_field,
            "definition": entry.get("definition", ""),
            "declared_type": entry.get("declared_type", ""),
            "role": entry.get("role", ""),
            "missing_value_codes": entry.get("missing_value_codes", []),
            "business_context": entry.get("business_context", ""),
        }
        dnorm = _norm(canonical_field)
        if dnorm in pool:
            source = pool.pop(dnorm)
            records.append({**base, "source_column": source, "tier": "high",
                            "score": 1.0, "status": "applied", "confirmed_by": "auto"})
            continue

        best_norm, best_col, best_score = None, None, 0.0
        for cand_norm, cand_raw in pool.items():
            score = SequenceMatcher(None, dnorm, cand_norm).ratio()
            if score > best_score:
                best_norm, best_col, best_score = cand_norm, cand_raw, score

        if best_col is not None and best_score >= FUZZY_FLOOR:
            pool.pop(best_norm)
            records.append({**base, "source_column": best_col, "tier": "fuzzy",
                            "score": round(best_score, 3), "status": "confirm_suggestion",
                            "confirmed_by": None})
        else:
            records.append({**base, "source_column": None, "tier": "below_floor",
                            "score": round(best_score, 3) if best_col is not None else None,
                            "status": "unmapped", "confirmed_by": None})
    return records
