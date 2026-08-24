"""Phase 6 — the KB-rule -> primitive binder (KB-08/KB-09/KB-11, CFR-03).

Standalone-runnable (``python -m unittest tests.test_cross_field_binder``);
same SYSTEM_DB_PATH / KB_STORAGE_DIR-at-import-time sandboxing as
tests/test_kb_binding.py.

Everything here runs against what the REAL Phase-5 parser produces — either
S9 itself (``synthetic-kb/KB_cross_field_reference_2.pdf``, uploaded through
``kb.upload_document`` + ``submit_for_review``) or a small Markdown rule
table put through the same pipeline. No hand-built ``kb_rules`` dict is
used anywhere, so the binder is proven against real parser output rather
than a shape invented to suit it.

Covers: which phrasings bind and which honestly do not (KB-09), that a
parse hazard blocks binding outright (KB-11), that binding is by SIGNATURE
with no per-rule-id branch, and 6-T1's rule-count / use-case-subset split.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-cf-binder.db"
_TMP_KB = Path(tempfile.gettempdir()) / "archimedes-test-cf-binder-storage"
if _TMP_DB.exists():
    _TMP_DB.unlink()
if _TMP_KB.exists():
    shutil.rmtree(_TMP_KB)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["KB_STORAGE_DIR"] = str(_TMP_KB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import kb  # noqa: E402
import system_db as s  # noqa: E402
from dq_diagnostics.engines.cross_field import binder  # noqa: E402
from dq_diagnostics.readiness import normalize_framework, rule_in_scope  # noqa: E402
from seeds.taxonomy_seed import BOOTSTRAP_TENANT, seed_platform, seed_taxonomy  # noqa: E402

TENANT = BOOTSTRAP_TENANT
BACKEND_ROOT = Path(__file__).resolve().parent.parent
S9_PDF = (BACKEND_ROOT.parent / "synthetic-kb" / "KB_cross_field_reference_2.pdf").resolve()

# A rule table spanning ALL FIVE rule types with role names the PDF-extraction
# hazard detector does not flag (underscored, so nothing "collapses"). Put
# through the same upload -> submit_for_review pipeline as S9.
FIVE_TYPE_MD = """## Cross-field rule catalogue

IRB (7 rules)

| ID | Sev | Type | Entity | Roles (semantic) | Rule | Reg reference |
| --- | --- | --- | --- | --- | --- | --- |
| CF-01 | CRIT | Inequality | workout | exposure_at_default, drawn_balance | exposure_at_default >= drawn_balance (EAD cannot be below drawn balance) | CRR Art. 166 |
| CF-02 | CRIT | Date ordering | workout | possession_date, sale_date | possession_date <= sale_date | - |
| CF-03 | MATE | Identity/derivation | facility | original_ltv, original_balance, original_property_value | original_ltv == original_balance / original_property_value * 100 (+/- 0.5) | - |
| CF-04 | MINO | Domain/bound | workout | drawn_balance | drawn_balance >= 0 (logical bound) | - |
| CF-05 | MATE | Domain/bound | workout | workout_state | workout_state in {closed, open, written_off} | - |
| CF-06 | MATE | Domain/bound | facility | original_ltv | original_ltv in [0, 200] (logical bound) | - |
| CF-07 | MATE | Conditional | workout | workout_state, sale_date, cure_indicator | IF workout_state = closed THEN sale_date populated (exceptions: cure_indicator=1) | - |

IFRS9 (3 rules)

| ID | Sev | Type | Entity | Roles (semantic) | Rule | Reg reference |
| --- | --- | --- | --- | --- | --- | --- |
| CF-08 | CRIT | Conditional | snapshot | days_past_due, ifrs9_stage | IF days_past_due > 90 THEN ifrs9_stage = 3 (90+ DPD backstop) | IFRS 9 5.5.11 |
| CF-09 | MINO | Conditional | snapshot | ifrs9_stage, days_past_due | IF ifrs9_stage in {2,3} THEN days_past_due present | - |
| CF-10 | MATE | Inequality | workout | exposure_at_default, original_balance | exposure_at_default <= original_balance * 1.5 (EAD bounded vs original) | CRR Art. 166 |

CF-07 - Cured workouts close without a property sale; no sale_date expected.
"""

# A table whose roles ARE collapsed (>=15 letters, no separators) so Phase 5
# flags a parse hazard on them — the same text would otherwise bind cleanly.
HAZARD_MD = """## Hazardous rule table

| ID | Sev | Type | Entity | Roles (semantic) | Rule | Reg reference |
| --- | --- | --- | --- | --- | --- | --- |
| HZ-01 | CRIT | Inequality | workout | exposureatdefault, drawnbalance | exposureatdefault >= drawnbalance | - |
"""

# Phrasings the binder deliberately does NOT support — each must stay
# reference-only with a stated reason rather than be guessed into a binding.
UNSUPPORTED_MD = """## Unsupported phrasings

| ID | Sev | Type | Entity | Roles (semantic) | Rule | Reg reference |
| --- | --- | --- | --- | --- | --- | --- |
| UN-01 | MATE | Conditional | workout | workout_state, realised_lgd | IF workout closed/written_off THEN realised_lgd populated | - |
| UN-02 | MATE | Conditional | workout | cure_indicator, realised_lgd | IF cured THEN realised_lgd <= 0.05 | - |
| UN-03 | CRIT | Domain/bound | snapshot | ifrs9_stage | ifrs9_stage within its declared staging domain | - |
| UN-04 | MATE | Inequality | workout | drawn_balance, other_costs | drawn_balance is broadly consistent with other_costs | - |
"""


def _upload(name: str, markdown: str) -> str:
    up = kb.upload_document(TENANT, name, "text/markdown", markdown.encode("utf-8"), None, "tester")
    version_id = up["version"]["version_id"]
    kb.submit_for_review(TENANT, version_id, "tester", "domain_fact")
    return version_id


class FiveTypeBindingTests(unittest.TestCase):
    """The binder handles every one of the five rule types on real parser
    output, and records enough provenance to rebuild the predicate."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.version_id = _upload("five-types.md", FIVE_TYPE_MD)
        cls.report = binder.bind_version(TENANT, cls.version_id, "tester")
        cls.by_id = {r["source_rule_id"]: r
                     for r in s.query("kb_rules", version_id=cls.version_id)}

    def test_every_rule_binds_and_all_five_types_are_covered(self):
        self.assertEqual(self.report["counts"], {"considered": 10, "bound": 10, "skipped": 0})
        types = Counter(b["rule_type"] for b in self.report["bound"])
        self.assertEqual(set(types), {"inequality", "date_ordering", "identity",
                                      "conditional", "domain"})

    def test_each_rule_binds_to_the_primitive_its_signature_implies(self):
        expected = {
            "CF-01": "ineq",            # role >= role
            "CF-02": "dateorder",       # earlier <= later
            "CF-03": "identity",        # target == a / b * factor (+/- tol)
            "CF-04": "dom_range",       # role >= number  (inclusive one-sided bound)
            "CF-05": "dom_set",         # role in {a, b, c}
            "CF-06": "dom_range",       # role in [lo, hi]
            "CF-07": "presence",        # IF role = value THEN role populated
            "CF-08": "ineq",            # IF role > n THEN role = n
            "CF-09": "present_number",  # IF role in {..} THEN role present
            "CF-10": "ineq",            # role <= role * factor
        }
        for rule_id, primitive in expected.items():
            self.assertEqual(self.by_id[rule_id]["binding_primitive"], primitive, rule_id)
            self.assertEqual(self.by_id[rule_id]["binding_status"], "bound", rule_id)

    def test_the_scale_factor_is_parsed_from_the_text_not_hardcoded(self):
        params = self.by_id["CF-10"]["binding_params_json"]
        self.assertEqual(params["expression"]["op"], "<=")
        self.assertEqual(params["expression"]["right_role"], "original_balance")
        self.assertEqual(params["expression"]["factor"], 1.5)

    def test_the_identity_formula_is_captured_declaratively(self):
        params = self.by_id["CF-03"]["binding_params_json"]["expression"]
        self.assertEqual(params["target"], "original_ltv")
        self.assertEqual(params["operands"], ["original_balance", "original_property_value"])
        self.assertEqual(params["operator"], "/")
        self.assertEqual(params["factor"], 100.0)
        self.assertEqual(params["tolerance"], 0.5)

    def test_an_if_condition_becomes_a_condition_primitive_not_an_expression(self):
        params = self.by_id["CF-08"]["binding_params_json"]
        self.assertEqual(params["condition"]["primitive"], "ineq")
        self.assertEqual(params["condition"]["op"], ">")
        self.assertEqual(params["condition"]["right_literal"], 90.0)
        self.assertEqual(params["expression"]["primitive"], "ineq")
        self.assertEqual(params["expression"]["op"], "==")

    def test_an_inline_exception_clause_is_captured_with_role_and_value(self):
        params = self.by_id["CF-07"]["binding_params_json"]
        self.assertEqual(params["exceptions"], [{"role": "cure_indicator", "value": 1}])
        # and the exception's stated REASON came off the notes appendix
        self.assertTrue(self.by_id["CF-07"]["encoded_exceptions_json"])

    def test_dtypes_are_inferred_structurally_from_each_rule_s_own_usage(self):
        self.assertEqual(self.by_id["CF-02"]["binding_params_json"]["role_dtypes"],
                         {"possession_date": "date", "sale_date": "date"})
        self.assertEqual(self.by_id["CF-04"]["binding_params_json"]["role_dtypes"],
                         {"drawn_balance": "number"})
        # a two-value enumerated domain implies a flag; a wider one a category
        self.assertEqual(self.by_id["CF-05"]["binding_params_json"]["role_dtypes"],
                         {"workout_state": "category"})

    def test_binding_writes_an_attributable_audit_row(self):
        # Scoped to THIS version's rules: the sandboxed DB is shared across
        # test modules in one pytest process (SYSTEM_DB_PATH is read once,
        # at system_db import), so a global count would be order-dependent.
        mine = {r["rule_id"] for r in s.query("kb_rules", version_id=self.version_id)}
        events = [e for e in s.query("transaction_log")
                  if e["event"] == "kb_rule_bound" and e["payload"]["rule_id"] in mine]
        self.assertEqual(len(events), 10)
        self.assertTrue(all(e["actor"] == "tester" for e in events))


class RefusalTests(unittest.TestCase):
    """KB-09/KB-11 — the binder refuses honestly rather than guessing."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.hazard_version = _upload("hazard.md", HAZARD_MD)
        cls.unsupported_version = _upload("unsupported.md", UNSUPPORTED_MD)

    def test_a_parse_hazard_blocks_binding_even_though_the_text_would_match(self):
        row = s.query("kb_rules", version_id=self.hazard_version)[0]
        self.assertTrue(row["parse_hazards_json"])          # Phase 5 flagged it
        report = binder.bind_version(TENANT, self.hazard_version, "tester")
        self.assertEqual(report["counts"]["bound"], 0)
        self.assertIn("parse hazard", report["skipped"][0]["reason"])
        after = s.query_one("kb_rules", rule_id=row["rule_id"])
        self.assertEqual(after["binding_status"], "reference-only")
        self.assertIsNone(after["binding_primitive"])

    def test_bind_rule_refuses_a_hazard_flagged_rule_even_when_called_directly(self):
        row = s.query("kb_rules", version_id=self.hazard_version)[0]
        with self.assertRaises(ValueError):
            binder.bind_rule(row["rule_id"], "ineq", {"expression": {}}, "tester")

    def test_unsupported_phrasings_stay_reference_only_with_a_stated_reason(self):
        report = binder.bind_version(TENANT, self.unsupported_version, "tester")
        self.assertEqual(report["counts"]["bound"], 0)
        self.assertEqual(len(report["skipped"]), 4)
        for skip in report["skipped"]:
            self.assertTrue(skip["reason"].strip())
            row = s.query_one("kb_rules", rule_id=skip["rule_id"])
            self.assertEqual(row["binding_status"], "reference-only")
        reasons = {sk["source_rule_id"]: sk["reason"] for sk in report["skipped"]}
        self.assertIn("IF-condition is not a supported form", reasons["UN-01"])
        self.assertIn("IF-condition is not a supported form", reasons["UN-02"])
        self.assertIn("matched no generic pattern", reasons["UN-03"])
        self.assertIn("matched no generic pattern", reasons["UN-04"])

    def test_an_unbound_rule_can_never_be_retrieved_for_execution(self):
        for row in s.query("kb_rules", version_id=self.unsupported_version):
            kb.publish_rule(TENANT, row["rule_id"], "rev", ["kb_reviewer"], "domain_fact")
        eligible = kb.list_eligible_rules(TENANT, "cross_field_engine", require_bound=True)
        self.assertEqual([r for r in eligible["rules"]
                          if r["version_id"] == self.unsupported_version], [])


def _executable_source(source: str) -> str:
    """Strip docstrings and comments — the assertions below are about CODE,
    not about the prose that explains it."""
    code = re.sub(r'"""(?:.|\n)*?"""', "", source)
    return "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("#"))


class NoPerRuleSpecialCasingTests(unittest.TestCase):
    """The binder is generic by construction: it may not name a rule."""

    def test_binder_source_contains_no_document_specific_rule_identifier(self):
        source = (BACKEND_ROOT / "dq_diagnostics" / "engines" / "cross_field"
                  / "binder.py").read_text(encoding="utf-8")
        code = _executable_source(source)
        # A KB rule id ("IRB-01", "IFRS9-22", "CF-07") anywhere in executable
        # code would be a per-rule branch — the thing KB-09 forbids. The
        # 0.4.0 requirement codes (KB-11, CFR-07, …) are references, not
        # rule ids, and are excluded by name.
        requirement_prefixes = {"KB", "CFR", "FWK", "DX", "ING", "PLT", "APL", "RCA",
                                "DET", "WSP", "DIA", "C", "D", "T"}
        hits = [m.group(0) for m in re.finditer(r"\b([A-Z][A-Z0-9]{0,6})-\d{1,3}\b", code)
                if m.group(1) not in requirement_prefixes]
        self.assertEqual(hits, [])

    def test_binder_source_names_no_business_role(self):
        source = (BACKEND_ROOT / "dq_diagnostics" / "engines" / "cross_field"
                  / "binder.py").read_text(encoding="utf-8")
        code = _executable_source(source)
        for token in ("exposure", "balance", "ltv", "workout", "forbearance", "days_past_due"):
            self.assertNotIn(token, code.lower(), f"binder.py names the business role {token!r}")


@unittest.skipUnless(S9_PDF.exists(), f"S9 fixture not found at {S9_PDF}")
class S9RealDataTests(unittest.TestCase):
    """6-T1 — the binder against S9's ACTUAL 49 parsed rules.

    The numbers below are the honest, measured outcome, not a target: 23 of
    49 bind; 21 are held back because Phase 5 flagged a collapsed role name
    on them (KB-11 — the role identity is not confirmed, so no predicate
    over it may be executed); 5 use phrasings the generic patterns do not
    cover and stay reference-only with a stated reason. Every one of those
    26 is explainable to the person who uploaded the document.
    """

    S9_TOTAL = 49
    S9_BOUND = 23
    S9_HAZARD_BLOCKED = 21
    S9_UNSUPPORTED = 5
    # docs/0.4.0/04-kb-contract.md §1.6's published split, reproduced here as
    # the 6-T1 rule-count assertion.
    BY_TYPE = {"conditional": 13, "date_ordering": 3, "domain": 26, "identity": 3, "inequality": 4}
    BY_FRAMEWORK = {"IRB": 36, "IFRS9": 11, "both": 2}

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        up = kb.upload_document(TENANT, S9_PDF.name, "application/pdf",
                                S9_PDF.read_bytes(), None, "tester")
        cls.version_id = up["version"]["version_id"]
        kb.submit_for_review(TENANT, cls.version_id, "tester", "domain_fact")
        cls.rows = s.query("kb_rules", version_id=cls.version_id)
        cls.report = binder.bind_version(TENANT, cls.version_id, "tester")

    def test_the_parsed_rule_set_matches_the_documented_counts(self):
        self.assertEqual(len(self.rows), self.S9_TOTAL)
        self.assertEqual(dict(Counter(r["rule_type"] for r in self.rows)), self.BY_TYPE)
        self.assertEqual(dict(Counter(r["framework"] for r in self.rows)), self.BY_FRAMEWORK)

    def test_the_use_case_subsets_partition_the_rule_set(self):
        """CFR-03 — 'both' rules run under either scope; the two single-scope
        subsets plus the shared rules sum to the whole 49."""
        irb = [r for r in self.rows if rule_in_scope(r, "irb")]
        ifrs9 = [r for r in self.rows if rule_in_scope(r, "ifrs9")]
        both = [r for r in self.rows if normalize_framework(r["framework"]) == "both"]
        self.assertEqual(len(irb), 36 + 2)
        self.assertEqual(len(ifrs9), 11 + 2)
        self.assertEqual(len(both), 2)
        self.assertEqual(len(irb) + len(ifrs9) - len(both), self.S9_TOTAL)
        self.assertEqual(len([r for r in self.rows if rule_in_scope(r, "both")]), self.S9_TOTAL)

    def test_binder_coverage_on_real_parser_output_is_exactly_accounted_for(self):
        counts = self.report["counts"]
        self.assertEqual(counts["considered"], self.S9_TOTAL)
        self.assertEqual(counts["bound"], self.S9_BOUND)
        hazard = [k for k in self.report["skipped"] if "parse hazard" in k["reason"]]
        other = [k for k in self.report["skipped"] if "parse hazard" not in k["reason"]]
        self.assertEqual(len(hazard), self.S9_HAZARD_BLOCKED)
        self.assertEqual(len(other), self.S9_UNSUPPORTED)
        self.assertEqual(len(hazard) + len(other) + counts["bound"], self.S9_TOTAL)

    def test_bound_rules_span_three_rule_types_and_five_primitives(self):
        by_type = Counter(b["rule_type"] for b in self.report["bound"])
        self.assertEqual(dict(by_type), {"domain": 14, "conditional": 6, "date_ordering": 3})
        by_primitive = Counter(b["primitive"] for b in self.report["bound"])
        self.assertEqual(dict(by_primitive),
                         {"dom_range": 10, "ineq": 4, "dateorder": 3, "dom_set": 3,
                          "present_number": 3})

    def test_no_bound_rule_carries_an_unresolved_parse_hazard(self):
        for entry in self.report["bound"]:
            row = s.query_one("kb_rules", rule_id=entry["rule_id"])
            self.assertFalse(row["parse_hazards_json"])

    def test_every_skipped_rule_states_why(self):
        for skip in self.report["skipped"]:
            self.assertTrue((skip["reason"] or "").strip())
            self.assertIn(s.query_one("kb_rules", rule_id=skip["rule_id"])["binding_status"],
                          {"reference-only", "unparsed"})


if __name__ == "__main__":
    unittest.main()
