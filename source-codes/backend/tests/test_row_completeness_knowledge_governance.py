"""Governed JSON editing workflow for the T2D6 diagnostic package."""
from __future__ import annotations

import copy
import json

import pytest

import system_db as db
from dq_diagnostics import row_completeness_knowledge as knowledge


@pytest.fixture()
def governed_kb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    db.init_schema()
    knowledge.seed_package()
    return knowledge.list_package_versions()


def test_download_template_is_valid_json_and_explains_safe_editing(governed_kb):
    template = knowledge.editable_template()
    serialized = json.dumps(template)

    assert json.loads(serialized)["based_on_version_id"] == governed_kb["active"]["version_id"]
    assert "change_summary" in template["_editing_guide"]["editable_fields"]
    assert "primitive" in " ".join(template["_editing_guide"]["do_not_change"])
    assert all(rule["_edit_note"] for rule in template["rules"])
    listing = knowledge.list_package_versions()
    assert listing["editor_template"] == template


def test_unsupported_engine_change_is_rejected_without_creating_a_draft(governed_kb):
    template = knowledge.editable_template()
    template["change_summary"] = "Attempt an unsupported executable change."
    template["rules"][0]["primitive"] = "execute_arbitrary_expression"

    with pytest.raises(knowledge.RowCompletenessKnowledgeError, match="not editable"):
        knowledge.upload_package_draft(
            json.dumps(template).encode(), "unsupported.json", "editor")

    assert len(knowledge.list_package_versions()["packages"]) == len(governed_kb["packages"])


def test_upload_creates_inactive_draft_and_reviewer_activation_changes_future_resolution(governed_kb):
    template = copy.deepcopy(knowledge.editable_template())
    template["change_summary"] = "Clarify the key-assignability wording and use a 90% default."
    template["rules"][0]["title"] = "Facility and reporting-period key assignability"
    template["configuration"]["default_continuity_floor"] = 0.90

    draft = knowledge.upload_package_draft(
        json.dumps(template).encode(), "row-completeness-update.json", "kb-editor")

    assert draft["lifecycle_state"] == "draft"
    assert draft["validation_json"]["valid"] is True
    assert knowledge.list_package_versions()["active"]["version_id"] == \
        governed_kb["active"]["version_id"]
    draft_rules = db.query("kb_rules", version_id=draft["version_id"])
    assert len(draft_rules) == 6
    assert {rule["lifecycle_state"] for rule in draft_rules} == {"draft"}
    assert {rule["binding_status"] for rule in draft_rules} == {"bound"}

    activated = knowledge.activate_package(
        draft["version_id"], "Reviewed wording and approved the bounded default change.",
        "kb-reviewer")
    resolved = knowledge.resolve_package()

    assert activated["lifecycle_state"] == "active"
    assert resolved["version_id"] == draft["version_id"]
    assert resolved["rules"][0]["title"] == "Facility and reporting-period key assignability"
    assert resolved["configuration"]["default_continuity_floor"] == 0.90
    assert db.query_one("threshold_settings", diagnostic_id=6, key="continuity_floor",
                        scope="default")["value_json"] == 0.90
    assert db.query_one("diagnostic_kb_packages",
                        version_id=governed_kb["active"]["version_id"])[
                            "lifecycle_state"] == "superseded"


def test_stale_draft_cannot_replace_a_newer_active_version(governed_kb):
    first = knowledge.editable_template()
    first["change_summary"] = "First independent draft."
    first["rules"][0]["user_help"] = "First reviewed wording."
    first_draft = knowledge.upload_package_draft(
        json.dumps(first).encode(), "first.json", "editor")

    second = knowledge.editable_template()
    second["change_summary"] = "Second independent draft."
    second["rules"][0]["user_help"] = "Second reviewed wording."
    second_draft = knowledge.upload_package_draft(
        json.dumps(second).encode(), "second.json", "editor")
    knowledge.activate_package(first_draft["version_id"], "Approve first draft.", "reviewer")

    with pytest.raises(knowledge.RowCompletenessKnowledgeError, match="superseded version"):
        knowledge.activate_package(second_draft["version_id"], "Attempt stale activation.",
                                   "reviewer")
