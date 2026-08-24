"""Status machine (ING-07): ``uploading -> profiling -> needs_review ->
ready | failed(reason)``.

Derived from events that actually happened — never advanced by a button
click. See docs/0.4.0/06-ingestion-contract.md §5. The Test Lab consumes
only ``ready`` items.

Precise derivation rule (the contract states the vocabulary; this is the
implementation's exact mechanics, documented since another phase depends on
it): nothing in ING-04 blocks reaching ``ready`` except structural
corruption (``ingest.errors.IngestCorruptionError``) — dictionary/type
warnings are ALL non-blocking. So once profiling completes without a
hard-fail, the item is ``ready`` immediately; there is no separate human
click between "profiled" and "ready". ``needs_review`` therefore never
persists as a resting status under normal profiling — the Review screen
(ING-06) stays reachable at ``ready`` too; it is somewhere a human CAN go
to edit in place, not a gate they MUST pass through first. ``needs_review``
remains a meaningful transient SSE progress phase while a later table is
still being profiled (surfaced by the streaming endpoint's phase names),
which is why it stays in the vocabulary below rather than being dropped.
"""
from __future__ import annotations

VALID = ("uploading", "profiling", "needs_review", "ready", "failed")


def derive(*, has_data_file: bool, profiling_complete: bool, fail_reason: str | None,
           outstanding_confirmations: int = 0) -> str:
    """Pure derivation from observed facts — never an independent variable
    that could drift from what actually happened."""
    if fail_reason:
        return "failed"
    if not has_data_file:
        return "uploading"
    if not profiling_complete:
        return "profiling"
    if outstanding_confirmations:
        return "needs_review"
    return "ready"
