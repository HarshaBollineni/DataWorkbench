"""The ONE logging helper for the governed helper catalogue (Phase 2 / PLT-08).

Every invocation that goes through ``ai.test_kit.call`` (and, transitively,
``ai.tool_registry.call``) is logged through :func:`logged_call`: exactly one
DEBUG-level structured line per call, on the module logger
``"archimedes.helpers"``, carrying the helper name, a caller-supplied context
id (a run/case id, or ``"-"`` when the caller has none), the outcome
(``ok``/``error``), and the wall-clock duration in milliseconds. On an
exception the outcome is logged as ``error`` with the exception's class name
and the exception is always re-raised unchanged — this module only observes
calls, it never swallows or alters their outcome.

This is intentionally the single place that formats that log line; nothing
else in ``ai/`` should hand-roll a similar message, so a future change to the
structured-logging contract (e.g. adding a field) is a one-line edit here.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger("archimedes.helpers")


def logged_call(name: str, fn: Callable[..., Any], kwargs: dict[str, Any],
                 *, context_id: str | None = None) -> Any:
    """Invoke ``fn(**kwargs)``, logging one DEBUG line with the helper name,
    ``context_id`` (or ``"-"``), outcome, and duration in milliseconds.

    Returns whatever ``fn`` returns. Re-raises any exception raised by ``fn``
    after logging ``outcome=error`` with the exception's class name — the
    exception itself is never suppressed or replaced.
    """
    ctx = context_id if context_id else "-"
    start = time.perf_counter()
    try:
        result = fn(**kwargs)
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000.0
        logger.debug(
            "helper=%s context_id=%s outcome=error error=%s duration_ms=%.2f",
            name, ctx, type(exc).__name__, duration_ms,
        )
        raise
    duration_ms = (time.perf_counter() - start) * 1000.0
    logger.debug(
        "helper=%s context_id=%s outcome=ok duration_ms=%.2f",
        name, ctx, duration_ms,
    )
    return result
