"""Integration coverage for governed T2-D11 semantic suggestions."""
from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
import pytest

import system_db as db
from ai.v2 import service
from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency import manifest
from dq_diagnostics.register import seed_register


@pytest.fixture()
def snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "SYS_DB_PATH", tmp_path / "system.db")
    monkeypatch.setattr(db, "ANALYSIS_ARTIFACT_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(service, "ITEM_DB_ROOT", tmp_path / "item-dbs")
    db.init_schema()
    seed_register()
    suffix = uuid.uuid4().hex[:8]
    asset_id, item_id = f"asset_{suffix}", f"item_{suffix}"
    now = db.now_ist()
    db.insert("dq_assets", {
        "asset_id": asset_id,
        "system_id": f"DS{suffix[:4].upper()}",
        "alias": f"semantic-{suffix}",
        "display_name": f"Semantic-{suffix}",
        "kind": "dataset",
        "time_basis": "period",
        "current_version_no": 1,
        "lifecycle_status": "active",
        "created_at": now,
        "updated_at": now,
    })
    db.insert("dq_items", {
        "item_id": item_id,
        "kind": "dataset",
        "name": f"Semantic-{suffix}",
        "status": "profiled",
        "created_at": now,
        "updated_at": now,
        "dataset_family_id": asset_id,
        "delivery_seq": 1,
        "version_no": 1,
        "snapshot_status": "active",
        "snapshot_label": item_id,
        "intent": "fresh",
        "ingest_status": "ready",
        "target_variable": "default_flag",
    })
    rng = np.random.default_rng(71)
    frame = pd.DataFrame({
        "mystery_metric": rng.normal(size=160),
        "default_flag": rng.binomial(1, 0.2, size=160),
    })
    service._write_table(item_id, "portfolio", frame)
    for column, role in (("mystery_metric", "Feature"), ("default_flag", "Target")):
        db.insert("variable_inventory", {
            "item_id": item_id,
            "table_name": "portfolio",
            "column_name": column,
            "classification": "numeric",
            "data_type": str(frame[column].dtype),
            "description": (
                "A borrower leverage measure" if column == "mystery_metric"
                else "One when the borrower defaults"
            ),
            "discrepancies": [],
            "notes": "",
            "role": role,
            "dictionary_role": role.lower(),
            "profile_json": {
                "total_count": len(frame),
                "non_null_count": len(frame),
                "null_count": 0,
                "cardinality": int(frame[column].nunique()),
            },
            "provisional": 0,
            "updated_at": now,
        })
    return item_id


def _configured_metadata():
    return {
        "model": "configured-experiment-deployment",
        "provider": "azure_openai_v1",
        "provider_api_version": "v1",
        "configuration_source": "t2_d11_experiment_dotenv",
    }


def _result(output):
    return {
        "output": output,
        "response_id": "response-integration-1",
        "model": "configured-experiment-deployment",
        "provider": "azure_openai_v1",
        "provider_api_version": "v1",
        "configuration_source": "t2_d11_experiment_dotenv",
        "prompt_version": "semantic_feature_adjudication_v0_2",
        "contract_version": "semantic_feature_adjudication_contract_v0_1",
        "usage": {"prompt_tokens": 21, "completion_tokens": 11},
    }


def _selected_draft(snapshot):
    draft = manifest.build_manifest(snapshot)
    draft = manifest.patch_manifest(draft["run_id"], {
        "kind": "reference_orientation",
        "orientation": "HIGHER_IS_WORSE",
    })
    return manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_selection",
        "features": ["mystery_metric"],
    })


def _feature(payload):
    return next(row for row in payload["features"] if row["feature"] == "mystery_metric")


def _invoked_event(run_id):
    events = [
        row for row in db.query("diag_inference_events", run_id=run_id)
        if row["event_kind"] == "llm" and bool(row["invoked"])
    ]
    assert len(events) == 1
    return events[0]


def test_match_combines_kb_and_ai_rationales_and_requires_manual_confirmation(
    snapshot, monkeypatch,
):
    draft = _selected_draft(snapshot)
    original = _feature(draft)
    candidate = original["candidates"][0]
    ai_reason = "The feature description is economically consistent with this KB concept."
    monkeypatch.setattr(manifest, "configuration_metadata", _configured_metadata)
    monkeypatch.setattr(manifest, "adjudicate", lambda *_args, **_kwargs: _result({
        "decision": "MATCH",
        "selected_candidate": candidate["canonical_feature"],
        "representation_orientation": "SAME",
        "reason": ai_reason,
    }))

    suggested = manifest.patch_manifest(draft["run_id"], {
        "kind": "semantic_adjudication",
        "feature": "mystery_metric",
    })
    row = _feature(suggested)

    assert candidate["rationale"] in row["rationale"]
    assert ai_reason in row["rationale"]
    assert row["review_required"] is True
    assert suggested["ready_to_run"] is False
    assert any(item["code"] == "feature_classification_required"
               for item in suggested["blockers"])
    event = _invoked_event(draft["run_id"])
    assert event["purpose"] == "directionality_semantic_adjudication"
    assert event["status"] == "succeeded"
    assert event["model"] == "configured-experiment-deployment"
    assert event["row_level_data_included"] == 0
    assert event["payload_json"]["validated_response"]["reason"] == ai_reason
    assert event["payload_json"]["user_disposition"] == "pending_manual_review"

    confirmed = manifest.patch_manifest(draft["run_id"], {
        "kind": "feature_classification",
        "feature": "mystery_metric",
        "expected_direction": row["expected_direction"],
        "canonical_feature": row["canonical_feature"],
        "representation_orientation": row["representation_orientation"],
        "rationale": row["rationale"],
        "include_in_kb": False,
    })
    confirmed_row = _feature(confirmed)
    assert confirmed_row["review_required"] is False
    assert confirmed_row["classification_source"] == "USER_CONFIRMED"
    assert confirmed["ready_to_run"] is True


@pytest.mark.parametrize(
    ("decision", "classification_source", "reason", "expected_direction"),
    [
        (
            "NOT_DIRECTIONAL",
            "LLM_ADJUDICATED_NOT_DIRECTIONAL",
            "The feature is meaningful, but increasing numeric order has no risk interpretation.",
            "NOT_APPLICABLE",
        ),
        (
            "NO_CANDIDATE_MATCH",
            "LLM_NO_KB_MATCH",
            "The supplied concepts do not represent this feature's economic meaning.",
            None,
        ),
        (
            "INSUFFICIENT_CONTEXT",
            "LLM_INSUFFICIENT_CONTEXT",
            "The feature description does not establish its economic meaning.",
            None,
        ),
    ],
)
def test_non_match_ai_outcomes_persist_rationale_as_manual_review_states(
    snapshot, monkeypatch, decision, classification_source, reason, expected_direction,
):
    draft = _selected_draft(snapshot)
    monkeypatch.setattr(manifest, "configuration_metadata", _configured_metadata)
    monkeypatch.setattr(manifest, "adjudicate", lambda *_args, **_kwargs: _result({
        "decision": decision,
        "selected_candidate": None,
        "representation_orientation": None,
        "reason": reason,
    }))

    suggested = manifest.patch_manifest(draft["run_id"], {
        "kind": "semantic_adjudication",
        "feature": "mystery_metric",
    })
    row = _feature(suggested)

    assert row["classification_source"] == classification_source
    assert row["rationale"] == f"AI rationale: {reason}"
    assert row["expected_direction"] == expected_direction
    assert row["review_required"] is True
    assert suggested["ready_to_run"] is False
    assert any(item["code"] == "feature_classification_required"
               for item in suggested["blockers"])
    event = _invoked_event(draft["run_id"])
    assert event["purpose"] == "directionality_semantic_adjudication"
    assert event["status"] == "succeeded"
    assert event["payload_json"]["validated_response"]["decision"] == decision
    assert event["payload_json"]["validated_response"]["reason"] == reason
