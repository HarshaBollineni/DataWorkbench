"""WSP-08 / D-19 (plan Phase 2.9) — admin factory reset behavioral tests.

Standalone-runnable (``python -m unittest tests.test_admin_reset``); follows
the same SYSTEM_DB_PATH / KB_STORAGE_DIR / UPLOAD_DIR-at-import-time
sandboxing convention as test_rca.py and test_kb.py. Route functions are
called directly (the repo's established pattern — see test_auth_security.py,
test_kb.py — no TestClient anywhere in this suite); FastAPI's route
decorators return the undecorated callable, so ``admin.factory_reset(...)``
is a plain Python call that still exercises the real auth gate, confirm
check, and PLT-04 sanitizer.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-admin-reset.db"
_TMP_KB_STORAGE = Path(tempfile.gettempdir()) / "archimedes-test-admin-reset-kb-storage"
_TMP_UPLOAD_DIR = Path(tempfile.gettempdir()) / "archimedes-test-admin-reset-uploads"
_TMP_ARTIFACT_DIR = Path(tempfile.gettempdir()) / "archimedes-test-admin-reset-artifacts"
if _TMP_DB.exists():
    _TMP_DB.unlink()
if _TMP_KB_STORAGE.exists():
    shutil.rmtree(_TMP_KB_STORAGE)
if _TMP_UPLOAD_DIR.exists():
    shutil.rmtree(_TMP_UPLOAD_DIR)
if _TMP_ARTIFACT_DIR.exists():
    shutil.rmtree(_TMP_ARTIFACT_DIR)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["KB_STORAGE_DIR"] = str(_TMP_KB_STORAGE)
os.environ["UPLOAD_DIR"] = str(_TMP_UPLOAD_DIR)
os.environ["ANALYSIS_ARTIFACT_DIR"] = str(_TMP_ARTIFACT_DIR)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

from fastapi import HTTPException  # noqa: E402

import kb  # noqa: E402
import system_db as s  # noqa: E402
import taxonomy  # noqa: E402
from routers import admin, auth  # noqa: E402
from seeds.taxonomy_seed import BOOTSTRAP_TENANT, seed_platform, seed_taxonomy  # noqa: E402

TENANT = BOOTSTRAP_TENANT

# Every table an item pipeline can touch — used to take precise before/after
# deltas so assertions never depend on test execution order or on what a
# sibling TestCase in this same module may have left behind in the shared
# sandboxed DB.
_ITEM_TABLES = (
    "dq_items", "dq_item_files", "dq_item_tables", "variable_inventory",
    "plan_v2", "results_v2", "scores_v2", "issues_v2", "tracked_issues_v2",
    "rca_cases", "tag_assignments",
)


def _make_user(username: str, roles: list[str]) -> None:
    s.upsert("users", {
        "username": username, "password": "pw", "name": username,
        "email": f"{username}@example.com", "function": "", "role": "",
        "salutation": "", "call_name": username, "ai_personality": "professional",
        "theme": "minimalist", "authz_roles": roles,
    })


def _login(username: str) -> str:
    result = auth.login(auth.LoginRequest(username=username, password="pw"))
    return result["token"]


def _build_item_fixture(item_id: str, table_name: str = "t1") -> dict:
    """A full item-lifecycle fixture, built with direct system_db inserts
    (mirrors test_rca.py's _build_fixture_item, which inserts plan_v2 rows
    directly rather than driving ai/v2's service layer) so this file has no
    dependency on ai/* internals: item + files/tables/inventory rows, a
    plan/result/score/issue row, a tracked issue, an RCA case, an item-keyed
    tag assignment, and a real file on disk under the sandboxed uploads dir.
    """
    now = s.now_ist()
    s.insert("dq_items", {
        "item_id": item_id, "kind": "database", "name": item_id, "status": "finalized",
        "module_tag": "", "target_variable": "", "use_case": "",
        "created_at": now, "updated_at": now,
    })
    s.insert("dq_item_files", {
        "file_id": f"file_{item_id}", "item_id": item_id, "role": "assessment",
        "filename": "data.csv", "path": f"{item_id}/data.csv", "completed_at": now,
    })
    s.insert("dq_item_tables", {
        "item_id": item_id, "table_name": table_name, "row_count": 10, "col_count": 2,
        "columns": ["a", "b"],
    })
    s.insert("variable_inventory", {
        "item_id": item_id, "table_name": table_name, "column_name": "a",
        "classification": "numeric", "data_type": "int", "description": "",
        "discrepancies": [], "notes": "", "role": "feature", "profile_json": {},
        "updated_at": now,
    })
    row_id = f"row_{item_id}"
    s.insert("plan_v2", {
        "row_id": row_id, "item_id": item_id, "table_name": table_name, "scope": "framework",
        "test_name": "Completeness check", "area_id": None, "origin": "framework",
        "status": "finalized", "columns_json": ["a"], "params_json": {}, "reason": "",
        "snippet_code": "", "approved": 1, "trigger": None, "crossval_json": None,
        "created_at": now, "updated_at": now,
    })
    result_id = f"res_{item_id}"
    s.insert("results_v2", {
        "result_id": result_id, "row_id": row_id, "item_id": item_id, "table_name": table_name,
        "scope": "framework", "test_name": "Completeness check", "status": "fail",
        "metric": 0.9, "threshold_json": {}, "violation_count": 3, "evidence_json": {},
        "columns_json": ["a"], "not_runnable_reason": None, "watch_note": 0,
        "origin": "framework", "area_id": None, "run_at": now,
    })
    s.insert("scores_v2", {
        "item_id": item_id, "scope": "framework", "provisional": 0.5, "final": 0.5,
        "breakdown_json": {}, "stage": "final", "computed_at": now,
    })
    issue_row_id = f"iss_{item_id}"
    s.insert("issues_v2", {
        "issue_row_id": issue_row_id, "item_id": item_id, "table_name": table_name,
        "test_name": "Completeness check", "area_id": None, "criticality": "high",
        "columns_json": ["a"], "violation_count": 3, "threshold_json": {},
        "column_details_json": {}, "metric": 0.9, "status": "Open",
        "resolution_rationale": None, "tracked_issue_id": None,
        "created_at": now, "updated_at": now,
    })
    s.insert("tracked_issues_v2", {
        "issue_id": f"tracked_{item_id}", "issue_row_id": issue_row_id, "title": "t",
        "description": "d", "owner": "tester", "priority": "high", "target_date": None,
        "status": "Open", "created_at": now,
    })
    case_id = f"case_{item_id}"
    s.insert("rca_cases", {
        "case_id": case_id, "tenant_id": TENANT, "issue_row_id": issue_row_id,
        "item_id": item_id, "table_name": table_name, "state": "opening_looks",
        "part": None, "tag_snapshot_json": {}, "complaint_text": "",
        "created_by": "tester", "created_at": now, "updated_at": now,
        "closed_at": None, "contract_version": "1",
    })
    taxonomy.assign_tags(TENANT, "dq_item", item_id, ["risk_type:credit"], "tester")
    upload_dir = s._upload_root() / item_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return {"item_id": item_id, "row_id": row_id, "result_id": result_id,
            "issue_row_id": issue_row_id, "case_id": case_id, "upload_dir": upload_dir}


class SurgicalResetTests(unittest.TestCase):
    """2-T5 — surgical reset clears the item + every derived artefact
    (plan/result/score/issue/RCA case/item-keyed tag/uploaded file);
    preserves users, taxonomy, and the knowledge base; the response's
    per-table counts are exact; an audit row lands; a second run is a
    true no-op."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        _make_user("surgical_admin", ["user", "admin"])
        cls.admin_token = _login("surgical_admin")

    def test_surgical_reset_clears_pipeline_preserves_platform_data(self):
        item_id = f"itm_surg_{uuid.uuid4().hex[:8]}"
        fixture = _build_item_fixture(item_id)
        kb_upload = kb.upload_document(TENANT, f"{item_id}.md", "text/markdown",
                                       b"## Rule\nBody.", "domain_fact", "tester")

        before = {t: len(s.query(t)) for t in _ITEM_TABLES}
        before_item_tags = [a for a in s.query("tag_assignments")
                            if a["object_type"] == "dq_item"]
        before_kb = len(s.query("kb_documents"))
        before_dims = len(s.query("tag_dimensions"))
        before_users = len(s.query("users"))

        res = admin.factory_reset(
            admin.FactoryResetRequest(grade="surgical", confirm="RESET"),
            f"Bearer {self.admin_token}")

        self.assertTrue(res["ok"])
        self.assertEqual(res["grade"], "surgical")

        after = {t: len(s.query(t)) for t in _ITEM_TABLES}
        for t in _ITEM_TABLES:
            if t == "tag_assignments":
                continue
            self.assertEqual(after[t], 0, f"{t} should be empty after a surgical reset")
            self.assertEqual(res["deleted"][t], before[t] - after[t],
                             f"reported delete count for {t} must match the actual delta")

        # Only item-lifecycle tags are work product. KB-document tags are
        # governed knowledge metadata and must survive a surgical reset.
        after_item_tags = [a for a in s.query("tag_assignments")
                           if a["object_type"] == "dq_item"]
        self.assertEqual(after_item_tags, [])
        self.assertEqual(res["deleted"]["tag_assignments"], len(before_item_tags))

        self.assertIsNone(s.query_one("dq_items", item_id=item_id))
        self.assertIsNone(s.query_one("plan_v2", row_id=fixture["row_id"]))
        self.assertIsNone(s.query_one("results_v2", result_id=fixture["result_id"]))
        self.assertIsNone(s.query_one("issues_v2", issue_row_id=fixture["issue_row_id"]))
        self.assertIsNone(s.query_one("rca_cases", case_id=fixture["case_id"]))
        self.assertFalse(fixture["upload_dir"].exists(), "uploaded files must be wiped on disk")

        # Preserved: users, taxonomy dimensions, knowledge base.
        self.assertEqual(len(s.query("users")), before_users)
        self.assertEqual(len(s.query("tag_dimensions")), before_dims)
        self.assertEqual(len(s.query("kb_documents")), before_kb)
        self.assertIsNotNone(s.query_one("kb_documents", document_id=kb_upload["document_id"]))

        # PLT-05 audit trail: actor + grade + counts.
        audit_rows = [r for r in s.query("transaction_log", order_by="id")
                     if r["event"] == "factory_reset" and r["payload"].get("grade") == "surgical"]
        self.assertTrue(audit_rows, "a factory_reset audit row must be written")
        last = audit_rows[-1]
        self.assertEqual(last["actor"], "surgical_admin")
        self.assertEqual(last["payload"]["deleted"]["dq_items"], before["dq_items"])

        # Idempotent: nothing new was added, so a second run deletes nothing.
        res2 = admin.factory_reset(
            admin.FactoryResetRequest(grade="surgical", confirm="RESET"),
            f"Bearer {self.admin_token}")
        self.assertTrue(res2["ok"])
        for t in _ITEM_TABLES:
            self.assertEqual(res2["deleted"][t], 0, f"second surgical reset must not delete any {t} rows")


class WipeResetTests(unittest.TestCase):
    """2-T6 — full wipe blanks every product/reference table except
    users+sessions/transaction_log/feature_flags/schema, then restores
    platform seed data (diagnostic_register/agent_skills non-empty —
    "re-boot equivalence"); the audit row is written after the wipe so it
    survives; a second run ends in the same stable state."""

    @classmethod
    def setUpClass(cls):
        # Pytest imports every test module before executing this class. Other
        # artifact tests configure the same process-wide environment variable
        # during collection, so bind the already-imported storage module to
        # this suite's sandbox explicitly instead of depending on import order.
        s.ANALYSIS_ARTIFACT_ROOT = _TMP_ARTIFACT_DIR
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        _make_user("wipe_admin", ["user", "admin"])
        cls.admin_token = _login("wipe_admin")

    def test_wipe_blanks_platform_reseeds_and_preserves_users_and_audit(self):
        item_id = f"itm_wipe_{uuid.uuid4().hex[:8]}"
        _build_item_fixture(item_id)
        kb.upload_document(TENANT, f"{item_id}.md", "text/markdown",
                           b"## Rule\nBody.", "domain_fact", "tester")
        s.insert("ingested_databases", {
            "logical_db": f"db_{item_id}", "display_name": "x", "description": "",
            "access_role": "", "dictionary": {}, "ai_summary": "", "metadata": {},
            "relations": {}, "fetched_at": s.now_ist(), "record_count": 0,
            "created_at": s.now_ist(),
        })
        _TMP_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        (_TMP_ARTIFACT_DIR / "wipe-proof.json").write_text("{}", encoding="utf-8")
        s.insert("analysis_artifacts", {
            "artifact_id": f"art_{item_id}", "artifact_type": "test", "asset_id": "asset_test",
            "snapshot_id": item_id, "comparison_snapshot_id": None, "population_fingerprint": "p",
            "target_fingerprint": None, "feature": None, "methodology_fingerprint": "m",
            "scope": "universal", "workflow_id": None, "owner_id": None,
            "payload_path": "wipe-proof.json", "payload_hash": "0", "status": "active",
            "source_artifact_ids_json": [], "source_artifacts_json": [], "identity_fingerprint": None,
            "identity_json": {}, "schema_version": 1, "summary_json": {},
            "summary_adapter_version": "1", "integrity_status": "unknown",
            "integrity_checked_at": None, "run_id": None, "created_by": "tester",
            "created_at": s.now_ist(), "superseded_at": None, "superseded_by_artifact_id": None,
        })
        s.insert("analysis_artifact_events", {
            "event_id": f"evt_{item_id}", "artifact_id": f"art_{item_id}",
            "event_type": "created", "actor": "tester", "detail_json": {}, "created_at": s.now_ist(),
        })

        before_users = len(s.query("users"))
        before_flags = {f["key"]: f["enabled"] for f in s.query("feature_flags")}

        res = admin.factory_reset(
            admin.FactoryResetRequest(grade="wipe", confirm="WIPE EVERYTHING"),
            f"Bearer {self.admin_token}")

        self.assertTrue(res["ok"])
        self.assertEqual(res["grade"], "wipe")

        blanked = _ITEM_TABLES + (
            "kb_documents", "kb_document_versions", "kb_sections", "kb_rules",
            "ingested_databases", "table_metadata", "object_contexts", "context_links",
            "analysis_artifacts", "analysis_artifact_events",
        )
        for t in blanked:
            self.assertEqual(len(s.query(t)), 0, f"{t} must be empty after a full wipe")
        self.assertEqual(list(_TMP_ARTIFACT_DIR.iterdir()), [])

        # Platform seeds restored (2-T6: "re-boot reseeds platform data").
        # Phase 3 (FWK-13): fw_areas/test_library retired with the old
        # framework; the reseeded platform framework is the 9-row register.
        self.assertEqual(len(s.query("diagnostic_register")), 9)
        self.assertEqual(len(s.query("framework_taxonomy")), 11)
        self.assertGreater(len(s.query("agent_skills")), 0)
        self.assertGreater(len(s.query("tag_dimensions")), 0)

        # Preserved: users, feature_flags (values untouched — no silent flag reset).
        self.assertEqual(len(s.query("users")), before_users)
        after_flags = {f["key"]: f["enabled"] for f in s.query("feature_flags")}
        self.assertEqual(after_flags, before_flags)

        # The admin's own session survives their own wipe (no self-lockout).
        still_logged_in = auth.current_user(f"Bearer {self.admin_token}")
        self.assertEqual(still_logged_in["username"], "wipe_admin")

        # Audit row written AFTER the wipe, into a table the wipe preserves.
        audit_rows = [r for r in s.query("transaction_log", order_by="id")
                     if r["event"] == "factory_reset" and r["payload"].get("grade") == "wipe"]
        self.assertTrue(audit_rows, "a factory_reset audit row must survive the wipe")
        self.assertEqual(audit_rows[-1]["actor"], "wipe_admin")

        # Idempotent: a second wipe succeeds and ends in the same stable state.
        res2 = admin.factory_reset(
            admin.FactoryResetRequest(grade="wipe", confirm="WIPE EVERYTHING"),
            f"Bearer {self.admin_token}")
        self.assertTrue(res2["ok"])
        for t in blanked:
            self.assertEqual(len(s.query(t)), 0)
        # Phase 3 (FWK-13): fw_areas/test_library retired with the old
        # framework; the reseeded platform framework is the 9-row register.
        self.assertEqual(len(s.query("diagnostic_register")), 9)
        self.assertEqual(len(s.query("framework_taxonomy")), 11)
        self.assertGreater(len(s.query("agent_skills")), 0)
        self.assertEqual(len(s.query("users")), before_users)


class DevelopmentCleanupTests(unittest.TestCase):
    """Development cleanup deletes only explicitly classified roots."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        _make_user("development_cleanup_admin", ["user", "admin"])
        cls.admin_token = _login("development_cleanup_admin")

    def test_development_cleanup_preserves_user_and_unclassified_items(self):
        suffix = uuid.uuid4().hex[:8]
        dev = _build_item_fixture(f"itm_dev_{suffix}")
        user = _build_item_fixture(f"itm_user_{suffix}")
        legacy = _build_item_fixture(f"itm_legacy_{suffix}")
        s.update("dq_items", {"item_id": dev["item_id"]},
                 {"artifact_origin": "development"})
        s.update("dq_items", {"item_id": user["item_id"]},
                 {"artifact_origin": "user"})
        s.update("dq_items", {"item_id": legacy["item_id"]},
                 {"artifact_origin": "unclassified"})

        result = admin.factory_reset(
            admin.FactoryResetRequest(
                grade="development", confirm="WIPE DEVELOPMENT"
            ),
            f"Bearer {self.admin_token}",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["grade"], "development")
        self.assertIsNone(s.query_one("dq_items", item_id=dev["item_id"]))
        self.assertIsNone(s.query_one("plan_v2", row_id=dev["row_id"]))
        self.assertFalse(dev["upload_dir"].exists())
        self.assertIsNotNone(s.query_one("dq_items", item_id=user["item_id"]))
        self.assertIsNotNone(s.query_one("plan_v2", row_id=user["row_id"]))
        self.assertTrue(user["upload_dir"].exists())
        self.assertIsNotNone(s.query_one("dq_items", item_id=legacy["item_id"]))
        self.assertTrue(legacy["upload_dir"].exists())

    def test_runtime_origin_defaults_user_and_honors_explicit_development(self):
        previous = os.environ.pop("ARCHIMEDES_ARTIFACT_ORIGIN", None)
        previous_pytest = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            self.assertEqual(s.current_artifact_origin(), "user")
            os.environ["ARCHIMEDES_ARTIFACT_ORIGIN"] = "development"
            self.assertEqual(s.current_artifact_origin(), "development")
        finally:
            os.environ.pop("ARCHIMEDES_ARTIFACT_ORIGIN", None)
            if previous is not None:
                os.environ["ARCHIMEDES_ARTIFACT_ORIGIN"] = previous
            if previous_pytest is not None:
                os.environ["PYTEST_CURRENT_TEST"] = previous_pytest

    def test_admin_can_review_and_select_one_legacy_asset_without_touching_another(self):
        suffix = uuid.uuid4().hex[:8]
        selected_id = f"asset_selected_{suffix}"
        protected_id = f"asset_protected_{suffix}"
        for asset_id, system_id, display_name in (
            (selected_id, f"DS9{suffix[:3]}", "Known backend fixture"),
            (protected_id, f"DS8{suffix[:3]}", "Genuine legacy upload"),
        ):
            s.insert("dq_assets", {
                "asset_id": asset_id, "system_id": system_id, "alias": display_name,
                "display_name": display_name, "kind": "dataset", "time_basis": "none",
                "current_version_no": 1, "lifecycle_status": "active",
                "created_at": s.now_ist(), "updated_at": s.now_ist(),
                "artifact_origin": "unclassified",
            })
        selected_item = _build_item_fixture(f"itm_selected_{suffix}")
        protected_item = _build_item_fixture(f"itm_protected_{suffix}")
        s.update("dq_items", {"item_id": selected_item["item_id"]},
                 {"dataset_family_id": selected_id, "artifact_origin": "unclassified"})
        s.update("dq_items", {"item_id": protected_item["item_id"]},
                 {"dataset_family_id": protected_id, "artifact_origin": "unclassified"})
        legacy_run_id = f"drun_legacy_{suffix}"
        s.insert("diag_runs", {
            "run_id": legacy_run_id, "item_id": protected_item["item_id"],
            "diagnostic_id": 14, "manifest_json": {}, "status": "draft",
            "engine_versions_json": {}, "created_at": s.now_ist(),
            "artifact_origin": "unclassified",
        })

        candidate_payload = admin.development_artifact_candidates(
            f"Bearer {self.admin_token}"
        )
        candidate_ids = {row["asset_id"] for row in candidate_payload["assets"]}
        candidate_run_ids = {row["run_id"] for row in candidate_payload["runs"]}
        self.assertIn(selected_id, candidate_ids)
        self.assertIn(protected_id, candidate_ids)
        self.assertIn(legacy_run_id, candidate_run_ids)

        admin.factory_reset(
            admin.FactoryResetRequest(
                grade="development", confirm="WIPE DEVELOPMENT",
                legacy_asset_ids=[selected_id], legacy_run_ids=[legacy_run_id],
            ),
            f"Bearer {self.admin_token}",
        )
        self.assertIsNone(s.query_one("dq_assets", asset_id=selected_id))
        self.assertIsNone(s.query_one("dq_items", item_id=selected_item["item_id"]))
        self.assertIsNotNone(s.query_one("dq_assets", asset_id=protected_id))
        self.assertIsNotNone(s.query_one("dq_items", item_id=protected_item["item_id"]))
        self.assertIsNone(s.query_one("diag_runs", run_id=legacy_run_id))

    def test_development_run_on_user_item_is_removed_without_deleting_the_item(self):
        suffix = uuid.uuid4().hex[:8]
        fixture = _build_item_fixture(f"itm_user_run_{suffix}")
        s.update("dq_items", {"item_id": fixture["item_id"]},
                 {"artifact_origin": "user"})
        dev_run_id = f"drun_dev_{suffix}"
        user_run_id = f"drun_user_{suffix}"
        for run_id, origin in ((dev_run_id, "development"), (user_run_id, "user")):
            s.insert("diag_runs", {
                "run_id": run_id, "item_id": fixture["item_id"], "diagnostic_id": 14,
                "manifest_json": {}, "status": "draft", "engine_versions_json": {},
                "created_at": s.now_ist(), "artifact_origin": origin,
            })
            s.insert("diag_results", {
                "result_id": f"result_{run_id}", "run_id": run_id,
                "diagnostic_id": 14, "created_at": s.now_ist(),
            })

        result = admin.factory_reset(
            admin.FactoryResetRequest(
                grade="development", confirm="WIPE DEVELOPMENT"
            ),
            f"Bearer {self.admin_token}",
        )
        self.assertEqual(result["deleted"]["diag_runs"], 1)
        self.assertIsNone(s.query_one("diag_runs", run_id=dev_run_id))
        self.assertIsNone(s.query_one("diag_results", result_id=f"result_{dev_run_id}"))
        self.assertIsNotNone(s.query_one("diag_runs", run_id=user_run_id))
        self.assertIsNotNone(s.query_one("diag_results", result_id=f"result_{user_run_id}"))
        self.assertIsNotNone(s.query_one("dq_items", item_id=fixture["item_id"]))
        self.assertTrue(fixture["upload_dir"].exists())


class DiagnosticsCleanupTests(unittest.TestCase):
    """Diagnostics reset preserves sourcing roots and sourcing-owned AAR."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        _make_user("diagnostics_cleanup_admin", ["user", "admin"])
        cls.admin_token = _login("diagnostics_cleanup_admin")

    def test_wipe_diagnostics_removes_runs_and_diagnostic_aar_only(self):
        suffix = uuid.uuid4().hex[:8]
        fixture = _build_item_fixture(f"itm_diag_{suffix}")
        asset_id = f"asset_diag_{suffix}"
        s.insert("dq_assets", {
            "asset_id": asset_id, "system_id": f"DS7{suffix[:3]}",
            "alias": f"mature-{suffix}", "display_name": f"Mature source {suffix}",
            "kind": "dataset", "time_basis": "none", "current_version_no": 1,
            "lifecycle_status": "active", "created_at": s.now_ist(),
            "updated_at": s.now_ist(), "artifact_origin": "user",
        })
        s.update("dq_items", {"item_id": fixture["item_id"]}, {
            "dataset_family_id": asset_id, "artifact_origin": "user",
        })
        run_id = f"drun_diagwipe_{suffix}"
        result_id = f"dres_diagwipe_{suffix}"
        finding_id = f"dfind_diagwipe_{suffix}"
        diagnostic_issue_id = f"dissue_diagwipe_{suffix}"
        diagnostic_case_id = f"dcase_diagwipe_{suffix}"
        s.insert("diag_runs", {
            "run_id": run_id, "item_id": fixture["item_id"], "diagnostic_id": 14,
            "manifest_json": {}, "status": "done", "engine_versions_json": {},
            "created_at": s.now_ist(), "finished_at": s.now_ist(),
            "artifact_origin": "user",
        })
        s.insert("diag_results", {
            "result_id": result_id, "run_id": run_id, "diagnostic_id": 14,
            "created_at": s.now_ist(),
        })
        s.insert("diag_findings", {
            "finding_id": finding_id, "result_id": result_id, "run_id": run_id,
            "created_at": s.now_ist(),
        })
        s.insert("issues_v2", {
            "issue_row_id": diagnostic_issue_id, "item_id": fixture["item_id"],
            "run_id": run_id, "finding_id": finding_id, "status": "Open",
            "created_at": s.now_ist(), "updated_at": s.now_ist(),
        })
        s.insert("rca_cases", {
            "case_id": diagnostic_case_id, "tenant_id": TENANT,
            "issue_row_id": diagnostic_issue_id, "item_id": fixture["item_id"],
            "state": "opening_looks", "created_at": s.now_ist(),
            "updated_at": s.now_ist(), "contract_version": "1",
        })

        artifact_root = s._analysis_artifact_root()
        artifact_root.mkdir(parents=True, exist_ok=True)
        source_artifact_id = f"art_source_{suffix}"
        diagnostic_artifact_id = f"art_psi_{suffix}"
        for artifact_id, artifact_type, payload_path, artifact_run_id in (
            (source_artifact_id, "column_profile", f"{source_artifact_id}.json", None),
            (diagnostic_artifact_id, "psi_bins", f"{diagnostic_artifact_id}.json", run_id),
        ):
            (artifact_root / payload_path).write_text("{}", encoding="utf-8")
            s.insert("analysis_artifacts", {
                "artifact_id": artifact_id, "artifact_type": artifact_type,
                "asset_id": asset_id, "snapshot_id": fixture["item_id"],
                "population_fingerprint": "population", "methodology_fingerprint": "method",
                "scope": "universal", "payload_path": payload_path, "payload_hash": "hash",
                "status": "active", "source_artifact_ids_json": [], "run_id": artifact_run_id,
                "created_at": s.now_ist(),
            })
        s.insert("analysis_artifact_sources", {
            "artifact_id": diagnostic_artifact_id,
            "source_artifact_id": source_artifact_id, "role": "source",
        })

        response = admin.factory_reset(
            admin.FactoryResetRequest(
                grade="diagnostics", confirm="WIPE DIAGNOSTICS",
                wipe_all_diagnostics=True,
            ),
            f"Bearer {self.admin_token}",
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["grade"], "diagnostics")
        self.assertIsNone(s.query_one("diag_runs", run_id=run_id))
        self.assertIsNone(s.query_one("diag_results", result_id=result_id))
        self.assertIsNone(s.query_one("diag_findings", finding_id=finding_id))
        self.assertIsNone(s.query_one("issues_v2", issue_row_id=diagnostic_issue_id))
        self.assertIsNone(s.query_one("rca_cases", case_id=diagnostic_case_id))
        self.assertIsNone(s.query_one("analysis_artifacts", artifact_id=diagnostic_artifact_id))
        self.assertFalse((artifact_root / f"{diagnostic_artifact_id}.json").exists())

        self.assertIsNotNone(s.query_one("dq_assets", asset_id=asset_id))
        self.assertIsNotNone(s.query_one("dq_items", item_id=fixture["item_id"]))
        self.assertIsNotNone(s.query_one("dq_item_files", item_id=fixture["item_id"]))
        self.assertIsNotNone(s.query_one("variable_inventory", item_id=fixture["item_id"]))
        self.assertTrue(fixture["upload_dir"].exists())
        self.assertIsNotNone(s.query_one("analysis_artifacts", artifact_id=source_artifact_id))
        self.assertTrue((artifact_root / f"{source_artifact_id}.json").exists())
        # The fixture's unrelated legacy plan/result/issue/RCA work is not a
        # descendant of a diagnostic run and must not be broadened into scope.
        self.assertIsNotNone(s.query_one("plan_v2", row_id=fixture["row_id"]))
        self.assertIsNotNone(s.query_one("issues_v2", issue_row_id=fixture["issue_row_id"]))
        self.assertIsNotNone(s.query_one("rca_cases", case_id=fixture["case_id"]))

    def test_wipe_selected_diagnostic_run_preserves_other_run_and_shared_aar(self):
        suffix = uuid.uuid4().hex[:8]
        fixture = _build_item_fixture(f"itm_diag_selected_{suffix}")
        selected_run = f"drun_selected_{suffix}"
        retained_run = f"drun_retained_{suffix}"
        artifact_root = s._analysis_artifact_root()
        artifact_root.mkdir(parents=True, exist_ok=True)
        for run_id in (selected_run, retained_run):
            s.insert("diag_runs", {
                "run_id": run_id, "item_id": fixture["item_id"], "diagnostic_id": 14,
                "manifest_json": {}, "status": "draft", "engine_versions_json": {},
                "created_at": s.now_ist(), "artifact_origin": "user",
            })
            artifact_id = f"art_{run_id}"
            (artifact_root / f"{artifact_id}.json").write_text("{}", encoding="utf-8")
            s.insert("analysis_artifacts", {
                "artifact_id": artifact_id, "artifact_type": "psi_bins",
                "asset_id": "asset", "snapshot_id": fixture["item_id"],
                "population_fingerprint": "population", "methodology_fingerprint": "method",
                "scope": "universal", "payload_path": f"{artifact_id}.json",
                "payload_hash": "hash", "status": "active",
                "source_artifact_ids_json": [], "run_id": run_id,
                "created_at": s.now_ist(),
            })
        shared_id = f"art_shared_{suffix}"
        (artifact_root / f"{shared_id}.json").write_text("{}", encoding="utf-8")
        s.insert("analysis_artifacts", {
            "artifact_id": shared_id, "artifact_type": "psi_bins",
            "asset_id": "asset", "snapshot_id": fixture["item_id"],
            "population_fingerprint": "population", "methodology_fingerprint": "shared",
            "scope": "universal", "payload_path": f"{shared_id}.json",
            "payload_hash": "hash", "status": "active",
            "source_artifact_ids_json": [], "run_id": None,
            "created_at": s.now_ist(),
        })

        listed = admin.diagnostic_run_candidates(
            f"Bearer {self.admin_token}"
        )["runs"]
        self.assertTrue({selected_run, retained_run} <= {row["run_id"] for row in listed})
        admin.factory_reset(
            admin.FactoryResetRequest(
                grade="diagnostics", confirm="WIPE DIAGNOSTICS",
                diagnostic_run_ids=[selected_run],
            ),
            f"Bearer {self.admin_token}",
        )

        self.assertIsNone(s.query_one("diag_runs", run_id=selected_run))
        self.assertIsNone(s.query_one("analysis_artifacts", artifact_id=f"art_{selected_run}"))
        self.assertIsNotNone(s.query_one("diag_runs", run_id=retained_run))
        self.assertIsNotNone(s.query_one("analysis_artifacts", artifact_id=f"art_{retained_run}"))
        self.assertIsNotNone(s.query_one("analysis_artifacts", artifact_id=shared_id))
        self.assertIsNotNone(s.query_one("dq_items", item_id=fixture["item_id"]))


class AdminGateInvariantTests(unittest.TestCase):
    """2-T7 — the WSP-08 admin gate must genuinely bite: a rejected call
    (403 non-admin / 401 no-auth / 400 wrong-confirm / 400 unknown-grade)
    deletes nothing. Kept in its own TestCase (never calls a *successful*
    reset) so a sibling class's exact-delta assertions are never affected
    by this class's fixtures."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        _make_user("gate_admin", ["user", "admin"])
        _make_user("gate_plain_user", ["user"])
        cls.admin_token = _login("gate_admin")
        cls.plain_token = _login("gate_plain_user")

    def test_non_admin_session_is_403_and_deletes_nothing(self):
        item_id = f"itm_gate403_{uuid.uuid4().hex[:8]}"
        fixture = _build_item_fixture(item_id)
        with self.assertRaises(HTTPException) as ctx:
            admin.factory_reset(
                admin.FactoryResetRequest(grade="surgical", confirm="RESET"),
                f"Bearer {self.plain_token}")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIsNotNone(s.query_one("dq_items", item_id=item_id))
        self.assertIsNotNone(s.query_one("plan_v2", row_id=fixture["row_id"]))
        self.assertIsNotNone(s.query_one("rca_cases", case_id=fixture["case_id"]))
        self.assertTrue(fixture["upload_dir"].exists())

    def test_no_auth_is_401_and_deletes_nothing(self):
        item_id = f"itm_gate401_{uuid.uuid4().hex[:8]}"
        fixture = _build_item_fixture(item_id)
        with self.assertRaises(HTTPException) as ctx:
            admin.factory_reset(
                admin.FactoryResetRequest(grade="surgical", confirm="RESET"), None)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIsNotNone(s.query_one("dq_items", item_id=item_id))
        self.assertIsNotNone(s.query_one("plan_v2", row_id=fixture["row_id"]))

    def test_wrong_confirm_is_400_and_deletes_nothing(self):
        item_id = f"itm_gate400_{uuid.uuid4().hex[:8]}"
        fixture = _build_item_fixture(item_id)

        with self.assertRaises(HTTPException) as ctx:
            admin.factory_reset(
                admin.FactoryResetRequest(grade="surgical", confirm="reset"),  # wrong case
                f"Bearer {self.admin_token}")
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx2:
            admin.factory_reset(
                admin.FactoryResetRequest(grade="wipe", confirm=""),  # missing
                f"Bearer {self.admin_token}")
        self.assertEqual(ctx2.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx3:
            admin.factory_reset(
                admin.FactoryResetRequest(grade="surgical", confirm="WIPE EVERYTHING"),  # wrong grade's phrase
                f"Bearer {self.admin_token}")
        self.assertEqual(ctx3.exception.status_code, 400)

        self.assertIsNotNone(s.query_one("dq_items", item_id=item_id))
        self.assertIsNotNone(s.query_one("plan_v2", row_id=fixture["row_id"]))
        self.assertIsNotNone(s.query_one("rca_cases", case_id=fixture["case_id"]))

    def test_unknown_grade_is_400_and_deletes_nothing(self):
        item_id = f"itm_gategrade_{uuid.uuid4().hex[:8]}"
        fixture = _build_item_fixture(item_id)
        with self.assertRaises(HTTPException) as ctx:
            admin.factory_reset(
                admin.FactoryResetRequest(grade="bogus", confirm="RESET"),
                f"Bearer {self.admin_token}")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIsNotNone(s.query_one("dq_items", item_id=item_id))
        self.assertIsNotNone(s.query_one("plan_v2", row_id=fixture["row_id"]))


class SanitizerTests(unittest.TestCase):
    """PLT-04 — an unexpected exception from the underlying reset function
    must never leak its text to the client; the response is a generic 500."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        _make_user("sanitizer_admin", ["user", "admin"])
        cls.admin_token = _login("sanitizer_admin")

    def test_unexpected_exception_returns_generic_500_not_the_raw_error(self):
        secret_message = "sqlite3.OperationalError: disk I/O error at C:/secret/internal/path.db"
        original = admin.s.reset_demo

        def _boom():
            raise RuntimeError(secret_message)

        admin.s.reset_demo = _boom
        try:
            with self.assertRaises(HTTPException) as ctx:
                admin.factory_reset(
                    admin.FactoryResetRequest(grade="surgical", confirm="RESET"),
                    f"Bearer {self.admin_token}")
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertEqual(ctx.exception.detail, "internal error")
            self.assertNotIn(secret_message, str(ctx.exception.detail))
            self.assertNotIn("secret", str(ctx.exception.detail))
            self.assertNotIn("OperationalError", str(ctx.exception.detail))
        finally:
            admin.s.reset_demo = original


if __name__ == "__main__":
    unittest.main()
