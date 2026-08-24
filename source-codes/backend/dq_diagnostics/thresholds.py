"""FWK-06 — the semantic layer. Thresholds are DATA in `threshold_settings`,
never a Python constant baked into an engine — this module is the only
read/write surface for them. Precedence when resolving the live value for
a (diagnostic, key) pair is engagement > dimension > default (the more
specific scope always wins when it exists; otherwise fall through).
"""
from __future__ import annotations

from typing import Any

import system_db as s


class ThresholdNotFoundError(KeyError):
    """No threshold_settings row exists for (diagnostic_id, key) at ANY
    scope — not even a default. A KeyError subclass so callers that just
    want "is this configured at all" can catch KeyError generically."""


_SCOPES = ("default", "dimension", "engagement")


def _latest_row(**filters: Any) -> dict[str, Any] | None:
    """The most recently written threshold_settings row matching `filters`
    (highest id), so a re-tuned value always wins over its own history —
    `query_one` alone would return arbitrary/insertion order, not "latest"."""
    rows = s.query("threshold_settings", order_by="id DESC", **filters)
    return rows[0] if rows else None


def effective_threshold(
    diagnostic_id: int, key: str, dimension: str | None = None, engagement: str | None = None
) -> dict[str, Any]:
    """Resolve the live value for (diagnostic_id, key).

    Precedence: an engagement-scoped row (only considered when
    `engagement` is given and a matching row exists) beats a
    dimension-scoped row (same condition) beats the default row. Returns
    ``{"value": ..., "source": "default" | "dimension (<ref>)" | "engagement (<ref>)"}``.
    Raises `ThresholdNotFoundError` if nothing is on file at all — callers
    must never fall back to a hardcoded number.
    """
    if engagement is not None:
        row = _latest_row(diagnostic_id=diagnostic_id, key=key, scope="engagement", scope_ref=engagement)
        if row is not None:
            return {"value": row["value_json"], "source": f"engagement ({engagement})"}
    if dimension is not None:
        row = _latest_row(diagnostic_id=diagnostic_id, key=key, scope="dimension", scope_ref=dimension)
        if row is not None:
            return {"value": row["value_json"], "source": f"dimension ({dimension})"}
    row = _latest_row(diagnostic_id=diagnostic_id, key=key, scope="default")
    if row is not None:
        return {"value": row["value_json"], "source": "default"}
    raise ThresholdNotFoundError(f"no threshold on file for diagnostic {diagnostic_id} key {key!r}")


def set_threshold(
    diagnostic_id: int, key: str, value: Any, scope: str, scope_ref: str | None, actor: str
) -> dict[str, Any]:
    """Write a threshold_settings row at `scope` and an append-only
    transaction_log audit row (mirrors routers/admin.py's factory_reset
    audit pattern — every threshold change is attributable to an actor).
    Does not delete/replace any prior row at that scope: `effective_threshold`
    always resolves to the most recently written row for a given
    (diagnostic_id, key, scope, scope_ref), so a re-tune wins over its own
    history while the full history is retained for audit.
    """
    if scope not in _SCOPES:
        raise ValueError(f"invalid scope {scope!r} — must be one of {_SCOPES}")
    ts = s.now_ist()
    row_id = s.insert("threshold_settings", {
        "diagnostic_id": diagnostic_id, "key": key, "value_json": value,
        "scope": scope, "scope_ref": scope_ref, "actor": actor, "ts": ts,
    })
    s.insert("transaction_log", {
        "ts": ts, "actor": actor, "event": "set_threshold",
        "payload": {
            "diagnostic_id": diagnostic_id, "key": key, "value": value,
            "scope": scope, "scope_ref": scope_ref,
        },
    })
    return {"id": row_id, "diagnostic_id": diagnostic_id, "key": key, "value": value,
            "scope": scope, "scope_ref": scope_ref, "actor": actor, "ts": ts}
