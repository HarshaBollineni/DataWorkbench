"""Phase 1B Slice 1: contracts, snapshot seam, artifacts, and registry."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import pandas as pd

_TMP_ROOT = Path(tempfile.gettempdir()) / "archimedes-test-analysis-runtime"
if _TMP_ROOT.exists():
    shutil.rmtree(_TMP_ROOT)
_TMP_ROOT.mkdir(parents=True)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_ROOT / "state.db")
os.environ["ANALYSIS_ARTIFACT_DIR"] = str(_TMP_ROOT / "artifacts")
os.environ["ITEM_DB_DIR"] = str(_TMP_ROOT / "item-dbs")
os.environ["UPLOAD_DIR"] = str(_TMP_ROOT / "uploads")
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from ai.v2 import service  # noqa: E402
from domains.aar.repository import (  # noqa: E402
    AnalysisArtifactRepository,
    ArtifactConflictError,
    ArtifactIntegrityError,
)
from domains.aar.types import ArtifactTypeDescriptor, register_artifact_type  # noqa: E402
from domains.aar.data_sourcing import _safe_profile, persist_snapshot_profile_artifacts  # noqa: E402
from analysis_runtime.capabilities import (  # noqa: E402
    clear_capabilities_for_tests,
    get_capability,
    list_capabilities,
    register_capability,
)
from analysis_runtime.contracts import (  # noqa: E402
    AnalysisScope,
    FrozenAnalysisManifest,
    SnapshotRef,
    stable_fingerprint,
    target_fingerprint,
)
from analysis_runtime.snapshots import SnapshotLoader, SnapshotNotReadyError  # noqa: E402
from routers.analyses import analysis_artifact_overview, analysis_catalog  # noqa: E402


def _asset_and_snapshot(*, ingest_status: str = "ready",
                        snapshot_status: str = "active") -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    asset_id = f"asset_{suffix}"
    snapshot_id = f"item_{suffix}"
    now = s.now_ist()
    s.insert("dq_assets", {
        "asset_id": asset_id, "system_id": f"DS{suffix[:4].upper()}",
        "alias": f"probe-{suffix}", "display_name": f"DS-{suffix}",
        "kind": "dataset", "time_basis": "none", "current_version_no": 1,
        "lifecycle_status": "sourcing", "created_at": now, "updated_at": now,
    })
    s.insert("dq_items", {
        "item_id": snapshot_id, "kind": "dataset", "name": f"DS-{suffix}",
        "status": "sourcing", "created_at": now, "updated_at": now,
        "dataset_family_id": asset_id, "delivery_seq": 1, "version_no": 1,
        "snapshot_status": snapshot_status, "snapshot_label": snapshot_id,
        # Governed profile publication is tenant-scoped.  Shared AAR fixtures
        # model a normal ready Data Sourcing snapshot rather than the legacy
        # tenantless compatibility path.
        "intent": "fresh", "ingest_status": ingest_status, "sourcing_tenant_id": "tenant-a",
    })
    s.insert("dq_item_tables", {
        "item_id": snapshot_id, "table_name": "portfolio", "row_count": 2,
        "col_count": 2, "columns": ["id", "score"],
    })
    return asset_id, snapshot_id


class ContractTests(unittest.TestCase):
    def test_fingerprints_ignore_dictionary_key_order(self):
        self.assertEqual(stable_fingerprint({"a": 1, "b": 2}),
                         stable_fingerprint({"b": 2, "a": 1}))

    def test_binary_target_fingerprint_includes_effective_positive_class(self):
        event_one = target_fingerprint(
            "outcome", target_type="binary", positive_class=1,
        )
        event_zero = target_fingerprint(
            "outcome", target_type="binary", positive_class=0,
        )
        self.assertNotEqual(event_one, event_zero)
        self.assertEqual(
            event_one,
            target_fingerprint("outcome", target_type="binary", positive_class="1"),
        )

    def test_manifest_fingerprint_changes_with_methodology(self):
        ref = SnapshotRef("item_1", "asset_1", "DS0001", "DS0001-probe", "dataset",
                          1, "Delivery 1", "active", "ready", ("portfolio",))
        scope = AnalysisScope("portfolio", ("score",), {"country": "GB"}, "bad")
        first = FrozenAnalysisManifest("iv", "1.0", ref, scope, {"bins": 10})
        second = FrozenAnalysisManifest("iv", "1.0", ref, scope, {"bins": 20})
        self.assertNotEqual(first.fingerprint, second.fingerprint)


class SnapshotLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def test_reference_uses_existing_asset_and_snapshot_identities(self):
        asset_id, snapshot_id = _asset_and_snapshot()
        ref = SnapshotLoader(table_reader=lambda *_args, **_kwargs: pd.DataFrame()).reference(snapshot_id)
        self.assertEqual(ref.asset_id, asset_id)
        self.assertEqual(ref.snapshot_id, snapshot_id)
        self.assertEqual(ref.tables, ("portfolio",))

    def test_loader_rejects_unready_and_historical_by_default(self):
        _, unready = _asset_and_snapshot(ingest_status="processing")
        with self.assertRaises(SnapshotNotReadyError):
            SnapshotLoader(table_reader=lambda *_args, **_kwargs: pd.DataFrame()).reference(unready)
        _, historical = _asset_and_snapshot(snapshot_status="superseded")
        with self.assertRaises(SnapshotNotReadyError):
            SnapshotLoader(table_reader=lambda *_args, **_kwargs: pd.DataFrame()).reference(historical)

    def test_load_table_passes_validated_selection_to_reader(self):
        _, snapshot_id = _asset_and_snapshot()
        calls = []

        def reader(item_id, table, columns=None):
            calls.append((item_id, table, columns))
            return pd.DataFrame({"score": [1, 2]})

        frame = SnapshotLoader(table_reader=reader).load_table(snapshot_id, "portfolio", ["score"])
        self.assertEqual(calls, [(snapshot_id, "portfolio", ["score"])])
        self.assertEqual(frame["score"].tolist(), [1, 2])

    def test_public_reader_returns_selected_copy_and_validates_columns(self):
        _, snapshot_id = _asset_and_snapshot()
        service._write_table(snapshot_id, "portfolio", pd.DataFrame({
            "id": [1, 2], "score": [0.1, 0.2],
        }))
        result = service.read_snapshot_table(snapshot_id, "portfolio", ["score"])
        self.assertEqual(list(result.columns), ["score"])
        with self.assertRaises(KeyError):
            service.read_snapshot_table(snapshot_id, "portfolio", ["missing"])


class ArtifactRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()

    def setUp(self):
        self.root = _TMP_ROOT / f"artifact-case-{uuid.uuid4().hex}"
        self.repo = AnalysisArtifactRepository(self.root)
        self.asset_id, self.snapshot_id = _asset_and_snapshot()
        self.base = {
            "artifact_type": "missingness_pattern",
            "asset_id": self.asset_id,
            "snapshot_id": self.snapshot_id,
            "population_fingerprint": stable_fingerprint({"scope": "all"}),
            "methodology_fingerprint": stable_fingerprint({"version": "1"}),
            "scope": "supporting_analysis",
        }

    def test_bounded_integrity_audit_does_not_misclassify_catalogued_payloads(self):
        self.repo.save_or_reuse({"rate": 0.2}, **self.base)
        changed = {**self.base, "methodology_fingerprint": stable_fingerprint({"version": "2"})}
        self.repo.save_or_reuse({"rate": 0.3}, **changed)

        audit = self.repo.integrity_audit(limit=1)

        self.assertEqual(audit["orphan_payload_files"], [])
        self.assertTrue(audit["bounded"])
        self.assertEqual(audit["status"], "healthy")

    def test_exact_match_reuses_but_methodology_change_does_not(self):
        first, reused = self.repo.save_or_reuse({"rate": 0.2}, **self.base)
        second, reused_second = self.repo.save_or_reuse({"rate": 0.9}, **self.base)
        changed = {**self.base, "methodology_fingerprint": stable_fingerprint({"version": "2"})}
        third, reused_third = self.repo.save_or_reuse({"rate": 0.9}, **changed)
        self.assertFalse(reused)
        self.assertTrue(reused_second)
        self.assertEqual(first.artifact_id, second.artifact_id)
        self.assertFalse(reused_third)
        self.assertNotEqual(first.artifact_id, third.artifact_id)

    def test_payload_integrity_is_verified(self):
        metadata, _ = self.repo.save_or_reuse({"rate": 0.2}, **self.base)
        (self.root / metadata.payload_path).write_text("{}", encoding="utf-8")
        with self.assertRaises(ArtifactIntegrityError):
            self.repo.get(metadata.artifact_id)

    def test_lineage_impact_and_supersession(self):
        source, _ = self.repo.save_or_reuse({"profile": 1}, **self.base)
        derived_args = {**self.base, "artifact_type": "iv_table", "feature": "score",
                        "source_artifact_ids": (source.artifact_id,)}
        derived, _ = self.repo.save_or_reuse({"iv": 0.3}, **derived_args)
        self.assertEqual([row.artifact_id for row in self.repo.impact(source.artifact_id)],
                         [derived.artifact_id])
        self.assertEqual(
            s.query("analysis_artifact_sources", source_artifact_id=source.artifact_id)[0]["artifact_id"],
            derived.artifact_id,
        )
        superseded = self.repo.supersede(source.artifact_id, by_artifact_id=derived.artifact_id)
        self.assertEqual(superseded.status, "superseded")

    def test_governed_identity_conflict_and_synthetic_type_extension(self):
        artifact_type = f"synthetic_test_evidence_{uuid.uuid4().hex[:8]}"
        register_artifact_type(ArtifactTypeDescriptor(
            artifact_type=artifact_type, display_name="Synthetic test evidence",
            description="Test-only extension proof", owner="tests",
            summary_adapter=lambda payload: {"metrics": [{"metric_key": "score", "value": payload["score"]}]},
        ))
        args = {**self.base, "artifact_type": artifact_type, "scope": "universal"}
        first = self.repo.save({"score": 1}, **args)
        reused = self.repo.save({"score": 1}, **args)
        self.assertEqual(first.outcome, "created")
        self.assertEqual(reused.outcome, "reused")
        self.assertEqual(reused.artifact.summary["metrics"][0]["value"], 1)
        with self.assertRaises(ArtifactConflictError):
            self.repo.save({"score": 2}, **args)

    def test_modern_identity_differences_do_not_fall_back_to_legacy_reuse(self):
        artifact_type = f"identity_test_evidence_{uuid.uuid4().hex[:8]}"
        register_artifact_type(ArtifactTypeDescriptor(
            artifact_type=artifact_type, display_name="Identity test evidence",
            description="Test-only full identity proof", owner="tests",
        ))
        args = {**self.base, "artifact_type": artifact_type, "scope": "universal"}
        first = self.repo.save({"score": 1}, **args, table="first",
                               identity_inputs={"dictionary_version": "v1"})
        second = self.repo.save({"score": 2}, **args, table="second",
                                identity_inputs={"dictionary_version": "v2"})
        self.assertEqual(first.outcome, "created")
        self.assertEqual(second.outcome, "created")
        self.assertNotEqual(first.artifact.artifact_id, second.artifact.artifact_id)

    def test_sql_page_returns_filtered_window_and_total(self):
        for index, feature in enumerate(("alpha", "beta", "gamma")):
            self.repo.save_or_reuse(
                {"score": index}, **{
                    **self.base,
                    "methodology_fingerprint": stable_fingerprint({"version": index}),
                    "feature": feature,
                },
            )
        first = self.repo.page(
            asset_id=self.asset_id, snapshot_id=self.snapshot_id,
            limit=2, offset=0,
        )
        second = self.repo.page(
            asset_id=self.asset_id, snapshot_id=self.snapshot_id,
            limit=2, offset=2,
        )
        filtered = self.repo.page(
            asset_id=self.asset_id, snapshot_id=self.snapshot_id,
            feature_query="ET", limit=2, offset=0,
        )
        self.assertEqual((first["total"], len(first["artifacts"])), (3, 2))
        self.assertEqual((second["total"], len(second["artifacts"])), (3, 1))
        self.assertEqual([row["feature"] for row in filtered["artifacts"]], ["beta"])

    def test_registered_identity_and_source_contracts_are_enforced(self):
        source_type = f"contract_source_{uuid.uuid4().hex[:8]}"
        child_type = f"contract_child_{uuid.uuid4().hex[:8]}"
        register_artifact_type(ArtifactTypeDescriptor(
            artifact_type=source_type, display_name="Contract source",
            description="Test source", owner="tests",
        ))
        register_artifact_type(ArtifactTypeDescriptor(
            artifact_type=child_type, display_name="Contract child",
            description="Test child", owner="tests",
            supported_scopes=("universal",), target_applicability="required",
            allowed_source_types=(source_type,),
        ))
        source = self.repo.save({"source": True}, **{
            **self.base, "artifact_type": source_type, "scope": "universal",
        }).artifact
        child_args = {
            **self.base, "artifact_type": child_type, "scope": "universal",
            "target_fingerprint": "target-v1",
            "source_artifact_ids": (source.artifact_id,),
        }
        self.assertEqual(self.repo.save({"child": True}, **child_args).outcome, "created")
        with self.assertRaisesRegex(ValueError, "target_fingerprint is required"):
            self.repo.save({"child": False}, **{**child_args,
                "target_fingerprint": None,
                "methodology_fingerprint": "missing-target"})
        with self.assertRaisesRegex(ValueError, "scope .* is not supported"):
            self.repo.save({"child": False}, **{**child_args,
                "scope": "diagnostic_local", "owner_id": "owner",
                "methodology_fingerprint": "bad-scope"})

    def test_ready_data_sourcing_profiles_backfill_as_governed_artifacts(self):
        now = s.now_ist()
        s.insert("variable_inventory", {
            "item_id": self.snapshot_id, "table_name": "portfolio", "column_name": "score",
            "classification": "numerical", "data_type": "float", "description": "",
            "discrepancies": [], "notes": "", "role": "feature", "profile_json": {
                "non_null_count": 8, "null_count": 2, "cardinality": 4, "null_share": .2,
                "min": 1, "max": 4, "mean": 2.5, "histogram": [], "top_k": {"1": 2},
                "calculation_method": "exact",
            }, "role_reviewed": 1, "provisional": 0, "updated_at": now,
        })
        ids = persist_snapshot_profile_artifacts(self.snapshot_id)
        self.assertEqual(len(ids), 3)
        rows = self.repo.list(snapshot_id=self.snapshot_id)
        self.assertEqual({row.artifact_type for row in rows},
                         {"column_profile", "table_profile", "table_inventory_profile"})
        self.assertEqual(ids, persist_snapshot_profile_artifacts(self.snapshot_id))
        overview = analysis_artifact_overview(asset_id=self.asset_id, snapshot_id=self.snapshot_id)
        self.assertEqual(overview["active_artifacts"], 3)
        self.assertEqual(overview["represented_features"], 1)

    def test_confirmed_exact_core_profile_is_fully_retained_in_aar(self):
        profile = service._column_profile(
            pd.Series([10.0, 20.0, -999.0, None]), ["-999"], True)
        s.insert("variable_inventory", {
            "item_id": self.snapshot_id, "table_name": "portfolio", "column_name": "amount",
            "classification": "numerical", "data_type": "float64", "description": "",
            "discrepancies": [], "notes": "", "role": "Feature",
            "missing_value_codes_json": ["-999"], "missing_codes_confirmed": 1,
            "profile_json": profile, "role_reviewed": 1, "provisional": 0, "updated_at": s.now_ist(),
        })

        # Publication completion re-verifies payloads through the governed
        # default repository root. Use that same root in this profile test;
        # a per-test repository is appropriate for repository-only cases but
        # cannot represent a publishable Data Sourcing snapshot.
        governed_repo = AnalysisArtifactRepository()
        persist_snapshot_profile_artifacts(
            self.snapshot_id, actor="test", artifact_repository=governed_repo)
        artifact = governed_repo.list(
            snapshot_id=self.snapshot_id, artifact_type="column_profile", feature="amount",
            status="active")[0]
        _, payload = governed_repo.get(artifact.artifact_id)

        self.assertEqual(payload["profile_basis"], "confirmed_regular_values")
        self.assertEqual(payload["calculation_method"], "exact")
        self.assertEqual(payload["special_value_counts"], {"-999": 1})
        self.assertEqual(payload["regular_value_count"], 2)
        self.assertEqual(payload["mean"], 15.0)
        self.assertIn("percentiles", payload)
        self.assertIn("mad", payload)
        self.assertEqual(sum(row["count"] for row in payload["histogram"]), 2)

    def test_aar_rejects_character_split_or_unreconciled_special_evidence(self):
        with self.assertRaisesRegex(ValueError, "inconsistent confirmed special-value evidence"):
            _safe_profile({
                "table_name": "portfolio", "column_name": "amount",
                "missing_value_codes_json": json.dumps(["-999"]),
                "missing_codes_confirmed": 1,
                "profile_json": {
                    "calculation_method": "exact",
                    "profile_basis": "confirmed_regular_values",
                    "normalized_special_values": ["[", "9", "]"],
                    "special_value_counts": {"[": 0, "9": 2, "]": 0},
                    "special_value_row_count": 2,
                },
            })

    def test_retained_profile_requires_explicit_exactness_and_review_provenance(self):
        base = {
            "table_name": "portfolio", "column_name": "score", "role": "Identifier",
            "profile_json": {"calculation_method": "exact", "cardinality": 1},
        }
        self.assertFalse(_safe_profile({**base, "provisional": 0})["metadata_reviewed"])
        self.assertTrue(_safe_profile({**base, "provisional": 1, "role_reviewed": 1})["metadata_reviewed"])
        with self.assertRaisesRegex(ValueError, "no exact retained profile"):
            _safe_profile({**base, "profile_json": {"cardinality": 1}})

    def test_categorical_profile_retention_is_role_aware(self):
        values = {f"category-{index:02d}": 100 - index for index in range(60)}

        identifier = _safe_profile({
            "role": "Identifier", "classification": "categorical",
            "profile_json": {"calculation_method": "exact", "cardinality": 60, "top_k": values},
        })
        feature = _safe_profile({
            "role": "Feature", "classification": "categorical",
            "profile_json": {"calculation_method": "exact", "cardinality": 60, "top_k": values},
        })

        self.assertEqual(len(identifier["top_k"]), 5)
        self.assertEqual(identifier["truncation"], {
            "top_k_limit": 5, "identifier_values_retained": True,
        })
        self.assertEqual(len(feature["top_k"]), 50)
        self.assertEqual(feature["truncation"], {
            "top_k_limit": 50, "identifier_values_retained": False,
        })

    def test_data_sourcing_profiler_computes_fifty_categorical_values(self):
        profile = service._column_profile(pd.Series([f"category-{index:02d}" for index in range(60)]))

        self.assertEqual(len(profile["top_k"]), 50)

    def test_string_binary_profile_retains_top_values(self):
        profile = service._column_profile(
            pd.Series(["fixed", "variable", "fixed"], dtype="string"),
            logical_type="binary",
        )

        self.assertEqual(profile["top_k"], {"fixed": 2, "variable": 1})
        self.assertNotIn("numeric_parse_failure_count", profile)
        self.assertNotIn("histogram", profile)

    def test_native_numeric_binary_profile_remains_numeric(self):
        profile = service._column_profile(
            pd.Series([0, 1, 1]), logical_type="binary")

        self.assertIsNone(profile["top_k"])
        self.assertEqual(profile["mean"], 2 / 3)
        self.assertTrue(profile["histogram"])

    def test_reviewed_schema_change_supersedes_the_old_active_projection(self):
        now = s.now_ist()
        s.insert("variable_inventory", {
            "item_id": self.snapshot_id, "table_name": "portfolio", "column_name": "score",
            "classification": "numerical", "data_type": "float", "description": "",
            "discrepancies": [], "notes": "", "role": "Feature", "profile_json": {
                "non_null_count": 2, "null_count": 0, "cardinality": 2, "null_share": 0,
                "calculation_method": "exact",
            }, "role_reviewed": 1, "provisional": 0, "updated_at": now,
        })
        persist_snapshot_profile_artifacts(self.snapshot_id)
        first = self.repo.list(snapshot_id=self.snapshot_id, artifact_type="column_profile",
                               status="active")[0]

        s.update("variable_inventory", {
            "item_id": self.snapshot_id, "table_name": "portfolio", "column_name": "score",
        }, {"role": "Ignore", "updated_at": s.now_ist()})
        persist_snapshot_profile_artifacts(self.snapshot_id)

        active = self.repo.list(snapshot_id=self.snapshot_id, artifact_type="column_profile",
                                status="active")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].summary["fields"]["role"], "Ignore")
        self.assertEqual(self.repo.get_metadata(first.artifact_id).status, "superseded")
        self.assertEqual(len(self.repo.list(snapshot_id=self.snapshot_id,
                                            artifact_type="table_profile", status="active")), 1)


class CapabilityRegistryTests(unittest.TestCase):
    def tearDown(self):
        clear_capabilities_for_tests()

    def test_registry_and_catalog_expose_only_registered_supporting_capabilities(self):
        class Probe:
            capability_id = "probe"
            name = "Probe analysis"
            version = "1.0"
            description = "Test capability"

        probe = Probe()
        register_capability(probe)
        self.assertIs(get_capability("probe"), probe)
        self.assertIn("probe", {row["capability_id"] for row in list_capabilities()})
        _, snapshot_id = _asset_and_snapshot()
        response = analysis_catalog(snapshot_id)
        self.assertIn("probe", {row["capability_id"] for row in response["capabilities"]})

    def test_duplicate_registration_is_rejected(self):
        class Probe:
            capability_id = "probe"
            name = "Probe"
            version = "1"
            description = "Probe"

        register_capability(Probe())
        with self.assertRaises(ValueError):
            register_capability(Probe())


if __name__ == "__main__":
    unittest.main()
