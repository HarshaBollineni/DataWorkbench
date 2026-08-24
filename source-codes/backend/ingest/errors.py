"""Shared exception types for the ingestion package."""
from __future__ import annotations


class IngestCorruptionError(ValueError):
    """Structural corruption — the ONLY thing ING-04 allows to hard-fail an
    ingestion (blocking the item from ever reaching ``ready``). Examples:
    a data file with no readable columns/tables, or a data dictionary that
    declares the same column twice for the same table. Everything else
    (missing dictionary entries, type disagreements, unsupported declared
    types) is a non-blocking structured warning instead — see
    ``ingest.warnings``.
    """
