"""Phase 4 (0.4.0) — data ingestion redesign behavioral tests (ING-01..ING-10,
D-20; docs/0.4.0/06-ingestion-contract.md).

Standalone-runnable (``python -m unittest tests.test_ingest``); follows the
suite's SYSTEM_DB_PATH/UPLOAD_DIR-at-import-time sandboxing convention (see
test_admin_reset.py / test_delivery.py). Route functions are never used here
— ``ai.v2.service`` is called directly, the repo's established pattern for
this module (test_rca.py does the same).
"""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-ingest.db"
_TMP_UPLOAD_DIR = Path(tempfile.gettempdir()) / "archimedes-test-ingest-uploads"
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if _TMP_DB.exists():
    _TMP_DB.unlink()
if _TMP_UPLOAD_DIR.exists():
    shutil.rmtree(_TMP_UPLOAD_DIR)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["UPLOAD_DIR"] = str(_TMP_UPLOAD_DIR)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from ai.v2 import service  # noqa: E402
from dq_diagnostics import delivery  # noqa: E402
from ingest import classify as ingest_classify, dictionary_state, mapping, warnings as ingest_warnings  # noqa: E402
from ingest.errors import IngestCorruptionError  # noqa: E402

s.init_schema()

ASSESSMENT_CSV = (
    b"facility_id,default_flag,pd,origination_date,maturity_date,outstanding_balance,loan_limit\n"
    b"FAC-001,0,0.02,2020-01-01,2030-01-01,100000,150000\n"
    b"FAC-002,1,0.30,2021-02-01,2031-02-01,125000,140000\n"
    b"FAC-003,0,0.05,2022-03-01,2032-03-01,80000,120000\n"
    b"FAC-004,0,0.08,2023-04-01,2033-04-01,90000,130000\n"
)
CLEAN_DICTIONARY_CSV = (
    b"column,definition,declared_type\n"
    b"facility_id,Unique facility identifier,string\n"
    b"default_flag,Observed default outcome,integer\n"
    b"pd,Probability of default,decimal\n"
    b"origination_date,Facility origination date,date\n"
    b"maturity_date,Facility maturity date,date\n"
    b"outstanding_balance,Current outstanding balance,decimal\n"
    b"loan_limit,Approved loan limit,decimal\n"
)


def _name() -> str:
    return f"ingest-test-{uuid.uuid4().hex[:10]}"


def _upload_dataset(name: str, dict_csv: bytes | None = None, kind: str = "dataset") -> str:
    item = service.create_item(kind, name)
    item_id = item["item_id"]
    service.save_file(item_id, "data", "assessment.csv", ASSESSMENT_CSV)
    if dict_csv is not None:
        service.save_file(item_id, "dictionary", "dictionary.csv", dict_csv)
    return item_id


class SetupMixin:
    @classmethod
    def setUpClass(cls):
        s.init_schema()



# ── 4-T2 — confidence-tiered mapping: high pre-applied, fuzzy surfaced,
#    below-floor never guessed (pure logic, no DB) ───────────────────────────
class MappingTierTests(unittest.TestCase):
    def test_exact_name_is_high_tier_and_pre_applied(self):
        rows = [("facility_id", {"definition": "d", "declared_type": "string"})]
        out = mapping.compute_mapping(rows, ["facility_id", "amount"])
        self.assertEqual(out[0]["tier"], "high")
        self.assertEqual(out[0]["source_column"], "facility_id")
        self.assertEqual(out[0]["status"], "applied")
        self.assertEqual(out[0]["confirmed_by"], "auto")
        self.assertEqual(out[0]["score"], 1.0)

    def test_close_but_inexact_name_is_fuzzy_and_never_auto_applied(self):
        rows = [("origination_dt", {"definition": "d", "declared_type": "date"})]
        out = mapping.compute_mapping(rows, ["origination_date"])
        self.assertEqual(out[0]["tier"], "fuzzy")
        self.assertEqual(out[0]["source_column"], "origination_date")
        self.assertEqual(out[0]["status"], "confirm_suggestion")
        self.assertIsNone(out[0]["confirmed_by"], "a fuzzy suggestion is never silently applied")
        self.assertGreaterEqual(out[0]["score"], mapping.FUZZY_FLOOR)

    def test_unrelated_name_is_below_floor_and_never_guessed(self):
        rows = [("credit_score", {"definition": "d", "declared_type": "integer"})]
        out = mapping.compute_mapping(rows, ["facility_id", "amount"])
        self.assertEqual(out[0]["tier"], "below_floor")
        self.assertIsNone(out[0]["source_column"], "below-floor columns are never guessed")
        self.assertEqual(out[0]["status"], "unmapped")
        self.assertIsNone(out[0]["confirmed_by"])


# ── 4-T3 — one-to-one consumption + duplicate-dictionary hard-fail ──────────
class OneToOneAndDuplicateTests(unittest.TestCase):
    def test_a_claimed_column_is_unavailable_to_a_later_dictionary_row(self):
        # Both dictionary rows would exact-match "facility_id" if it were
        # available twice; only the FIRST (file order) gets it.
        rows = [
            ("facility_id", {"definition": "first claim", "declared_type": "string"}),
            ("facility identifier", {"definition": "second, near-duplicate label", "declared_type": "string"}),
        ]
        out = mapping.compute_mapping(rows, ["facility_id"])
        first, second = out
        self.assertEqual(first["source_column"], "facility_id")
        self.assertEqual(first["tier"], "high")
        self.assertIsNone(second["source_column"], "the pool is empty by the time row 2 is considered")
        self.assertEqual(second["tier"], "below_floor")

    def test_duplicate_dictionary_rows_for_the_same_column_hard_fail(self):
        item_id = _upload_dataset(_name())
        dup_dict = (
            b"column,definition,declared_type\n"
            b"facility_id,First definition,string\n"
            b"facility_id,Conflicting second definition,string\n"
        )
        service.save_file(item_id, "dictionary", "dup.csv", dup_dict)
        rows = service.profile_item(item_id)
        # profile_item never raises on a hard-fail — it records it on the item.
        self.assertEqual(rows, [])
        summary = service.ingest_summary(item_id)
        self.assertEqual(summary["status"], "failed")
        self.assertIn("facility_id", summary["fail_reason"])
        self.assertIn("more than once", summary["fail_reason"])

    def test_duplicate_dictionary_hard_fail_has_a_clear_message_via_parse(self):
        item_id = _upload_dataset(_name())
        dup_dict = b"column,declared_type\nfacility_id,string\nfacility_id,string\n"
        service.save_file(item_id, "dictionary", "dup2.csv", dup_dict)
        with self.assertRaises(IngestCorruptionError) as ctx:
            service._parse_dictionary(item_id)  # noqa: SLF001 - exercising the raising path directly
        self.assertIn("duplicate column definitions are not allowed", str(ctx.exception))


# ── 4-T4 — every warning code fires from a dedicated fixture; none blocks
#    reaching ready; hard-fail is reserved for structural corruption only ───
class WarningCodeTests(unittest.TestCase):
    def test_dict_var_not_in_dataset(self):
        dict_csv = CLEAN_DICTIONARY_CSV + b"credit_score,Applicant credit score,integer\n"
        item_id = _upload_dataset(_name(), dict_csv)
        service.profile_item(item_id)
        summary = service.ingest_summary(item_id)
        codes = {w["code"] for w in summary["warnings"]}
        self.assertIn("dict_var_not_in_dataset", codes)
        self.assertEqual(summary["status"], "ready", "a warning never blocks ready")

    def test_dict_field_unused_on_a_mapping_collision(self):
        dict_csv = (
            b"column,definition,declared_type\n"
            b"facility identifier,Alt label claiming the column first,string\n"
            b"facility_id,The exact-name row that loses the collision,string\n"
            b"default_flag,Observed default outcome,integer\n"
            b"pd,Probability of default,decimal\n"
            b"origination_date,Facility origination date,date\n"
            b"maturity_date,Facility maturity date,date\n"
            b"outstanding_balance,Current outstanding balance,decimal\n"
            b"loan_limit,Approved loan limit,decimal\n"
        )
        item_id = _upload_dataset(_name(), dict_csv)
        service.profile_item(item_id)
        summary = service.ingest_summary(item_id)
        unused = [w for w in summary["warnings"] if w["code"] == "dict_field_unused"]
        self.assertTrue(unused, "the collided dictionary row must surface as preserved-but-unused")
        self.assertIn("preserved", unused[0]["message"])
        self.assertEqual(summary["status"], "ready")

    def test_type_conflict(self):
        # outstanding_balance is numeric in the data; declare it as text.
        dict_csv = CLEAN_DICTIONARY_CSV.replace(b"outstanding_balance,Current outstanding balance,decimal",
                                                 b"outstanding_balance,Current outstanding balance,text")
        item_id = _upload_dataset(_name(), dict_csv)
        service.profile_item(item_id)
        summary = service.ingest_summary(item_id)
        conflicts = [w for w in summary["warnings"] if w["code"] == "type_conflict"
                    and w["column"] == "outstanding_balance"]
        self.assertTrue(conflicts)
        self.assertEqual(summary["status"], "ready")

    def test_unsupported_value(self):
        # "widget" resolves to no recognized declared_type token.
        dict_csv = CLEAN_DICTIONARY_CSV.replace(b"facility_id,Unique facility identifier,string",
                                                 b"facility_id,Unique facility identifier,widget")
        item_id = _upload_dataset(_name(), dict_csv)
        service.profile_item(item_id)
        summary = service.ingest_summary(item_id)
        unsupported = [w for w in summary["warnings"] if w["code"] == "unsupported_value"
                       and w["column"] == "facility_id"]
        self.assertTrue(unsupported)
        self.assertEqual(summary["status"], "ready")

    def test_hard_fail_is_reserved_for_structural_corruption_only(self):
        """None of the four warning codes ever produce ingest_status='failed'
        — only the duplicate-dictionary / empty-table structural cases do."""
        dict_csv = (CLEAN_DICTIONARY_CSV
                   + b"credit_score,Applicant credit score,integer\n").replace(
            b"outstanding_balance,Current outstanding balance,decimal",
            b"outstanding_balance,Current outstanding balance,widget")
        item_id = _upload_dataset(_name(), dict_csv)
        service.profile_item(item_id)
        summary = service.ingest_summary(item_id)
        codes = {w["code"] for w in summary["warnings"]}
        self.assertTrue({"dict_var_not_in_dataset", "unsupported_value"} & codes)
        self.assertEqual(summary["status"], "ready")
        self.assertIsNone(summary["fail_reason"])


# ── 4-T5 — dictionary states: absent / thin (precise fixture) / yes ─────────
class DictionaryStateTests(unittest.TestCase):
    def test_no_dictionary_is_absent_and_columns_are_provisional(self):
        item_id = _upload_dataset(_name())  # no dictionary file at all
        service.profile_item(item_id)
        summary = service.ingest_summary(item_id)
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(service.require_item(item_id).get("dictionary_state"), "absent")
        inv = service.get_inventory(item_id)
        self.assertTrue(inv)
        self.assertTrue(all(row["provisional"] for row in inv),
                        "every column is provisional when there is no dictionary at all")

    def test_step3_review_confirms_an_inferred_definition(self):
        item_id = _upload_dataset(_name())
        service.profile_item(item_id)
        row = service.get_inventory(item_id)[0]
        self.assertTrue(row["provisional"])

        saved = service.put_inventory(item_id, [row])

        reviewed = next(item for item in saved
                        if item["table_name"] == row["table_name"]
                        and item["column_name"] == row["column_name"])
        self.assertFalse(reviewed["provisional"])

    def test_dictionary_naming_every_column_but_unresolved_types_is_thin(self):
        """Exact rule under test (ingest/dictionary_state.py): a dictionary
        that declares (by exact name) EVERY dataset column, but whose
        declared_type resolves to a concrete classification for FEWER than
        70% of them, is 'thin' — the looked-complete-but-thin downgrade
        (ING-05) — even though every single column has a dictionary row."""
        dict_csv = (
            b"column,definition,declared_type\n"
            b"facility_id,Unique facility identifier,\n"          # blank type: unresolved
            b"default_flag,Observed default outcome,integer\n"     # resolved
            b"pd,Probability of default,\n"                        # blank type: unresolved
            b"origination_date,Facility origination date,\n"       # blank type: unresolved
            b"maturity_date,Facility maturity date,\n"              # blank type: unresolved
            b"outstanding_balance,Current outstanding balance,decimal\n"  # resolved
            b"loan_limit,Approved loan limit,\n"                    # blank type: unresolved
        )
        item_id = _upload_dataset(_name(), dict_csv)
        service.profile_item(item_id)
        inv = service.get_inventory(item_id)
        self.assertEqual(len(inv), 7, "every column has a dictionary row by name")
        # 2 of 7 resolved = ~29% < the 70% floor -> thin.
        self.assertEqual(service.require_item(item_id).get("dictionary_state"), "thin")

    def test_dictionary_covering_most_columns_with_resolved_types_is_yes(self):
        item_id = _upload_dataset(_name(), CLEAN_DICTIONARY_CSV)
        service.profile_item(item_id)
        self.assertEqual(service.require_item(item_id).get("dictionary_state"), "yes")

    def test_compute_function_directly_matches_the_documented_floor(self):
        self.assertEqual(dictionary_state.compute(has_dictionary=False, total_columns=5, declared_covered=0),
                         "absent")
        self.assertEqual(dictionary_state.compute(has_dictionary=True, total_columns=10, declared_covered=6),
                         "thin")
        self.assertEqual(dictionary_state.compute(has_dictionary=True, total_columns=10, declared_covered=7),
                         "yes")


# ── 4-T6 — persisted record shapes are a CONTRACT (Phase 6 depends on these
#    exact field names) ──────────────────────────────────────────────────────
class PersistedRecordShapeTests(unittest.TestCase):
    """What Phase 6's Test Lab manifest will read, per
    docs/0.4.0/06-ingestion-contract.md §6. Field NAMES are asserted
    explicitly, not just "truthy" — a silent rename here is exactly what
    this test exists to catch."""

    def test_mapping_record_shape(self):
        item_id = _upload_dataset(_name(), CLEAN_DICTIONARY_CSV)
        service.profile_item(item_id)
        recs = service.ingest_summary(item_id)["mapping"]
        self.assertTrue(recs)
        required = {"source_column", "canonical_field", "tier", "score", "confirmed_by", "dtype"}
        for rec in recs:
            self.assertTrue(required.issubset(rec.keys()), rec)
        facility = next(r for r in recs if r["canonical_field"] == "facility_id")
        self.assertEqual(facility, {
            "source_column": "facility_id", "canonical_field": "facility_id",
            "tier": "high", "score": 1.0, "confirmed_by": "auto", "dtype": facility["dtype"],
            "table_name": facility["table_name"], "status": "applied",
            "definition": "Unique facility identifier", "declared_type": "string",
            "role": "", "missing_value_codes": [], "business_context": "",
        })

    def test_warning_record_shape(self):
        dict_csv = CLEAN_DICTIONARY_CSV + b"credit_score,Applicant credit score,integer\n"
        item_id = _upload_dataset(_name(), dict_csv)
        service.profile_item(item_id)
        warns = service.ingest_summary(item_id)["warnings"]
        self.assertTrue(warns)
        for w in warns:
            self.assertTrue({"column", "code", "message"}.issubset(w.keys()), w)

    def test_dictionary_state_and_provisional_shape(self):
        item_id = _upload_dataset(_name(), CLEAN_DICTIONARY_CSV)
        service.profile_item(item_id)
        self.assertIn(service.require_item(item_id).get("dictionary_state"), ("yes", "thin", "absent"))
        inv = service.get_inventory(item_id)
        for row in inv:
            self.assertIn("provisional", row)
            self.assertIsInstance(row["provisional"], bool)

    def test_profile_snapshot_shape(self):
        item_id = _upload_dataset(_name(), CLEAN_DICTIONARY_CSV)
        service.profile_item(item_id)
        inv = service.get_inventory(item_id)
        row = next(r for r in inv if r["column_name"] == "outstanding_balance")
        profile = row["profile_json"]
        for key in ("dtype", "cardinality", "null_share", "patterns"):
            self.assertIn(key, profile)


class ConfirmedSpecialValueProfileTests(unittest.TestCase):
    def test_exact_core_profile_excludes_confirmed_special_values(self):
        profile = service._column_profile(
            pd.Series([1.0, 2.0, 3.0, -999.0, np.nan]), ["-999"], True)

        self.assertEqual(profile["profile_basis"], "confirmed_regular_values")
        self.assertEqual(profile["special_value_counts"], {"-999": 1})
        self.assertEqual(profile["special_value_row_count"], 1)
        self.assertEqual(profile["regular_value_count"], 3)
        self.assertEqual(profile["effective_missing_count"], 2)
        self.assertEqual(profile["raw_distinct_count"], 4)
        self.assertEqual(profile["cardinality"], 3)
        self.assertAlmostEqual(profile["mean"], 2.0)
        self.assertAlmostEqual(profile["variance"], 2 / 3)
        self.assertAlmostEqual(profile["median"], 2.0)
        self.assertAlmostEqual(profile["iqr"], 1.0)
        self.assertAlmostEqual(profile["mad"], 1.0)
        self.assertEqual(sum(row["count"] for row in profile["histogram"]), 3)
        self.assertGreater(profile["min"], -999)

    def test_multiple_confirmed_special_values_are_counted_and_excluded_independently(self):
        profile = service._column_profile(
            pd.Series([1.0, 2.0, -999.0, -998.0, np.nan]), ["-999", "-998"], True)

        self.assertEqual(profile["special_value_counts"], {"-999": 1, "-998": 1})
        self.assertEqual(profile["special_value_row_count"], 2)
        self.assertEqual(profile["regular_value_count"], 2)
        self.assertAlmostEqual(profile["mean"], 1.5)

    def test_unconfirmed_codes_leave_the_upload_profile_provisional(self):
        profile = service._column_profile(pd.Series([1.0, 2.0, -999.0]), ["-999"], False)

        self.assertEqual(profile["profile_basis"], "provisional_all_non_null")
        self.assertEqual(profile["special_value_row_count"], 0)
        self.assertEqual(profile["min"], -999.0)

    def test_sqlite_json_string_is_decoded_instead_of_profiled_character_by_character(self):
        profile = service._column_profile(
            pd.Series([1.0, 9.0, -999.0]), json.dumps(["-999"]), True)

        self.assertEqual(profile["declared_special_values"], ["-999"])
        self.assertEqual(profile["normalized_special_values"], ["-999"])
        self.assertEqual(profile["special_value_counts"], {"-999": 1})
        self.assertEqual(profile["regular_value_count"], 2)
        self.assertAlmostEqual(profile["mean"], 5.0)

    def test_descriptive_dictionary_text_is_not_guessed_as_an_atomic_code(self):
        profile = service._column_profile(
            pd.Series([1.0, -999.0]), ["-999 = source missing"], True)

        self.assertEqual(profile["special_value_counts"], {"-999 = source missing": 0})
        self.assertEqual(profile["unmatched_special_values"], ["-999 = source missing"])
        self.assertEqual(profile["min"], -999.0)

    def test_declared_numeric_strings_share_one_exact_numeric_aggregation(self):
        profile = service._column_profile(
            pd.Series(["1", "2", "bad", "-999"]), ["-999"], True, "numerical")

        self.assertEqual(profile["regular_value_count"], 3)
        self.assertEqual(profile["numeric_value_count"], 2)
        self.assertEqual(profile["numeric_parse_failure_count"], 1)
        self.assertAlmostEqual(profile["mean"], 1.5)
        self.assertEqual(sum(row["count"] for row in profile["histogram"]), 2)


class DictionaryIntegrationTests(unittest.TestCase):
    def test_confirmation_reprofiles_regular_population_before_ready(self):
        dictionary = CLEAN_DICTIONARY_CSV.replace(
            b"column,definition,declared_type\n",
            b"column,definition,declared_type,missing_value_codes\n",
        ).replace(
            b"outstanding_balance,Current outstanding balance,decimal\n",
            b"outstanding_balance,Current outstanding balance,decimal,100000\n",
        )
        item_id = _upload_dataset(_name(), dictionary)
        service.profile_item(item_id)
        before = next(row for row in service.get_inventory(item_id)
                      if row["column_name"] == "outstanding_balance")

        self.assertEqual(service.ingest_summary(item_id)["status"], "needs_review")
        self.assertEqual(before["profile_json"]["profile_basis"], "provisional_all_non_null")
        self.assertEqual(before["profile_json"]["min"], 80000)

        before["missing_codes_confirmed"] = True
        service.put_inventory(item_id, [before], table=before["table_name"])
        after = next(row for row in service.get_inventory(item_id)
                     if row["column_name"] == "outstanding_balance")

        self.assertEqual(service.ingest_summary(item_id)["status"], "ready")
        self.assertEqual(after["profile_json"]["profile_basis"], "confirmed_regular_values")
        self.assertEqual(list(after["profile_json"]["special_value_counts"].values()), [1])
        self.assertEqual(after["profile_json"]["regular_value_count"], 3)
        self.assertNotEqual(after["profile_json"]["mean"], before["profile_json"]["mean"])

    def test_confirmation_normalizes_raw_sqlite_json_fallback(self):
        dictionary = CLEAN_DICTIONARY_CSV.replace(
            b"column,definition,declared_type\n",
            b"column,definition,declared_type,missing_value_codes\n",
        ).replace(
            b"outstanding_balance,Current outstanding balance,decimal\n",
            b"outstanding_balance,Current outstanding balance,decimal,100000\n",
        )
        item_id = _upload_dataset(_name(), dictionary)
        service.profile_item(item_id)
        raw = s.execute(
            "SELECT missing_value_codes_json FROM variable_inventory "
            "WHERE item_id=? AND table_name=? AND column_name=?",
            [item_id, "assessment", "outstanding_balance"],
        )[0]["missing_value_codes_json"]
        self.assertIsInstance(raw, str)

        service.put_inventory(item_id, [{
            "table_name": "assessment", "column_name": "outstanding_balance",
            "missing_codes_confirmed": True,
        }])
        saved = next(row for row in service.get_inventory(item_id)
                     if row["column_name"] == "outstanding_balance")

        self.assertEqual(saved["missing_value_codes_json"], ["100000.0"])
        self.assertEqual(saved["profile_json"]["special_value_counts"], {"100000": 1})
        self.assertEqual(saved["profile_json"]["regular_value_count"], 3)

    def test_confirmation_rejects_descriptive_special_value_annotations(self):
        item_id = _upload_dataset(_name(), CLEAN_DICTIONARY_CSV)
        service.profile_item(item_id)

        with self.assertRaisesRegex(ValueError, "atomic source codes"):
            service.put_inventory(item_id, [{
                "table_name": "assessment", "column_name": "outstanding_balance",
                "missing_value_codes_json": ["-999 = source missing"],
                "missing_codes_confirmed": True,
            }])

    def test_confirmation_rejects_nonnumeric_codes_for_numeric_columns(self):
        item_id = _upload_dataset(_name(), CLEAN_DICTIONARY_CSV)
        service.profile_item(item_id)

        with self.assertRaisesRegex(ValueError, "Numeric column"):
            service.put_inventory(item_id, [{
                "table_name": "assessment", "column_name": "outstanding_balance",
                "classification": "numerical",
                "missing_value_codes_json": ["UNKNOWN"],
                "missing_codes_confirmed": True,
            }])

    def test_confirmation_accepts_string_codes_for_categorical_columns(self):
        item_id = _upload_dataset(_name(), CLEAN_DICTIONARY_CSV)
        service.profile_item(item_id)

        service.put_inventory(item_id, [{
            "table_name": "assessment", "column_name": "facility_id",
            "classification": "categorical",
            "missing_value_codes_json": ["UNKNOWN"],
            "missing_codes_confirmed": True,
        }])
        saved = next(row for row in service.get_inventory(item_id)
                     if row["column_name"] == "facility_id")
        self.assertEqual(saved["missing_value_codes_json"], ["UNKNOWN"])
        self.assertTrue(saved["missing_codes_confirmed"])

    def test_guided_headers_roles_codes_and_asset_version_are_persisted(self):
        dictionary = (
            b"Variable,Meaning,Format,Requirement,Missing Codes,Notes\n"
            b"facility_id,Facility key,string,identifier,-999,Source system key\n"
            b"pd,Probability of default,numeric,mandatory,-1,Monthly feed\n"
        )
        item_id = _upload_dataset(_name(), dictionary)
        service.profile_item(item_id)
        summary = service.ingest_summary(item_id)
        self.assertEqual(summary["dictionary_inspection"]["active_mapping"]["column_name"],
                         "Variable")
        self.assertIsNotNone(summary["dictionary_version"])
        item = service.require_item(item_id)
        version = s.query_one("dq_asset_dictionaries",
                              dictionary_version_id=item["dictionary_version_id"])
        self.assertEqual(version["asset_id"], item["dataset_family_id"])
        pd_row = next(row for row in service.get_inventory(item_id)
                      if row["column_name"] == "pd")
        self.assertEqual(pd_row["dictionary_role"], "mandatory")
        self.assertEqual(pd_row["missing_value_codes"], ["-1"])
        self.assertFalse(pd_row["missing_codes_confirmed"])

        pd_row["missing_codes_confirmed"] = True
        service.put_inventory(item_id, [pd_row], table=pd_row["table_name"])
        saved = next(row for row in service.get_inventory(item_id)
                     if row["column_name"] == "pd")
        self.assertTrue(saved["missing_codes_confirmed"])

    def test_fuzzy_column_metadata_is_not_applied_until_accepted(self):
        dictionary = (
            b"column,definition,declared_type\n"
            b"origination_dt,Reviewed origination date,date\n"
        )
        item_id = _upload_dataset(_name(), dictionary)
        service.profile_item(item_id)
        before = next(row for row in service.get_inventory(item_id)
                      if row["column_name"] == "origination_date")
        self.assertEqual(before["description"], "")
        suggestion = next(row for row in service.ingest_summary(item_id)["mapping"]
                          if row["canonical_field"] == "origination_dt")
        self.assertEqual(suggestion["status"], "confirm_suggestion")

        before["mapping_confirmed"] = True
        service.put_inventory(item_id, [before], table=before["table_name"])
        after = next(row for row in service.get_inventory(item_id)
                     if row["column_name"] == "origination_date")
        self.assertEqual(after["description"], "Reviewed origination date")
        self.assertEqual(after["classification"], "datetime")


# ── 0.5.0 Step 3b (C-40/D-25/AST-05/06/08/10) — a re-upload is a SNAPSHOT of
#    the SAME ASSET, never a sibling item with its own name and its own
#    results. This class replaces 4-T7's old "a new delivery, never a
#    mutation" test (rule 7 of the 0.5.0 plan §1: rewritten in the SAME
#    commit as the cause, never simply deleted). What survives from 4-T7:
#    the old snapshot's own row is still byte-identical before/after, and
#    the family ordering is still exactly [old, new] by delivery_seq — that
#    part of 0.4.0's guarantee was correct and still holds. What is
#    REPLACED: the old assertion `new_row["name"] == name` encoded the
#    0.4.0 defect this step corrects (Data_Sourcing_3) — a re-upload used
#    to get its OWN literal name, which is exactly what let a dependent
#    screen keep showing "the old dataset's name and figures". The new
#    assertions instead prove the asset identity stays completely stable:
#    both snapshots share the SAME dataset_family_id, the SAME display_name
#    (system-ID-prefixed, not the raw literal passed to create_item), and
#    the SAME version_no (AST-06 — add_period never bumps it).
class ReuploadDeliveryTests(unittest.TestCase):
    def test_reupload_adds_a_snapshot_of_the_same_asset_without_mutating_the_old_one(self):
        name = _name()
        old_id = _upload_dataset(name, CLEAN_DICTIONARY_CSV)
        service.profile_item(old_id)
        row_before = dict(s.query_one("dq_items", item_id=old_id))

        result = service.reupload_item(old_id, as_of_date="2026-07-30")
        new_id = result["item_id"]
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(result["delivery_seq"], 2)

        row_after = dict(s.query_one("dq_items", item_id=old_id))
        self.assertEqual(row_before, row_after, "the old snapshot's own row is byte-identical before/after")

        members = delivery.family_deliveries(row_after["dataset_family_id"])
        self.assertEqual([m["item_id"] for m in members], [old_id, new_id])

        new_row = s.query_one("dq_items", item_id=new_id)
        self.assertEqual(new_row["ingest_status"], "uploading")

        # C-40/D-25 — the asset identity stays stable: SAME family, SAME
        # display name (system-ID-prefixed, replacing the OLD literal-name
        # assertion this test used to make), SAME version, both snapshots
        # simultaneously active (add_period supersedes nothing — AST-08).
        self.assertEqual(new_row["dataset_family_id"], row_after["dataset_family_id"])
        self.assertEqual(new_row["name"], row_after["name"])
        self.assertNotEqual(new_row["name"], name,
                           "the stored name is the asset's system-ID-prefixed display name, "
                           "never the raw literal passed to create_item (AST-02/AST-03)")
        self.assertEqual(new_row["version_no"], row_after["version_no"])
        self.assertEqual(new_row["intent"], "add_period")
        self.assertEqual(new_row["snapshot_status"], "active")
        self.assertEqual(row_after["snapshot_status"], "active",
                         "add_period leaves the prior snapshot active too — nothing superseded")

    def test_full_replacement_supersedes_the_old_snapshot_and_bumps_the_version(self):
        """The NEW capability 0.4.0 never had at all: 'full replacement'
        supersedes the whole prior set and bumps the reference-schema
        generation (AST-06/AST-08), reachable through the SAME reupload_item
        entry point via an explicit intent."""
        old_id = _upload_dataset(_name(), CLEAN_DICTIONARY_CSV)
        service.profile_item(old_id)
        asset_id = s.query_one("dq_items", item_id=old_id)["dataset_family_id"]
        asset_before = s.query_one("dq_assets", asset_id=asset_id)
        self.assertEqual(asset_before["current_version_no"], 1)

        result = service.reupload_item(old_id, intent="full_replacement")

        self.assertEqual(s.query_one("dq_items", item_id=old_id)["snapshot_status"], "superseded")
        self.assertEqual(result["snapshot_status"], "active")
        self.assertEqual(result["version_no"], 2)
        asset_after = s.query_one("dq_assets", asset_id=asset_id)
        self.assertEqual(asset_after["current_version_no"], 2)


# ── 4-T8 — source-inspection: no schema/domain literal from the deleted
#    TYPE_PRIORITY table survives in the code this phase owns ───────────────
class BinaryClassificationTests(unittest.TestCase):
    def test_two_named_segments_are_categorical_without_a_parse_warning(self):
        values = pd.Series(["Retail", "Commercial", "Retail", "Commercial"])

        observed = ingest_classify.classify(values)

        self.assertEqual(observed, "categorical")
        self.assertIsNone(ingest_warnings.column_parse_failure_warning(
            "segment", values, observed))

    def test_boolean_text_and_zero_one_remain_binary(self):
        self.assertEqual(ingest_classify.classify(pd.Series(["Yes", "No", "Yes"])), "binary")
        self.assertEqual(ingest_classify.classify(pd.Series([0, 1, 0, 1])), "binary")


class SourceInspectionTests(unittest.TestCase):
    """TYPE_PRIORITY itself (ai/v2/service.py:40-48 pre-Phase-4) is gone —
    checked at whole-file scope for the fragment that is UNIQUELY tied to it
    ("bad_flag" appears nowhere else in this codebase, pre- or post-Phase-4).

    The full original fragment set (including "dscr"/"cltv"/"noi", which
    ALSO appear in ``RULE_KEYWORDS`` — pre-existing, out-of-scope dead code
    in the retiring AI-recommender path, explicitly not this phase's
    territory per the orchestrator's file assignment) is checked against
    ONLY the ingestion functions Phase 4 owns (via inspect.getsource) plus
    the entire backend/ingest/ package, which has zero legacy baggage.
    """

    # Deliberately excludes generic vocabulary the NEW code legitimately
    # returns/uses as classification values or dict keys (e.g. "target",
    # "identifier", "status", "score", "flag") — those words are not schema
    # literals on their own; the regression this guards against is a column-
    # NAME-fragment hint table like the deleted TYPE_PRIORITY, so every
    # fragment kept here was verified to have zero legitimate use in the
    # new ingestion code (see the smoke check this test file's author ran).
    # ``stage`` is now a legitimate progress-contract field; the guard still
    # rejects the prior schema-specific fragments rather than generic workflow
    # vocabulary.
    OLD_FRAGMENTS = ("dscr", "cltv", "noi", "default_flag", "bad_flag",
                     "rating", "grade", "amount", "balance",
                     "ratio", "value", "debt", "segment", "pd",
                     "region", "location", "indicator", "timestamp",
                     "_id", " id", "_dt", "is_", "period")

    def test_bad_flag_appears_nowhere_in_service_py(self):
        text = (_BACKEND_ROOT / "ai" / "v2" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("bad_flag", text)

    def test_type_priority_is_not_defined_anywhere(self):
        for path in [(_BACKEND_ROOT / "ai" / "v2" / "service.py"),
                     *(_BACKEND_ROOT / "ingest").glob("*.py")]:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"^TYPE_PRIORITY\s*=", text, re.MULTILINE), path)

    def test_ingestion_owned_functions_carry_no_schema_literal(self):
        from ai.v2 import service
        owned = [
            service._classify, service._column_profile, service.profile_item,
            service.save_file, service.create_item, service.reupload_item,
            service.finalize_item, service.ingest_summary, service._dict_entry,
            service._dictionary_rows_for_table, service._set_dict_entry,
            service._parse_dictionary, service._parse_json_dictionary,
            service.get_inventory, service.put_inventory,
        ]
        blob = "\n".join(inspect.getsource(fn) for fn in owned)
        for fragment in self.OLD_FRAGMENTS:
            self.assertNotIn(f'"{fragment}"', blob, f"schema literal '{fragment}' leaked into ingestion code")
            self.assertNotIn(f"'{fragment}'", blob, f"schema literal '{fragment}' leaked into ingestion code")

    def test_ingest_package_carries_no_schema_literal(self):
        import ingest.classify
        import ingest.dictionary_state
        import ingest.mapping
        import ingest.records
        import ingest.status
        import ingest.warnings
        modules = [ingest.classify, ingest.dictionary_state, ingest.mapping,
                  ingest.records, ingest.status, ingest.warnings]
        blob = "\n".join(inspect.getsource(m) for m in modules)
        for fragment in self.OLD_FRAGMENTS:
            self.assertNotIn(f'"{fragment}"', blob, f"schema literal '{fragment}' leaked into ingest/{fragment}")
            self.assertNotIn(f"'{fragment}'", blob, f"schema literal '{fragment}' leaked into ingest/{fragment}")


# ── 4-T9 — migration: any pre-Phase-4 dq_items row lands on a valid new
#    status; running the migration twice is a no-op the second time ────────
class MigrationBackfillTests(SetupMixin, unittest.TestCase):
    def _legacy_item(self, status: str, with_data_file: bool, with_inventory: bool,
                     with_dict_file: bool = False) -> str:
        item_id = f"legacy_{uuid.uuid4().hex[:10]}"
        now = s.now_ist()
        s.insert("dq_items", {
            "item_id": item_id, "kind": "dataset", "name": item_id, "status": status,
            "module_tag": "", "target_variable": "", "use_case": "",
            "created_at": now, "updated_at": now,
        })  # ingest_status/dictionary_state deliberately absent — pre-Phase-4 shape
        if with_data_file:
            s.insert("dq_item_files", {"file_id": f"f_{uuid.uuid4().hex[:8]}", "item_id": item_id,
                                       "role": "data", "filename": "x.csv", "path": "x.csv",
                                       "completed_at": now})
        if with_dict_file:
            s.insert("dq_item_files", {"file_id": f"f_{uuid.uuid4().hex[:8]}", "item_id": item_id,
                                       "role": "dictionary", "filename": "d.csv", "path": "d.csv",
                                       "completed_at": now})
        if with_inventory:
            s.insert("variable_inventory", {
                "item_id": item_id, "table_name": "t1", "column_name": "amount",
                "classification": "numerical", "data_type": "float64",
                "description": "An amount" if with_dict_file else "",
                "discrepancies": [], "notes": "", "role": "Feature", "profile_json": {},
                "updated_at": now,
            })
        return item_id

    def test_previously_profiled_item_migrates_to_ready(self):
        item_id = self._legacy_item("profiled", with_data_file=True, with_inventory=True, with_dict_file=True)
        s.init_schema()
        row = s.query_one("dq_items", item_id=item_id)
        self.assertEqual(row["ingest_status"], "ready")
        self.assertIn(row["dictionary_state"], ("yes", "thin"))
        mapped = s.query("dq_item_mappings", item_id=item_id)
        self.assertTrue(mapped, "a best-effort mapping record is synthesized from the existing inventory")

    def test_requires_reupload_item_migrates_to_failed(self):
        item_id = self._legacy_item("requires_reupload", with_data_file=False, with_inventory=False)
        s.init_schema()
        row = s.query_one("dq_items", item_id=item_id)
        self.assertEqual(row["ingest_status"], "failed")
        self.assertTrue(row["ingest_fail_reason"])

    def test_never_uploaded_item_migrates_to_uploading(self):
        item_id = self._legacy_item("sourcing", with_data_file=False, with_inventory=False)
        s.init_schema()
        row = s.query_one("dq_items", item_id=item_id)
        self.assertEqual(row["ingest_status"], "uploading")

    def test_migration_is_idempotent_run_twice(self):
        item_id = self._legacy_item("profiled", with_data_file=True, with_inventory=True, with_dict_file=True)
        s.init_schema()
        first = dict(s.query_one("dq_items", item_id=item_id))
        first_mappings = s.query("dq_item_mappings", item_id=item_id)
        s.init_schema()
        s.init_schema()
        second = dict(s.query_one("dq_items", item_id=item_id))
        second_mappings = s.query("dq_item_mappings", item_id=item_id)
        self.assertEqual(first, second)
        self.assertEqual(len(first_mappings), len(second_mappings))


if __name__ == "__main__":
    unittest.main()
