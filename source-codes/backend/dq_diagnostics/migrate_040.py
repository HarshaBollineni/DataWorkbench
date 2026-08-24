"""0.4.0 Phase-3 retirement migration — drop the old framework's records.

FWK-13 / C-18 (accepted per instruction): the 14-test Galileo framework is
replaced by the 9-diagnostic register. Everything keyed to the retired tests
is dropped — the old registry content (``test_library``, ``fw_areas``,
``fw_tests``, ``fw_family_weights``) and every work-product record produced
by it (``plan_v2``, ``results_v2``, ``scores_v2``, ``issues_v2``,
``tracked_issues_v2``, and the ``rca_*`` cases those issues opened). The new
framework never writes these record classes (diagnostic results land in the
Phase-6 ``diag_*`` records), so every existing row is by construction an
old-framework artifact.

**Preserved untouched** (§1.3): datasets (``dq_items``/``dq_item_files``/
``dq_item_tables``), ``variable_inventory``, users/roles/sessions, taxonomy
dimensions/values/aliases (item-level ``tag_assignments`` survive; plan/
result/issue assignments are dropped with their objects), knowledge
documents/versions/rules.

One-time: completion is recorded in ``transaction_log``. Later boots must not
re-evaluate current ``issues_v2``/RCA rows as legacy work and delete them.
Per-table counts are recorded in an append-only audit event (PLT-05) whenever
anything was actually dropped.
"""
from __future__ import annotations

import os
from pathlib import Path

import system_db

# Work-product record classes produced only by the retired framework.
_RETIRED_WORK_TABLES = [
    "plan_v2", "results_v2", "scores_v2", "issues_v2", "tracked_issues_v2",
]
# The retired registry/content tables (galileo_seed.py content, FWK-13).
_RETIRED_CONTENT_TABLES = [
    "test_library", "fw_areas", "fw_tests", "fw_family_weights",
]
# tag_assignments object types that pointed at dropped work-product rows.
_RETIRED_TAG_OBJECT_TYPES = ["plan_v2", "results_v2", "issues_v2"]
_DROP_EVENT = "framework_retirement_drop"
_COMPLETE_EVENT = "framework_retirement_complete"


def retirement_snapshot_path() -> Path:
    """Return the recovery point used immediately before this migration drops data.

    Prefer the configured persistent-volume location when one is available.
    Local development still gets a sibling SQLite snapshot, and deployments
    may override either choice with ``SYSTEM_DB_RETIREMENT_SNAPSHOT_PATH``.
    """
    configured = os.environ.get("SYSTEM_DB_RETIREMENT_SNAPSHOT_PATH")
    if configured:
        return Path(configured)
    base = system_db.SYS_DB_BACKUP_PATH or system_db.SYS_DB_PATH
    return base.with_name(f"{base.stem}.pre-retirement-040{base.suffix}")


def _legacy_row_counts(conn) -> dict[str, int]:
    """Return the destructive targets and their current row counts."""
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in _RETIRED_CONTENT_TABLES + _RETIRED_WORK_TABLES
    }
    rca_tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rca_%'")]
    for table in rca_tables:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    ph = ",".join("?" for _ in _RETIRED_TAG_OBJECT_TYPES)
    counts["tag_assignments"] = conn.execute(
        f"SELECT COUNT(*) FROM tag_assignments WHERE object_type IN ({ph})",
        _RETIRED_TAG_OBJECT_TYPES).fetchone()[0]
    return counts


def retire_old_framework() -> dict[str, int]:
    """Drop every retired record class, after a mandatory recovery snapshot."""
    counts: dict[str, int] = {}
    with system_db.get_conn() as conn:
        # Older installations only have the drop audit; it is also a valid
        # completion marker because it was written after the deleting
        # transaction committed. Without this guard, every newly-created
        # current issue was mistaken for legacy work at the next restart.
        completed = conn.execute(
            "SELECT 1 FROM transaction_log WHERE event IN (?, ?) LIMIT 1",
            [_DROP_EVENT, _COMPLETE_EVENT],
        ).fetchone()
        if completed:
            return {table: 0 for table in _RETIRED_CONTENT_TABLES + _RETIRED_WORK_TABLES}

    # This is deliberately outside the deleting transaction: SQLite's online
    # backup API captures a complete, committed pre-retirement database.  A
    # snapshot error aborts before the first DELETE is issued.
    with system_db.get_conn() as conn:
        legacy_counts = _legacy_row_counts(conn)
    if any(legacy_counts.values()):
        system_db.backup_to_path(retirement_snapshot_path())

    with system_db.get_conn() as conn:
        for table in _RETIRED_CONTENT_TABLES + _RETIRED_WORK_TABLES:
            counts[table] = conn.execute(f"DELETE FROM {table}").rowcount
        # RCA cases were opened from issues_v2 rows — keyed to old tests (C-18).
        rca_tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rca_%'")]
        for table in rca_tables:
            counts[table] = conn.execute(f"DELETE FROM {table}").rowcount
        ph = ",".join("?" for _ in _RETIRED_TAG_OBJECT_TYPES)
        counts["tag_assignments"] = conn.execute(
            f"DELETE FROM tag_assignments WHERE object_type IN ({ph})",
            _RETIRED_TAG_OBJECT_TYPES).rowcount
        conn.commit()
    dropped = {k: v for k, v in counts.items() if v}
    if dropped:
        system_db.insert("transaction_log", {
            "ts": system_db.now_ist(), "actor": "system:migration-0.4.0",
            "event": _DROP_EVENT, "payload": {"dropped": dropped},
        })
    else:
        # A clean install has nothing to drop, but still needs a permanent
        # marker so current issue records created later remain untouched.
        system_db.insert("transaction_log", {
            "ts": system_db.now_ist(), "actor": "system:migration-0.4.0",
            "event": _COMPLETE_EVENT, "payload": {"dropped": {}},
        })
    return counts
