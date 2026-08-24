"""ADM-01..05 (0.5.0 plan Step 2) — reset results are reported in business
language, never in table names.

This is the ADM-04 build-time "fail loudly" gate: it enumerates every key the
two factory-reset paths (``system_db.reset_demo()`` / ``system_db.wipe_all_items()``)
can actually put into their ``deleted``/``reseeded`` dicts — read live off
``system_db._WORKPRODUCT_TABLES`` and ``system_db._rca_tables()`` where those
exist as importable symbols, hand-listed against the source for the inline
literals in ``reset_demo``/``wipe_all_items`` where no such symbol exists
(system_db.py:1256-1371 — this step is forbidden from adding one, see the
plan's Permitted paths for Step 2) — and asserts every one of them resolves to
a human label via the single shared map, ``ui/src/lib/resetLabels.json``.
That JSON file is read directly by both this test and
``ui/src/lib/resetLabels.js`` (the UI helper ``summarizeReset``), so there is
exactly one place the table -> label mapping is written; nothing here
re-implements or duplicates the map's *data* (only its small lookup/grouping
*algorithm* is necessarily ported — see below).

The map has two parts: ``exact`` (table/key -> {label, group}) and
``prefixes`` (currently just ``rca_`` -> {label, group}), because
``system_db._rca_tables()`` deliberately discovers every ``rca_*`` table LIVE
off ``sqlite_master`` rather than a hardcoded list — confirmed live while
building this test: running the full suite together (a shared throwaway DB
across files, per test_admin_reset.py's own docstring) surfaced
``rca_cases_legacy_v1``/``rca_hypotheses_legacy_v1`` rows left behind by
another test module's migration fixtures, neither of which is one of the 19
canonical ``rca_*`` tables. A flat enumeration of exact names would have
failed on exactly the case ADM-04 exists to catch; the ``rca_`` prefix rule
mirrors the backend's own "never hardcode this family" design instead.

A table/key that resolves to NEITHER an exact entry nor a prefix rule must
fail ``test_every_real_reset_key_has_a_human_label`` — not leak its raw name
into the UI. ``test_bites_when_an_unlabeled_key_is_added`` is the permanent
proof that this check actually rejects such a case (plan operating rule 6).

Standalone-runnable (``python -m unittest tests.test_reset_language``),
mirroring test_taxonomy.py's/test_admin_reset.py's SYSTEM_DB_PATH-at-import
sandboxing convention.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-reset-language.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LABELS_PATH = _REPO_ROOT / "ui" / "src" / "lib" / "resetLabels.json"

# --------------------------------------------------------------------------
# The real key set — read live where system_db.py exposes a symbol for it,
# hand-listed (against the current source, verified) where it only ever
# appears as an inline literal inside reset_demo()/wipe_all_items(). This
# step may not modify system_db.py (Permitted paths), so a literal list is
# the only way to check those from outside — that is also exactly the
# situation ADM-04 exists to make safe for the genuinely-dynamic case
# (`_rca_tables()`), which IS covered here live, not hand-listed.
# --------------------------------------------------------------------------


def _real_reset_keys() -> set[str]:
    """Every key ``reset_demo()``/``wipe_all_items()`` can produce in their
    ``deleted``/``reseeded`` dicts (system_db.py:1241-1371)."""
    s.init_schema()
    keys: set[str] = set()

    # _clear_item_pipeline() — shared by both reset grades (system_db.py:590-601, :1222-1238).
    keys.update(s._WORKPRODUCT_TABLES)
    keys.update({"dq_assets", "dq_asset_versions", "dq_asset_dictionaries",
                 "dq_asset_events", "id_sequences"})
    with s.get_conn() as conn:
        keys.update(s._rca_tables(conn))  # live off sqlite_master, deliberately not hardcoded
    keys.add("tag_assignments")

    # reset_demo() — surgical grade's own keys (system_db.py:1256-1288).
    keys.update({"ingested_databases", "table_metadata", "object_contexts",
                 "context_links", "app_fsm"})
    # reset_demo()'s reseed_static() (seeds/__init__.py:193-201).
    keys.update({"monitoring", "context_memory"})

    # wipe_all_items() — full-wipe grade's extra static lists
    # (system_db.py:1340-1353).
    keys.update({
        "kb_retrieval_manifests", "kb_rules", "kb_sections",
        "kb_document_versions", "kb_documents",
        "test_library", "agent_skills", "dq_framework_areas",
        "dq_framework_families", "fw_areas", "fw_tests", "fw_family_weights",
        "diagnostic_register", "framework_taxonomy", "framework_test_areas",
        "threshold_settings",
        "tenants", "tag_dimensions", "tag_values", "tag_aliases",
        "tag_taxonomy_versions",
    })
    # wipe_all_items()'s own reseed dict (system_db.py:1365-1370).
    keys.update({"agent_skills", "dq_framework", "framework_register",
                 "platform_and_taxonomy"})
    return keys


def _load_labels() -> dict:
    data = json.loads(_LABELS_PATH.read_text(encoding="utf-8"))
    data.setdefault("exact", {})
    data.setdefault("prefixes", {})
    data.setdefault("order", {"yourWork": [], "platform": []})
    return data


def _resolve_key(key: str, labels: dict) -> dict | None:
    """Exact match first, then any `prefixes` rule (startswith) — a port of
    resetLabels.js's `resolveKey`. Returns the {label, group} dict, or None
    if this key has no entry anywhere in the map."""
    exact = labels["exact"].get(key)
    if exact:
        return exact
    for prefix, meta in labels["prefixes"].items():
        if key.startswith(prefix):
            return meta
    return None


# --------------------------------------------------------------------------
# A Python port of resetLabels.js's summarizeReset(), reading the SAME JSON,
# so this suite can assert composed-sentence behaviour without needing a JS
# runtime. The data (the label map) is single-sourced; only this small
# grouping/wording algorithm is necessarily duplicated across the language
# boundary — low-drift-risk, and ADM-04's actual safety net is the
# completeness assertion above, not this composition mirror.
# --------------------------------------------------------------------------

_PLATFORM_SENTENCE = (
    "Platform reference data (framework, taxonomy, agent skills and thresholds) "
    "was cleared and immediately restored — the product is ready to use."
)
_EMPTY_SENTENCE = "Nothing needed removing — there was no work data to clear."

_SINGULAR = {
    "assets": "asset",
    "connected databases": "connected database",
    "uploaded files": "uploaded file",
    "uploaded tables": "uploaded table",
    "variables and their profiles": "variable and its profile",
    "tags": "tag",
    "issues": "issue",
    "RCA cases": "RCA case",
    "knowledge-base documents": "knowledge-base document",
    "test results and findings": "test result or finding",
    "saved workflow context": "saved workflow context item",
    "scheduled tasks and alerts": "scheduled task or alert",
    "background activity records": "background activity record",
    "support requests": "support request",
    "agent skills": "agent skill",
    "framework and diagnostics reference data": "framework or diagnostics reference record",
    "thresholds and settings": "threshold or setting",
    "tenancy configuration": "tenancy configuration record",
    "taxonomy reference data": "taxonomy reference record",
    "other platform records": "other platform record",
}


def _noun(label: str, count: int) -> str:
    if count == 1:
        return _SINGULAR.get(label, label[:-1] if label.endswith("s") else label)
    return label


def _join_with_and(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _group_rows(counts: dict, labels: dict) -> tuple[list[dict], list[dict]]:
    sums: dict[str, dict] = {}
    for key, value in counts.items():
        if not value:
            continue
        meta = _resolve_key(key, labels)
        if meta is None:
            meta = {"label": "other platform records", "group": "platform"}
        label = meta["label"]
        if label not in sums:
            sums[label] = {"label": label, "group": meta["group"], "count": 0}
        sums[label]["count"] += value

    def ordered(group: str, order_list: list[str]) -> list[dict]:
        seen = set()
        rows = []
        for label in order_list:
            row = sums.get(label)
            if row and row["group"] == group:
                rows.append({"label": row["label"], "count": row["count"]})
                seen.add(label)
        for row in sums.values():
            if row["group"] == group and row["label"] not in seen:
                rows.append({"label": row["label"], "count": row["count"]})
        return rows

    your_work = ordered("yourWork", labels["order"].get("yourWork", []))
    platform = ordered("platform", labels["order"].get("platform", []))
    return your_work, platform


def _summarize(counts: dict, labels: dict) -> dict:
    your_work, platform = _group_rows(counts, labels)
    your_work_phrase = (
        "Removed " + _join_with_and(
            [f'{r["count"]} {_noun(r["label"], r["count"])}' for r in your_work]
        ) + "."
        if your_work else ""
    )
    platform_phrase = _PLATFORM_SENTENCE if platform else ""
    if not your_work_phrase and not platform_phrase:
        sentence = _EMPTY_SENTENCE
    else:
        sentence = " ".join(p for p in (your_work_phrase, platform_phrase) if p)
    return {"yourWork": your_work, "platform": platform, "sentence": sentence}


class ResetLabelCompletenessTests(unittest.TestCase):
    """ADM-04's actual build-time gate: every key a live reset path can
    produce resolves (exact or prefix) to a human label. This is the check
    that must fail loudly the moment a table gains a count with no label —
    never a UI-only concern."""

    def test_every_real_reset_key_has_a_human_label(self):
        labels = _load_labels()
        missing = sorted(k for k in _real_reset_keys() if _resolve_key(k, labels) is None)
        self.assertEqual(
            missing, [],
            f"these reset keys have no human label (exact or prefix) in "
            f"ui/src/lib/resetLabels.json: {missing}",
        )

    def test_every_exact_entry_is_well_formed(self):
        labels = _load_labels()
        for key, meta in labels["exact"].items():
            self._assert_well_formed(key, meta)

    def test_every_prefix_entry_is_well_formed(self):
        labels = _load_labels()
        self.assertIn("rca_", labels["prefixes"], "the rca_ prefix rule must exist")
        for prefix, meta in labels["prefixes"].items():
            self._assert_well_formed(prefix, meta)

    def test_order_lists_only_reference_labels_that_exist(self):
        """A stale `order` entry (a label renamed/removed on one side but not
        the other) is a silent drift risk, not a hard failure at runtime
        (the algorithm tolerates it), but it must never go unnoticed here."""
        labels = _load_labels()
        all_labels = {m["label"] for m in labels["exact"].values()} | \
            {m["label"] for m in labels["prefixes"].values()}
        for group in ("yourWork", "platform"):
            for label in labels["order"].get(group, []):
                self.assertIn(label, all_labels, f"order.{group} names an unknown label: {label}")

    def _assert_well_formed(self, key: str, meta: dict) -> None:
        self.assertIsInstance(meta, dict, key)
        self.assertIn("label", meta, key)
        self.assertIsInstance(meta["label"], str, key)
        self.assertTrue(meta["label"].strip(), f"{key} has an empty label")
        self.assertIn(meta.get("group"), ("yourWork", "platform"), f"{key} has no valid group")
        # ADM-01: a label must never itself be (or contain) the raw
        # snake_case identifier it stands in for.
        self.assertNotIn("_", meta["label"], f"{key}'s label reads like a table name")

    def test_bites_when_an_unlabeled_key_is_added(self):
        """Invariant-bites proof (plan operating rule 6): if a table/key ever
        shows up in a reset path's counts with no entry (exact or prefix) in
        resetLabels.json, the completeness assertion above must reject it,
        not silently pass. Proven here with a key that is deliberately
        absent from the map, and also demonstrated live during
        implementation by temporarily removing a real entry from
        resetLabels.json and re-running this suite (see the Step 2 report)
        — this assertion is what keeps that demonstration permanent instead
        of a one-off manual check."""
        labels = _load_labels()
        keys = _real_reset_keys() | {"zz_future_unlabeled_table"}
        missing = sorted(k for k in keys if _resolve_key(k, labels) is None)
        self.assertEqual(missing, ["zz_future_unlabeled_table"])


class ResetSentenceCompositionTests(unittest.TestCase):
    """Behavioural coverage (plan operating rule 5) for ADM-01..03: business
    language, your-work-before-platform ordering, summed shared labels, a
    full sentence even when the result is all-zero, correct singular/plural
    for a count of exactly one."""

    def setUp(self):
        self.labels = _load_labels()

    def test_empty_result_reads_as_a_full_sentence(self):
        out = _summarize({}, self.labels)
        self.assertEqual(out["sentence"], _EMPTY_SENTENCE)
        self.assertTrue(out["sentence"].endswith("."))
        self.assertEqual(out["yourWork"], [])
        self.assertEqual(out["platform"], [])

    def test_all_zero_counts_reads_as_a_full_sentence_not_a_fragment(self):
        zeroed = {k: 0 for k in _real_reset_keys()}
        out = _summarize(zeroed, self.labels)
        self.assertEqual(out["sentence"], _EMPTY_SENTENCE)

    def test_your_work_reported_before_platform_no_identifiers_leak(self):
        counts = {
            "dq_items": 2, "dq_item_files": 7, "dq_item_tables": 2,
            "variable_inventory": 40, "dq_item_mappings": 8, "tag_assignments": 3,
            "agent_skills": 14, "tag_dimensions": 4, "object_contexts": 25,
            "kb_retrieval_manifests": 5, "rca_cases": 2, "rca_cases_legacy_v1": 1,
        }
        out = _summarize(counts, self.labels)
        sentence = out["sentence"]

        self.assertIn("Removed", sentence)
        self.assertIn("Platform reference data", sentence)
        # ADM-02: your-work phrase must precede the platform phrase.
        self.assertLess(sentence.index("Removed"), sentence.index("Platform reference data"))
        # ADM-01/M-2: no raw internal identifier (every real table/key in this
        # codebase is snake_case) survives into the composed message, and no
        # bare `key=value` pair does either.
        for name in list(_real_reset_keys()) + ["rca_cases_legacy_v1"]:
            if "_" in name:
                self.assertNotIn(name, sentence, f"raw identifier {name!r} leaked into: {sentence}")
        self.assertIsNone(re.search(r"[a-zA-Z_][a-zA-Z0-9_]*=\d+", sentence),
                          f"a table=count pair survived in: {sentence}")
        # rca_cases + rca_cases_legacy_v1 (the prefix-matched, non-canonical
        # table observed live in the shared test DB) sum into ONE "RCA cases"
        # row, not two.
        rca_rows = [r for r in out["yourWork"] if r["label"] == "RCA cases"]
        self.assertEqual(len(rca_rows), 1)
        self.assertEqual(rca_rows[0]["count"], 3)

    def test_platform_only_result_is_still_a_full_sentence(self):
        out = _summarize({"agent_skills": 14, "threshold_settings": 6}, self.labels)
        self.assertTrue(out["sentence"].startswith("Platform reference data"))
        self.assertTrue(out["sentence"].strip().endswith("."))
        self.assertEqual(out["yourWork"], [])

    def test_two_tables_sharing_a_label_sum_once_not_twice(self):
        out = _summarize({"variable_inventory": 5, "dq_item_mappings": 3,
                           "dq_item_warnings": 2}, self.labels)
        matches = [r for r in out["yourWork"] if r["label"] == "variables and their profiles"]
        self.assertEqual(len(matches), 1, "one concept must render as one row")
        self.assertEqual(matches[0]["count"], 10)
        self.assertEqual(out["sentence"].count("variables and their profiles"), 1)

    def test_count_of_exactly_one_is_grammatically_singular(self):
        out = _summarize({"dq_items": 1}, self.labels)
        self.assertIn("1 asset.", out["sentence"])
        self.assertNotIn("1 assets", out["sentence"])

    def test_unknown_key_never_leaks_its_raw_name(self):
        out = _summarize({"zz_future_unlabeled_table": 3}, self.labels)
        self.assertNotIn("zz_future_unlabeled_table", out["sentence"])
        # The composed sentence for the platform group is always the fixed,
        # generic wording (ADM-02) — it never itemises labels or counts — so
        # the "folded into a generic bucket, never its raw name" guarantee is
        # checked against the structured `platform` rows, not the sentence.
        self.assertTrue(
            any(r["label"] == "other platform records" and r["count"] == 3
                for r in out["platform"]),
            out["platform"],
        )

    def test_rca_legacy_or_ad_hoc_table_still_resolves_via_the_prefix_rule(self):
        """The exact scenario observed live: some other test module's
        migration fixtures leave rca_cases_legacy_v1 sitting in the shared
        throwaway DB. It must resolve via the `rca_` prefix rule, not be
        treated as an unlabelled key."""
        self.assertIsNotNone(_resolve_key("rca_cases_legacy_v1", self.labels))
        self.assertIsNotNone(_resolve_key("rca_anything_a_future_stage_adds", self.labels))
        out = _summarize({"rca_cases_legacy_v1": 4}, self.labels)
        self.assertEqual(out["yourWork"], [{"label": "RCA cases", "count": 4}])


if __name__ == "__main__":
    unittest.main()
