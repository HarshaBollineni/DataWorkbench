"""Item-level dictionary state and graceful degradation (ING-05)."""
from __future__ import annotations

# >= 70% of a table's columns must be BOTH exactly matched (mapping tier
# 'high') AND resolve to a concrete classification via the dictionary's
# declared_type, or the dictionary counts as "thin" — even if it names
# every column. Chosen and documented: 70% tolerates a handful of columns
# genuinely outside the dictionary's scope (e.g. system/audit columns)
# without calling a near-empty dictionary "yes".
COVERAGE_FLOOR = 0.7


def compute(*, has_dictionary: bool, total_columns: int, declared_covered: int) -> str:
    """Return ``'yes' | 'thin' | 'absent'``.

    - ``'absent'``: no dictionary file was uploaded for this item.
    - ``'thin'``: a dictionary was uploaded, but fewer than
      ``COVERAGE_FLOOR`` of the dataset's columns ended up with BOTH a
      high-confidence (exact-name) mapping AND a ``declared_type`` that
      resolves to a concrete classification (see
      ``ai/v2/service.py::_classification_from_declared``). A dictionary
      that NAMES every column but never gives a usable TYPE for most of
      them is "thin" even though it looks complete by column-name coverage
      alone — the "looked-complete-but-thin downgrade" the contract calls
      for (docs/0.4.0/06-ingestion-contract.md §4).
    - ``'yes'``: a dictionary was uploaded and >= ``COVERAGE_FLOOR`` of
      columns are both declared and resolved.
    """
    if not has_dictionary:
        return "absent"
    if total_columns <= 0:
        return "thin"
    ratio = declared_covered / total_columns
    return "yes" if ratio >= COVERAGE_FLOOR else "thin"
