"""Phase 4 (0.4.0) — data ingestion redesign (ING-01..ING-10, D-20).

Split out of ``ai/v2/service.py`` so the confidence-tiered mapping, the
structured-warning generation, and the status derivation each have one small,
independently testable module instead of growing the 1700-line service file
further. ``ai/v2/service.py`` calls into this package; nothing here imports
back from ``ai.v2.service`` (one-directional dependency).

See ``docs/0.4.0/06-ingestion-contract.md`` for the authoritative contract.
"""
