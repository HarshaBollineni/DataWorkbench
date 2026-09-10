"""Ownership-boundary rejection for the D06 DSC-assist reader."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from domains.test_lab.diagnostics.t2_d06_row_completeness import dsc_assist


@pytest.mark.parametrize(
    "foreign_asset,foreign_snapshot",
    [("asset-foreign", "snapshot-owned"), ("asset-owned", "snapshot-foreign")],
)
def test_d06_assist_ignores_authority_artifact_outside_owned_snapshot(
        monkeypatch, foreign_asset, foreign_snapshot):
    """AAR tenancy is proved by owned item + exact metadata, not payload fields."""
    foreign = SimpleNamespace(
        artifact_id="art-foreign", status="active", artifact_type="dataset_structure_assertion",
        asset_id=foreign_asset, snapshot_id=foreign_snapshot,
    )

    class Repo:
        def list(self, **_kwargs):
            return [foreign]

        def get(self, _artifact_id):
            raise AssertionError("foreign artifact must be ignored before payload access")

    monkeypatch.setattr(dsc_assist, "AnalysisArtifactRepository", Repo)
    monkeypatch.setattr(dsc_assist, "_enabled", lambda _tenant: True)
    result = dsc_assist.resolved_d06_assist(item={
        "sourcing_tenant_id": "tenant-owned", "dataset_family_id": "asset-owned", "item_id": "snapshot-owned",
    }, table="orders")
    assert result == {"state": "unavailable"}


def test_d06_assist_suppresses_expected_cadence_when_group_pin_is_stale(monkeypatch):
    """Cadence is advisory: stale axis/group pins remove only that suggestion."""
    snapshot = {"asset_id": "asset-owned", "snapshot_id": "snapshot-owned"}

    def authority(artifact_id, predicate, value):
        payload = {"context_version": "2", "snapshot": snapshot, "subject": {"table": "orders"},
                   "predicate": predicate, "resolution": {"status": "confirmed", "effective_claim_ids": [artifact_id]},
                   "claims": [{"claim_id": artifact_id, "value": value}]}
        return SimpleNamespace(artifact_id=artifact_id, status="active", artifact_type="dataset_structure_assertion",
                               asset_id=snapshot["asset_id"], snapshot_id=snapshot["snapshot_id"]), payload

    entity, entity_payload = authority("entity-default", "table.structure/default_entity_binding", {})
    temporal, temporal_payload = authority("temporal-default", "table.temporal/default_temporal_binding", {})
    cadence, cadence_payload = authority("cadence-default", "table.temporal/expected_cadence", {
        "kind": "expected_cadence", "unit": "month", "step": 1,
        "axis": {"locator": {"predicate": "table.temporal/temporal_binding", "instance_key": "column:period"},
                 "pin": {"artifact_id": "axis", "payload_hash": "a", "assertion_id": "axis", "dependency_fingerprint": "a"}},
        "grouping": {"locator": {"predicate": "table.structure/entity_binding", "instance_key": "column:facility"},
                     "pin": {"artifact_id": "stale-group", "payload_hash": "b", "assertion_id": "group", "dependency_fingerprint": "b"}},
    })
    payloads = {"entity-default": entity_payload, "temporal-default": temporal_payload, "cadence-default": cadence_payload}

    class Repo:
        def list(self, **_kwargs):
            return [entity, temporal, cadence]

        def get(self, artifact_id):
            return None, payloads[artifact_id]

    def candidate(_repo, value, *, predicate, **_kwargs):
        if predicate == "table.structure/entity_binding" and value.get("candidate_pin", {}).get("artifact_id") == "stale-group":
            return None
        return {"value": {"columns": [{"column": "facility_id" if predicate == "table.structure/entity_binding" else "period"}]}}

    monkeypatch.setattr(dsc_assist, "AnalysisArtifactRepository", Repo)
    monkeypatch.setattr(dsc_assist, "_enabled", lambda _tenant: True)
    monkeypatch.setattr(dsc_assist, "_candidate", candidate)
    result = dsc_assist.resolved_d06_assist(item={
        "sourcing_tenant_id": "tenant-owned", "dataset_family_id": snapshot["asset_id"], "item_id": snapshot["snapshot_id"],
    }, table="orders")
    assert result["state"] == "available"
    assert result["expected_cadence"] is None
