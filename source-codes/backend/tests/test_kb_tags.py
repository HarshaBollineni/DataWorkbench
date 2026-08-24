"""Phase 5 (KB-02, C-35) — Knowledge Base document tagging tests.

taxonomy.py is already fully generic (any object_type string) — this phase's
work is wiring it to object_type="kb_document" at the router layer
(routers/v3.py: GET/POST/DELETE /knowledge/documents/{document_id}/tags).
Standalone-runnable (``python -m unittest tests.test_kb_tags``); same
SYSTEM_DB_PATH/KB_STORAGE_DIR-at-import-time sandboxing convention as
test_kb.py.
"""
from __future__ import annotations

import inspect
import os
import shutil
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-kb-tags.db"
_TMP_KB_STORAGE = Path(tempfile.gettempdir()) / "archimedes-test-kb-tags-storage"
if _TMP_DB.exists():
    _TMP_DB.unlink()
if _TMP_KB_STORAGE.exists():
    shutil.rmtree(_TMP_KB_STORAGE)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["KB_STORAGE_DIR"] = str(_TMP_KB_STORAGE)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
import kb  # noqa: E402
import taxonomy  # noqa: E402
from seeds.taxonomy_seed import BOOTSTRAP_TENANT, seed_platform, seed_taxonomy  # noqa: E402

TENANT = BOOTSTRAP_TENANT
OBJECT_TYPE = "kb_document"


def _upload_document(filename="doc.md", body="## A rule\nSome text.\n"):
    return kb.upload_document(TENANT, filename, "text/markdown", body.encode("utf-8"), None, "tester")


class KbDocumentTagServiceTests(unittest.TestCase):
    """Service-layer proof that taxonomy.py needs zero changes to support
    kb_document — only the object_type string differs from dq_item's usage
    elsewhere in the app."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_assign_and_get_tags_on_a_kb_document(self):
        upload = _upload_document()
        document_id = upload["document_id"]
        self.assertEqual(taxonomy.get_tags(TENANT, OBJECT_TYPE, document_id), [])

        created = taxonomy.assign_tags(TENANT, OBJECT_TYPE, document_id,
                                       ["risk_type:credit", "use_case:irb"], "tester")
        self.assertEqual(len(created), 2)

        tags = taxonomy.get_tags(TENANT, OBJECT_TYPE, document_id)
        self.assertEqual(len(tags), 2)
        value_keys = {(t["dimension_key"], t["value_key"]) for t in tags}
        self.assertEqual(value_keys, {("risk_type", "credit"), ("use_case", "irb")})
        self.assertTrue(all(t["origin"] == "user_added" for t in tags))

    def test_assigning_the_same_tag_twice_is_idempotent(self):
        upload = _upload_document(filename="doc2.md")
        document_id = upload["document_id"]
        taxonomy.assign_tags(TENANT, OBJECT_TYPE, document_id, ["risk_type:credit"], "tester")
        second = taxonomy.assign_tags(TENANT, OBJECT_TYPE, document_id, ["risk_type:credit"], "tester")
        self.assertEqual(second, [])  # no duplicate row created
        self.assertEqual(len(taxonomy.get_tags(TENANT, OBJECT_TYPE, document_id)), 1)

    def test_remove_tag_requires_a_reason_and_soft_deletes(self):
        upload = _upload_document(filename="doc3.md")
        document_id = upload["document_id"]
        created = taxonomy.assign_tags(TENANT, OBJECT_TYPE, document_id, ["portfolio:retail"], "tester")
        assignment_id = created[0]["assignment_id"]

        with self.assertRaises(ValueError):
            taxonomy.remove_tag(TENANT, assignment_id, "", "tester")

        removed = taxonomy.remove_tag(TENANT, assignment_id, "mis-tagged", "tester")
        self.assertIsNotNone(removed["removed_at"])
        self.assertEqual(taxonomy.get_tags(TENANT, OBJECT_TYPE, document_id), [])

    def test_unknown_dimension_value_is_rejected(self):
        upload = _upload_document(filename="doc4.md")
        with self.assertRaises(ValueError):
            taxonomy.assign_tags(TENANT, OBJECT_TYPE, upload["document_id"],
                                 ["not_a_real_dimension:whatever"], "tester")

    def test_kb_document_tags_are_isolated_from_dq_item_tags_with_the_same_id(self):
        """object_type genuinely partitions the tag space — tagging a
        kb_document never leaks into (or reads from) a dq_item row that
        happens to share the same object_id string."""
        upload = _upload_document(filename="doc5.md")
        shared_id = upload["document_id"]
        taxonomy.assign_tags(TENANT, OBJECT_TYPE, shared_id, ["risk_type:market"], "tester")
        self.assertEqual(taxonomy.get_tags(TENANT, "dq_item", shared_id), [])
        self.assertEqual(len(taxonomy.get_tags(TENANT, OBJECT_TYPE, shared_id)), 1)


class KbDocumentTagRouterWiringTests(unittest.TestCase):
    """Confirms routers/v3.py actually wires the new endpoints to
    object_type="kb_document" (not just that taxonomy.py itself works)."""

    def test_v3_router_defines_the_three_kb_document_tag_routes(self):
        import routers.v3 as v3
        paths = {(route.path, tuple(sorted(route.methods)))
                for route in v3.router.routes if hasattr(route, "path")}
        expected = {
            ("/api/v3/knowledge/documents/{document_id}/tags", ("GET",)),
            ("/api/v3/knowledge/documents/{document_id}/tags", ("POST",)),
            ("/api/v3/knowledge/documents/{document_id}/tags/{assignment_id}", ("DELETE",)),
        }
        self.assertTrue(expected <= paths, f"missing route(s): {expected - paths}")

    def test_route_handlers_call_taxonomy_with_kb_document_object_type(self):
        import routers.v3 as v3
        for fn in (v3.get_kb_document_tags, v3.assign_kb_document_tags, v3.remove_kb_document_tag):
            src = inspect.getsource(fn)
            self.assertIn('"kb_document"', src, f"{fn.__name__} does not use object_type='kb_document'")

    def test_playback_and_parse_report_routes_exist(self):
        import routers.v3 as v3
        paths = {route.path for route in v3.router.routes if hasattr(route, "path")}
        self.assertIn("/api/v3/knowledge/versions/{version_id}/playback", paths)
        self.assertIn("/api/v3/knowledge/versions/{version_id}/parse-report", paths)


if __name__ == "__main__":
    unittest.main()
