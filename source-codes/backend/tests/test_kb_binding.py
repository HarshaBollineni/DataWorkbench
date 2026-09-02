"""Phase 5 (KB-01..KB-16) — binding status + retrieval invariant tests.

See docs/0.4.0/04-kb-contract.md. Standalone-runnable
(``python -m unittest tests.test_kb_binding``); same SYSTEM_DB_PATH/
KB_STORAGE_DIR-at-import-time sandboxing convention as test_kb.py.

5-T3 — unparsed/reference-only rules are displayable but PROVABLY non-
       executable: list_eligible_rules(require_bound=True) excludes anything
       not binding_status='bound'. Phase 6 wired kb.publish_rule to call the
       cross-field binder automatically (KB-08); binding_status='bound' is
       still written in exactly one place repo-wide — the binder itself,
       never an inline literal in kb.py or anywhere else (KB-09's "never
       execute a predicate recovered from prose" — single-writer discipline
       is how that stays true as the codebase grows).
5-T4 — a collapsed role name is surfaced for confirmation, never silently
       used as-is.
5-T6 — draft rules never reach list_eligible_rules; no LLM path anywhere in
       the Phase 5 code (kb.py + kb_convert.py).
5-T8 — business_rules.json and its loader are gone (KB-01/C-36); no module
       under backend/ ships an embedded domain rule set in its place.
5-T9 — publish still requires the kb_reviewer role; shelf life / schema-
       change invalidation still work with the new KB-07 fields present.
"""
from __future__ import annotations

import ast
import inspect
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-kb-binding.db"
_TMP_KB_STORAGE = Path(tempfile.gettempdir()) / "archimedes-test-kb-binding-storage"
if _TMP_DB.exists():
    _TMP_DB.unlink()
if _TMP_KB_STORAGE.exists():
    shutil.rmtree(_TMP_KB_STORAGE)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["KB_STORAGE_DIR"] = str(_TMP_KB_STORAGE)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
import kb  # noqa: E402
import kb_convert  # noqa: E402
from seeds.taxonomy_seed import BOOTSTRAP_TENANT, seed_platform, seed_taxonomy  # noqa: E402

TENANT = BOOTSTRAP_TENANT
EDITOR_ROLES = ["kb_editor", "kb_reviewer"]
BACKEND_ROOT = Path(__file__).resolve().parent.parent

# A small table with a fuzzy-matched header (Sev/Type/Rule/Roles) plus one
# collapsed role name — this is a hand-built fixture (not S9) proving the
# hazard detector generalizes to any similarly-shaped table, and is exactly
# the shape 5-T4 asks for: a constructed row with "exposureatdefault".
TABLE_WITH_COLLAPSED_ROLE_MD = """## Rule catalogue

| ID | Sev | Type | Entity | Roles (semantic) | Rule | Reg reference |
| --- | --- | --- | --- | --- | --- | --- |
| T-01 | CRIT | Inequality | account | exposureatdefault, balance | exposureatdefault >= balance | - |
| T-02 | MINO | Domain/bound | account | balance | balance >= 0 | - |
"""


def _upload_and_submit(filename: str, body: str, category: str | None = "domain_fact"):
    upload = kb.upload_document(TENANT, filename, "text/markdown", body.encode("utf-8"), None, "tester")
    preview = kb.submit_for_review(TENANT, upload["version"]["version_id"], "tester", category)
    return upload, preview


class BindingStatusInvariantTests(unittest.TestCase):
    """5-T3 — displayable, never executable, until a real primitive match."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_table_derived_rule_defaults_to_reference_only_never_bound(self):
        _, preview = _upload_and_submit("catalogue.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        self.assertEqual(len(preview["rules"]), 2)
        for rule in preview["rules"]:
            self.assertIn(rule["binding_status"], {"reference-only", "unparsed"})
            self.assertNotEqual(rule["binding_status"], "bound")
            self.assertIsNone(rule["binding_primitive"])

    def test_prose_only_rule_defaults_to_unparsed(self):
        _, preview = _upload_and_submit("prose.md", "## A rule\nSome free-text governance note.\n")
        self.assertEqual(preview["rules"][0]["binding_status"], "unparsed")

    def test_no_inline_dict_literal_in_kb_module_ever_assigns_bound(self):
        """Invariant-bite style proof, updated for Phase 6: kb.py's own
        source never contains an INLINE ``"binding_status": "bound"`` dict
        literal — every write path that sets ``binding_status`` directly
        (e.g. rule extraction) can only produce 'reference-only' or
        'unparsed'.

        This is narrower than the Phase-5-era claim ("kb.py never binds
        anything"), which is no longer true of the system as a whole:
        ``publish_rule`` now calls
        ``domains.test_lab.diagnostics.t2_d04_cross_field_business_rule.binder.bind_rule`` (Phase 6,
        KB-08) as the automatic, signature-exact binding trigger. What
        this test still proves — and the invariant that actually matters —
        is single-writer discipline: the literal string 'bound' is written
        in exactly ONE place in the whole codebase
        (``binder.bind_rule``, see test_cross_field_binder.py), never
        inlined ad hoc here or anywhere else. See test_binder_is_the_only_
        writer_of_bound below."""
        source = inspect.getsource(kb)
        assignments = re.findall(r'"binding_status"\s*:\s*([^,\n}]+)', source)
        self.assertTrue(assignments, "expected at least one binding_status assignment site")
        for expr in assignments:
            self.assertNotIn('"bound"', expr)
            self.assertNotIn("'bound'", expr)

    def test_binder_is_the_only_writer_of_bound(self):
        """Repo-wide proof (not just kb.py): grep every backend .py file
        (excluding tests) for an inline ``"binding_status": "bound"`` /
        ``'binding_status': 'bound'`` literal — it must appear in exactly
        one file, domains/test_lab/diagnostics/t2_d04_cross_field_business_rule/binder.py (KB-08/09:
        a rule reaches 'bound' only through the signature-exact binder,
        never a second ad hoc write path)."""
        backend_root = Path(kb.__file__).resolve().parent
        pattern = re.compile(r"""['"]binding_status['"]\s*:\s*['"]bound['"]""")
        hits = []
        for path in backend_root.rglob("*.py"):
            rel = path.relative_to(backend_root)
            if "tests" in rel.parts or "__pycache__" in rel.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if pattern.search(text):
                hits.append(str(rel).replace("\\", "/"))
        self.assertEqual(hits, ["domains/test_lab/diagnostics/t2_d04_cross_field_business_rule/binder.py"])

    def test_require_bound_excludes_reference_only_and_unparsed_from_retrieval(self):
        """Phase 6 wires kb.publish_rule to attempt binding automatically
        (KB-08), so TABLE_WITH_COLLAPSED_ROLE_MD's two rules now split
        honestly: T-01 (exposureatdefault — a KB-11 collapsed-role hazard)
        can never bind; T-02 (balance >= 0 — a clean domain/bound rule, no
        hazard) legitimately does. require_bound=True must reflect exactly
        that split, not blanket-exclude everything — the old Phase-5-era
        assumption ('nothing can ever bind') doesn't hold once a binder
        exists, and asserting it would hide the fix actually working."""
        _, preview = _upload_and_submit("catalogue2.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        for rule in preview["rules"]:
            kb.publish_rule(TENANT, rule["rule_id"], "tester", roles=EDITOR_ROLES, category="domain_fact")

        hazard_id = next(r["rule_id"] for r in preview["rules"] if r["source_rule_id"] == "T-01")
        clean_id = next(r["rule_id"] for r in preview["rules"] if r["source_rule_id"] == "T-02")

        without_gate = {r["rule_id"] for r in
                        kb.list_eligible_rules(TENANT, "planner", require_bound=False)["rules"]}
        with_gate = {r["rule_id"] for r in
                     kb.list_eligible_rules(TENANT, "planner", require_bound=True)["rules"]}
        self.assertIn(hazard_id, without_gate)
        self.assertIn(clean_id, without_gate)
        # The gate excludes the hazard-flagged rule and includes only the
        # genuinely bound one.
        self.assertNotIn(hazard_id, with_gate)
        self.assertIn(clean_id, with_gate)
        self.assertEqual(
            s.query_one("kb_rules", rule_id=hazard_id)["binding_status"], "reference-only")
        self.assertEqual(
            s.query_one("kb_rules", rule_id=clean_id)["binding_status"], "bound")

    def test_require_bound_is_backward_compatible_default_false(self):
        """Existing callers (pre-Phase-5) that don't pass require_bound must
        see unchanged behaviour."""
        _, preview = _upload_and_submit("catalogue3.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        for rule in preview["rules"]:
            kb.publish_rule(TENANT, rule["rule_id"], "tester", roles=EDITOR_ROLES, category="domain_fact")
        default_call = kb.list_eligible_rules(TENANT, "planner")
        explicit_false = kb.list_eligible_rules(TENANT, "planner", require_bound=False)
        self.assertEqual({r["rule_id"] for r in default_call["rules"]},
                         {r["rule_id"] for r in explicit_false["rules"]})


class HazardDetectionTests(unittest.TestCase):
    """5-T4 — a collapsed role name is surfaced for confirmation."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_collapsed_role_is_flagged_on_the_rule_and_in_playback_hazards(self):
        upload, preview = _upload_and_submit("catalogue4.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        t01 = next(r for r in preview["rules"] if r["source_rule_id"] == "T-01")
        self.assertTrue(t01["parse_hazards_json"])
        hazard_texts = {h["role_text"] for h in t01["parse_hazards_json"]}
        self.assertIn("exposureatdefault", hazard_texts)
        for h in t01["parse_hazards_json"]:
            self.assertIn("confirm", h["note"].lower())

        playback = kb.playback_summary(TENANT, upload["version"]["version_id"])
        playback_role_texts = {h["role_text"] for h in playback["hazards"]}
        self.assertIn("exposureatdefault", playback_role_texts)
        # every hazard entry in the aggregated playback view carries which
        # rule it came from, not just the bare role text
        self.assertTrue(all("rule_id" in h for h in playback["hazards"]))

    def test_ordinary_short_role_names_are_not_flagged(self):
        _, preview = _upload_and_submit("catalogue5.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        t02 = next(r for r in preview["rules"] if r["source_rule_id"] == "T-02")
        self.assertEqual(t02["parse_hazards_json"], [])  # "balance" is short, not collapsed

    def test_hazard_role_is_never_auto_split_or_rewritten(self):
        """KB-11 — never silently guessed: the raw recovered string is kept
        verbatim, not corrected into a guessed 'exposure_at_default'."""
        _, preview = _upload_and_submit("catalogue6.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        t01 = next(r for r in preview["rules"] if r["source_rule_id"] == "T-01")
        self.assertIn("exposureatdefault", t01["semantic_roles_json"])
        self.assertNotIn("exposure_at_default", t01["semantic_roles_json"])


class RetrievalInvariantTests(unittest.TestCase):
    """5-T6 — draft rules never reach retrieval; no LLM path anywhere."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_draft_table_derived_rules_never_appear_in_eligible_rules(self):
        _, preview = _upload_and_submit("catalogue7.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        draft_ids = {r["rule_id"] for r in preview["rules"]}
        self.assertTrue(all(r["lifecycle_state"] == "draft" for r in preview["rules"]))
        result = kb.list_eligible_rules(TENANT, "intake")
        self.assertFalse(draft_ids & {r["rule_id"] for r in result["rules"]})
        result_bound = kb.list_eligible_rules(TENANT, "intake", require_bound=True)
        self.assertFalse(draft_ids & {r["rule_id"] for r in result_bound["rules"]})

    def test_no_llm_import_anywhere_in_kb_module_or_kb_convert_module(self):
        for module, path in ((kb, BACKEND_ROOT / "kb.py"), (kb_convert, BACKEND_ROOT / "kb_convert.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            offending = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if re.search(r"\b(openai|anthropic)\b", alias.name) or alias.name.startswith("ai.llm"):
                            offending.append(alias.name)
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if re.search(r"\b(openai|anthropic)\b", mod) or mod.startswith("ai.llm"):
                        offending.append(mod)
            self.assertEqual(offending, [], f"{module.__name__} imports an LLM module: {offending}")
            self.assertNotRegex(source, r"\bai\.llm\b")


class BusinessRuleSetDeletionTests(unittest.TestCase):
    """5-T8 — business_rules.json + its loader are gone; nothing under
    backend/ ships a replacement domain rule set (KB-01/C-36)."""

    def test_business_rules_json_file_does_not_exist(self):
        candidates = list(BACKEND_ROOT.rglob("business_rules.json"))
        # synthetic-kb/ fixtures are explicitly test-only and out of scope —
        # but for backend/, the file must not exist anywhere at all.
        self.assertEqual(candidates, [], f"business_rules.json still present at: {candidates}")

    def test_ai_v2_service_does_not_reference_rules_path_or_rules_for_loader(self):
        service_path = BACKEND_ROOT / "ai" / "v2" / "service.py"
        source = service_path.read_text(encoding="utf-8")
        self.assertNotIn("RULES_PATH", source)
        # _rules_for() may still exist as a stub (returns []) per the plan's
        # KB-01 cleanup note (its docstring is even allowed to name the
        # deleted file for context) — what must be gone is any FUNCTIONAL
        # reference: a file open/read/Path() call actually touching it.
        tree = ast.parse(source)
        func = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_rules_for"), None)
        self.assertIsNotNone(func, "_rules_for() was expected to still exist as a no-op stub")
        body_source = ast.get_source_segment(source, func) or ""
        # Drop the docstring line(s) before checking for a functional reference.
        body_without_docstring = "\n".join(
            line for line in body_source.splitlines()
            if "business_rules.json" not in line and "RULES_PATH" not in line
        )
        for forbidden in ("open(", "json.load", ".read_text(", "Path(__file__"):
            self.assertNotIn(forbidden, body_without_docstring,
                             f"_rules_for() still touches a file ({forbidden!r})")

    def test_no_backend_module_defines_a_large_embedded_rule_literal(self):
        """Heuristic: outside tests/ and synthetic-kb/, no .py file under
        backend/ defines a dict/list literal with 20+ entries shaped like
        {id, rule, severity} (the deleted file's shape) at module level."""
        offenders = []
        for path in BACKEND_ROOT.rglob("*.py"):
            rel = path.relative_to(BACKEND_ROOT)
            parts = rel.parts
            if parts[0] in ("tests", "__pycache__") or "synthetic-kb" in parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                literal = None
                if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) >= 20:
                    literal = node.elts
                elif isinstance(node, ast.Dict) and len(node.keys) >= 20:
                    literal = node.values
                if literal is None:
                    continue
                dict_like = sum(1 for el in literal if isinstance(el, ast.Dict))
                if dict_like >= 20:
                    sample_keys = set()
                    for el in literal:
                        if isinstance(el, ast.Dict):
                            for k in el.keys:
                                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                    sample_keys.add(k.value)
                    if {"id", "rule"} <= sample_keys or {"id", "severity"} <= sample_keys:
                        offenders.append(str(rel))
        self.assertEqual(offenders, [], f"module(s) embed a business-rule-shaped literal: {offenders}")


class GovernanceRegressionTests(unittest.TestCase):
    """5-T9 — publish role-gate / shelf life / schema invalidation still
    hold with the new KB-07 fields present on the row."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_publish_still_requires_kb_reviewer_role_for_table_derived_rule(self):
        _, preview = _upload_and_submit("catalogue8.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        rule_id = preview["rules"][0]["rule_id"]
        with self.assertRaises(kb.ForbiddenError):
            kb.publish_rule(TENANT, rule_id, "tester", roles=["kb_editor"], category="domain_fact")
        published = kb.publish_rule(TENANT, rule_id, "tester", roles=EDITOR_ROLES, category="domain_fact")
        self.assertEqual(published["lifecycle_state"], "published")
        # KB-07 fields survive publish untouched (publish_rule's update()
        # only sets the fields it names — verified here, not just asserted).
        self.assertEqual(published["source_rule_id"], "T-01")
        self.assertEqual(published["rule_type"], "inequality")
        self.assertTrue(published["parse_hazards_json"])

    def test_shelf_life_and_schema_invalidation_still_flip_trust_level(self):
        from datetime import datetime, timedelta
        _, preview = _upload_and_submit("catalogue9.md", TABLE_WITH_COLLAPSED_ROLE_MD)
        rule_id = preview["rules"][1]["rule_id"]  # T-02, no hazards, simpler
        kb.publish_rule(TENANT, rule_id, "tester", roles=EDITOR_ROLES, category="domain_fact",
                        shelf_life_months=1)
        backdated = (datetime.fromisoformat(s.now_ist()) - timedelta(days=90)).isoformat()
        s.update("kb_rules", {"rule_id": rule_id}, {"last_confirmed_date": backdated})
        self.assertGreaterEqual(kb.check_shelf_life(TENANT), 1)
        refreshed = s.query_one("kb_rules", rule_id=rule_id)
        self.assertEqual(refreshed["trust_level"], "inferred")
        # KB-07 fields untouched by the shelf-life flip.
        self.assertEqual(refreshed["source_rule_id"], "T-02")
        self.assertEqual(refreshed["rule_type"], "domain")


if __name__ == "__main__":
    unittest.main()
