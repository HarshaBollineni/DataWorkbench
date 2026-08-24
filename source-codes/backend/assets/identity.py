"""backend/assets/identity.py — AST-01/02/04, P-02/P-03/P-10.

The human-quotable ID scheme for the 0.5.0 asset model: a small per-scope
counter table (``id_sequences``), an alias validator, and the display-name
composer. Distinct from ``dq_items.item_id`` — the internal UUID-fragment
primary key (``ai/v2/service.py``'s ``_id()``) — which this module never
touches: it never collided and never needed a reset (AST-01).

Exactly five scopes exist (A-Q04 bounds the 0.5.0 asset graph to asset,
version, snapshot, variable, dictionary version — "variable" has no
separate typed ID here; it is already uniquely keyed by
``(item_id/asset_id, table_name, column_name)``):

    asset_dataset, asset_database, snapshot, version, dictionary_version

Both factory-reset grades delete every ``id_sequences`` row (D-29/P-10), so
issuance restarts at 1 after either reset — see ``system_db.reset_demo`` /
``system_db.wipe_all_items`` and ``routers/admin.py``'s ``id_epoch`` audit
payload, which calls ``epoch_snapshot()`` below BEFORE the delete runs.
"""
from __future__ import annotations

import re
import sqlite3

import system_db as s

# AST-02: letters, digits, dash, underscore only. Length-bounded per task 3.5.
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ALIAS_MIN_LEN = 1
_ALIAS_MAX_LEN = 60

# scope -> (prefix, zero-pad width). Width auto-grows past the pad — a
# %0{width}d format never truncates a wider number, so DS10000 is fine
# (P-02's regex is deliberately {4,} for exactly this reason).
_FORMATS: dict[str, tuple[str, int]] = {
    "asset_dataset": ("DS", 4),
    "asset_database": ("DB", 4),
    "snapshot": ("SN", 6),
    "version": ("VR", 5),
    "dictionary_version": ("DV", 5),
}

# Exactly five (A-Q04) — exported so a test or a future caller can iterate
# them without hand-copying the format table.
SCOPES: tuple[str, ...] = tuple(_FORMATS)


def scope_for_kind(kind: str) -> str:
    """AST-01/P-02 — which ``id_sequences`` scope an asset's ``system_id``
    draws from, keyed by the asset's ``kind`` ('database' | 'dataset')."""
    if kind == "database":
        return "asset_database"
    if kind == "dataset":
        return "asset_dataset"
    raise ValueError(f"kind must be 'database' or 'dataset', got {kind!r}")


def _format_id(scope: str, value: int) -> str:
    prefix, width = _FORMATS[scope]
    return f"{prefix}{value:0{width}d}"


def _allocate_on(conn: sqlite3.Connection, scope: str) -> str:
    if scope not in _FORMATS:
        raise ValueError(f"Unknown id_sequences scope: {scope!r}. Must be one of {SCOPES}.")
    conn.execute(
        "INSERT OR IGNORE INTO id_sequences (scope, next_value, epoch_started_at) "
        "VALUES (?, 1, ?)",
        (scope, s.now_ist()),
    )
    row = conn.execute(
        "SELECT next_value FROM id_sequences WHERE scope=?", (scope,)
    ).fetchone()
    next_value = row[0]
    conn.execute(
        "UPDATE id_sequences SET next_value=? WHERE scope=?",
        (next_value + 1, scope),
    )
    return _format_id(scope, next_value)


def allocate(scope: str, conn: sqlite3.Connection | None = None) -> str:
    """Allocate the next human-quotable ID for ``scope``, atomically.

    ``INSERT OR IGNORE`` the counter row, ``SELECT`` its current value,
    ``UPDATE`` it to the next value — all inside ONE transaction, so two
    concurrent callers never observe (and therefore never format) the same
    ``next_value``: every connection from ``system_db.get_conn()`` sets
    ``PRAGMA busy_timeout=30000``, and SQLite serialises writers against a
    single database file — the second caller's ``UPDATE``/commit simply
    waits for the first transaction to finish and then reads the value IT
    already advanced past. The ``ux_dq_assets_system_id`` unique index is
    the belt to this transaction's braces (AST-01): even if two processes
    somehow raced past this lock (a different SQLite build, a network
    filesystem edge case), a duplicate ``system_id`` insert into
    ``dq_assets`` would fail loudly at that index rather than silently
    succeeding twice.

    Pass ``conn`` to run inside a caller-managed transaction — e.g.
    ``system_db._backfill_asset_model``, which is already inside
    ``init_schema()``'s open connection and must never open a second one
    against the same file mid-transaction (that would deadlock against its
    own uncommitted writes). With no ``conn``, this function opens and
    commits its own, exactly like every other one-shot write in this
    codebase (see ``system_db.insert``/``update``).
    """
    if conn is not None:
        return _allocate_on(conn, scope)
    with s.get_conn() as c:
        result = _allocate_on(c, scope)
        c.commit()
        return result


def preview_next_id(scope: str) -> str:
    """Peek at the next formatted ID without creating or changing a counter row."""
    if scope not in _FORMATS:
        raise ValueError(f"Unknown id_sequences scope: {scope!r}. Must be one of {SCOPES}.")
    row = s.query_one("id_sequences", scope=scope)
    next_value = int(row["next_value"]) if row else 1
    return _format_id(scope, next_value)


def validate_alias(alias: str) -> str:
    """AST-02 — letters, digits, dashes and underscores only; 1-60 chars.

    Raises ``ValueError`` with a message the UI can show verbatim (the
    server-side re-validation behind AST-02's "as typed" client-side rule —
    never trust the client, the same discipline as the factory reset's
    server-side confirm-phrase check). Returns the alias unchanged on
    success so a caller can write ``alias = validate_alias(raw)``.
    """
    if alias is None or not alias.strip():
        raise ValueError("Alias cannot be empty.")
    if len(alias) < _ALIAS_MIN_LEN or len(alias) > _ALIAS_MAX_LEN:
        raise ValueError(f"Alias must be between {_ALIAS_MIN_LEN} and {_ALIAS_MAX_LEN} characters.")
    if not _ALIAS_RE.match(alias):
        raise ValueError(
            "Alias can only contain letters, digits, dashes and underscores (no spaces or "
            "other punctuation)."
        )
    return alias


def sanitize_alias_for_migration(raw: str | None) -> str:
    """AST-10 backfill only (system_db._backfill_asset_model, task 3.4) —
    turn an arbitrary pre-0.5.0 item name into a valid alias: spaces become
    dashes, everything else outside ``[A-Za-z0-9_-]`` is dropped, and an
    empty result falls back to ``'asset'``.

    Never raises — migrating historical data must always produce SOME valid
    alias, never block on one that doesn't fit AST-02's rule (a
    whitespace-only or symbol-only legacy name is the case this exists for).
    """
    text = (raw or "").strip().replace(" ", "-")
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", text)
    return cleaned or "asset"


def compose_display_name(system_id: str, alias: str) -> str:
    """P-03 — the ONLY writer of the ``'<system_id>-<alias>'`` shape.

    There is deliberately no ``parse_display_name``: ``system_id`` and
    ``alias`` are always stored as separate columns
    (``dq_assets.system_id`` / ``.alias``) and never reconstructed by
    splitting this string apart — an alias may itself contain a dash
    (AST-02 permits it), which would make splitting ambiguous. If you find
    yourself wanting to parse this string, that is a design error (P-03).
    """
    return f"{system_id}-{alias}"


def epoch_snapshot() -> dict[str, int]:
    """D-29/P-10/R-08 — the ``next_value`` each scope would issue right now,
    for every scope that has ever been allocated.

    Called by ``routers/admin.py``'s ``factory_reset`` BEFORE either reset
    grade runs, so the post-delete ``transaction_log`` audit row can carry
    the pre-reset counters as ``id_epoch`` — the human-legible boundary
    between one ID generation and the next (D-29, ADM-07). A scope that was
    never allocated has no row and is simply absent here — there is nothing
    honest to report for a counter nobody has touched yet.
    """
    return {row["scope"]: row["next_value"] for row in s.query("id_sequences")}
