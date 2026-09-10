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
import uuid
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


class DiagnosticDecisionKindMigrationTests(unittest.TestCase):
    """D06's explicit DSC acknowledgement must survive the legacy CHECK."""

    def setUp(self):
        self._prior_path = s.SYS_DB_PATH
        s.SYS_DB_PATH = Path(tempfile.gettempdir()) / f"archimedes-diag-decisions-{uuid.uuid4().hex}.db"

    def tearDown(self):
        s.SYS_DB_PATH = self._prior_path

    def test_boot_upgrades_legacy_decision_check_and_preserves_audit_rows(self):
        conn = s.get_conn()
        try:
            conn.execute("""CREATE TABLE diag_run_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
                kind TEXT CHECK(kind IN ('role_override','threshold_tune','scope_exclusion',
                                         'default_applied','role_verification_change')),
                payload_json TEXT, actor TEXT, ts TEXT
            )""")
            conn.execute("""INSERT INTO diag_run_decisions
                            (run_id,kind,payload_json,actor,ts)
                            VALUES ('legacy-run','default_applied','{}','tester','then')""")
            conn.commit()
        finally:
            conn.close()

        s.init_schema()
        conn = s.get_conn()
        try:
            row = conn.execute("SELECT run_id,kind,payload_json FROM diag_run_decisions").fetchone()
            self.assertEqual(tuple(row), ("legacy-run", "default_applied", "{}"))
            conn.execute("""INSERT INTO diag_run_decisions
                            (run_id,kind,payload_json,actor,ts)
                            VALUES ('d06-run','dsc_assist_confirmation','{}','tester','now')""")
            conn.commit()
        finally:
            conn.close()

        s.init_schema()
        self.assertEqual(
            s.query("diag_run_decisions", run_id="d06-run")[0]["kind"],
            "dsc_assist_confirmation",
        )


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


class D06ShadowAuditMigrationTests(unittest.TestCase):
    """Boot repair for every D06 shadow-audit shape shipped before C2.

    The index at the end of ``init_schema`` must never be the first code that
    notices a partial table: the upgrade itself owns schema repair and must be
    safe to repeat against the same on-disk database.
    """

    def setUp(self):
        self._prior_path = s.SYS_DB_PATH
        s.SYS_DB_PATH = Path(tempfile.gettempdir()) / f"archimedes-d06-shadow-{uuid.uuid4().hex}.db"

    def tearDown(self):
        s.SYS_DB_PATH = self._prior_path

    @staticmethod
    def _columns(conn):
        return [row[1] for row in conn.execute("PRAGMA table_info(d06_dsc_cadence_shadow_audit)")]

    @staticmethod
    def _has_run_version_key(conn):
        for index in conn.execute("PRAGMA index_list(d06_dsc_cadence_shadow_audit)"):
            if index[2]:
                names = tuple(row[2] for row in conn.execute(f'PRAGMA index_info("{index[1]}")'))
                if names == ("run_id", "adapter_version"):
                    return True
        return False

    def test_fresh_database_creates_the_canonical_shadow_audit_schema(self):
        s.init_schema()
        conn = s.get_conn()
        try:
            self.assertEqual(self._columns(conn), list(s._D06_SHADOW_AUDIT_COLUMNS))
            self.assertTrue(self._has_run_version_key(conn))
        finally:
            conn.close()

    def test_legacy_unique_run_id_rows_receive_safe_defaults(self):
        conn = s.get_conn()
        try:
            conn.execute("""CREATE TABLE d06_dsc_cadence_shadow_audit (
                event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            conn.execute("INSERT INTO d06_dsc_cadence_shadow_audit VALUES (?,?,?,?,?)", (
                "legacy-event", "legacy-run", "legacy-type", '{"run_id":"legacy-run"}', "2026-01-01T00:00:00+00:00",
            ))
            conn.commit()
        finally:
            conn.close()

        s.init_schema()
        conn = s.get_conn()
        try:
            row = conn.execute("SELECT tenant_id, run_id, adapter_version, event_type FROM d06_dsc_cadence_shadow_audit").fetchone()
            self.assertEqual(tuple(row), ("bootstrap", "legacy-run", "v1", "legacy-type"))
            self.assertTrue(self._has_run_version_key(conn))
        finally:
            conn.close()

    def test_partial_table_missing_run_id_recovers_it_from_the_payload(self):
        conn = s.get_conn()
        try:
            # This is the exact interrupted additive shape that caused startup
            # to fail: tenant/version were added but run_id never existed.
            conn.execute("""CREATE TABLE d06_dsc_cadence_shadow_audit (
                event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL, tenant_id TEXT NOT NULL DEFAULT 'bootstrap',
                adapter_version TEXT NOT NULL DEFAULT 'v1'
            )""")
            conn.execute("INSERT INTO d06_dsc_cadence_shadow_audit (event_id,event_type,payload_json,created_at) VALUES (?,?,?,?)", (
                "partial-event", "legacy-type", '{"run_id":"payload-run"}', "2026-01-01T00:00:00+00:00",
            ))
            conn.commit()
        finally:
            conn.close()

        s.init_schema()
        conn = s.get_conn()
        try:
            row = conn.execute("SELECT event_id, run_id FROM d06_dsc_cadence_shadow_audit").fetchone()
            self.assertEqual(tuple(row), ("partial-event", "payload-run"))
            self.assertEqual(self._columns(conn), list(s._D06_SHADOW_AUDIT_COLUMNS))
        finally:
            conn.close()

    def test_unrecoverable_partial_rows_are_quarantined_not_silently_dropped(self):
        conn = s.get_conn()
        try:
            conn.execute("""CREATE TABLE d06_dsc_cadence_shadow_audit (
                event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            conn.execute("INSERT INTO d06_dsc_cadence_shadow_audit VALUES (?,?,?,?)", (
                "unrecoverable-event", "legacy-type", '{}', "2026-01-01T00:00:00+00:00",
            ))
            conn.commit()
        finally:
            conn.close()

        s.init_schema()
        conn = s.get_conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM d06_dsc_cadence_shadow_audit").fetchone()[0], 0)
            legacy = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'd06_dsc_cadence_shadow_audit_legacy_v2%'"
            ).fetchone()[0]
            self.assertEqual(conn.execute(f'SELECT event_id FROM "{legacy}"').fetchone()[0], "unrecoverable-event")
        finally:
            conn.close()

    def test_current_schema_is_a_fixed_point_across_repeat_initialization(self):
        s.init_schema()
        conn = s.get_conn()
        try:
            conn.execute("""INSERT INTO d06_dsc_cadence_shadow_audit
                (event_id,tenant_id,run_id,adapter_version,event_type,payload_json,created_at)
                VALUES (?,?,?,?,?,?,?)""", (
                "current-event", "tenant-a", "current-run", "v1", "current-type",
                '{"run_id":"current-run"}', "2026-01-01T00:00:00+00:00",
            ))
            conn.commit()
        finally:
            conn.close()

        s.init_schema()
        s.init_schema()
        conn = s.get_conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM d06_dsc_cadence_shadow_audit").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT tenant_id FROM d06_dsc_cadence_shadow_audit").fetchone()[0], "tenant-a")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'd06_dsc_cadence_shadow_audit_legacy_v2%'"
            ).fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
