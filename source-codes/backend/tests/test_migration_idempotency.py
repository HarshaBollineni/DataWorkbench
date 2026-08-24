"""RCA Stage 7 — migration idempotency gate (MP §9): 'migration run
twice yields identical schema/row counts.' Exercises the real boot-time
migration path (init_schema() + the additive-column _MIGRATIONS pass +
seed_platform_and_taxonomy()) against a throwaway DB, run twice, comparing
the full table/row-count inventory each time.

Standalone-runnable (``python -m unittest tests.test_migration_idempotency``);
same SYSTEM_DB_PATH-at-import-time sandboxing convention as the other
backend test files.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-migration-idempotency.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from seeds import seed_platform_and_taxonomy  # noqa: E402


def _table_inventory() -> dict:
    """{table_name: row_count} for every user table in the live schema."""
    conn = s.get_conn()
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


class MigrationIdempotencyTests(unittest.TestCase):
    def test_schema_and_seed_are_stable_across_a_second_run(self):
        s.init_schema()
        seed_platform_and_taxonomy()
        first = _table_inventory()
        self.assertGreater(len(first), 0)
        self.assertGreater(first.get("tenants", 0), 0)  # bootstrap tenant really seeded

        # Re-run the exact boot-time migration path a second time, as if the
        # process restarted against the same on-disk DB.
        s.init_schema()
        seed_platform_and_taxonomy()
        second = _table_inventory()

        self.assertEqual(set(first.keys()), set(second.keys()), "table set changed on a second migration run")
        diffs = {t: (first[t], second[t]) for t in first if first[t] != second[t]}
        self.assertEqual(diffs, {}, f"row counts changed on a second migration run: {diffs}")

    def test_third_run_is_still_stable(self):
        """Not just twice — the migration must be a true fixed point, not
        something that happens to stabilize after exactly one extra run."""
        s.init_schema()
        seed_platform_and_taxonomy()
        after_two = _table_inventory()
        s.init_schema()
        seed_platform_and_taxonomy()
        after_three = _table_inventory()
        self.assertEqual(after_two, after_three)


class LegacyRcaTableUpgradeTests(unittest.TestCase):
    """RCA Stage 3 reused the table names `rca_cases` / `rca_hypotheses` for an
    incompatible new shape. `CREATE TABLE IF NOT EXISTS` is a silent no-op
    against a pre-existing table, so a database that already ran the retired
    legacy RCA schema (pre-Stage-3) kept the old columns — and every new-only
    column reference (starting with the `rca_cases(issue_row_id)` index
    created at boot) crashed init_schema() outright. This reproduces that
    exact on-disk shape and asserts the boot path upgrades it in place instead
    of crashing, preserving the legacy rows under a `_legacy_v1` name."""

    def setUp(self):
        conn = s.get_conn()
        try:
            conn.execute("DROP TABLE IF EXISTS rca_cases")
            conn.execute("DROP TABLE IF EXISTS rca_hypotheses")
            conn.execute("""CREATE TABLE rca_cases (
                case_id TEXT PRIMARY KEY, title TEXT, status TEXT, severity TEXT,
                logical_dbs TEXT, tables TEXT, columns TEXT, failure_ids TEXT,
                primary_failure_id INTEGER, suspected_cause_family TEXT,
                accepted_hypothesis_id TEXT, remediation_plan_id TEXT, ticket_ids TEXT,
                owner TEXT, dossier TEXT, created_at TEXT, updated_at TEXT, closed_at TEXT
            )""")
            conn.execute(
                "INSERT INTO rca_cases (case_id, title, status) VALUES ('legacy-case-1','old title','open')"
            )
            conn.execute("""CREATE TABLE rca_hypotheses (
                hypothesis_id TEXT PRIMARY KEY, case_id TEXT, cause_family TEXT,
                statement TEXT, confidence REAL, status TEXT, supporting_evidence TEXT,
                contradicting_evidence TEXT, next_probes TEXT, impact TEXT,
                remediation_fit TEXT, proposed_by TEXT, created_at TEXT, updated_at TEXT
            )""")
            conn.execute(
                "INSERT INTO rca_hypotheses (hypothesis_id, case_id, statement) "
                "VALUES ('legacy-hyp-1','legacy-case-1','old hypothesis')"
            )
            conn.commit()
        finally:
            conn.close()

    def test_boot_upgrades_legacy_shape_without_crashing(self):
        s.init_schema()  # must not raise sqlite3.OperationalError

        conn = s.get_conn()
        try:
            new_cols = {r[1] for r in conn.execute('PRAGMA table_info("rca_cases")')}
            self.assertIn("issue_row_id", new_cols)

            legacy_row = conn.execute(
                "SELECT case_id, title FROM rca_cases_legacy_v1"
            ).fetchone()
            self.assertEqual(tuple(legacy_row), ("legacy-case-1", "old title"))

            conn.execute(
                "INSERT INTO rca_cases (case_id, tenant_id, issue_row_id, state) "
                "VALUES ('c1','bootstrap','i1','open')"
            )
            conn.commit()
        finally:
            conn.close()

        # Re-running on the now-upgraded DB must be a no-op, not a second rename.
        s.init_schema()
        conn = s.get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='rca_cases_legacy_v1'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
