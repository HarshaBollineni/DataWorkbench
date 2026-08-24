"""Phase 6 — the cross-field PURE CORE (CFR-01..CFR-10, CFR-15/16).

Standalone-runnable (``python -m unittest tests.test_cross_field_engine``);
same SYSTEM_DB_PATH-at-import-time sandboxing convention as
tests/test_register.py — although almost everything here is DB-free by
construction, which is the point: the core is pure.

6-T3  each of the nine primitives has a passing, a violating AND a
      skipped-censored case (for the four whose semantics cannot return
      None, the censoring case is asserted where it actually lives — the
      engine's scope/verdict accounting; see PrimitiveTests' docstrings).
6-T4  every ladder tier is exercised by a fixture that actually reaches
      that tier's branch, plus the 0.70 unresolved floor and the dtype gate.
6-T6  verdict gate, severity ordering, exception reasons, <=5 evidence
      rows and CLUSTERED vs SCATTERED against a known-concentration fixture
      whose expected lift is computed by hand in the test.
6-T7  determinism: two runs of one frozen manifest are byte-identical.
6-T8  purity: no print/input/argparse/open(/sys.exit in the core; S5's LLM
      API-key env var appears nowhere under backend/ (the literal is
      assembled at runtime in the test so this file is not its own match);
      no per-role vocabulary dict survives in roles.py (DX-04).
6-T13 no exec/eval/compile anywhere on the new run path.
6-T14 a verdict-shaped payload cannot be constructed as a candidate flag,
      nor the reverse (FWK-07 shapes are structurally distinct).
"""
from __future__ import annotations

import ast
import os
import re
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-cf-engine.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import pandas as pd  # noqa: E402

from dq_diagnostics.engines.cross_field import engine as cf_engine  # noqa: E402
from dq_diagnostics.engines.cross_field import result as cf_result  # noqa: E402
from dq_diagnostics.engines.cross_field import roles as cf_roles  # noqa: E402
from dq_diagnostics.engines.cross_field import rules as cf_rules  # noqa: E402
from dq_diagnostics.result import DiagnosticResult, InvalidDiagnosticResultError  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parent.parent

THRESHOLDS = {"tolerance": 0.0, "cluster_lift": 3.0, "cluster_coverage": 0.5,
              "grain_role": "facility_id", "segment_role": "region"}


def _rule(rule_id, rule_type, roles, expression, condition=None, *, severity="MATERIAL",
          text="", exceptions=(), notes=(), tolerance=0.0, framework="irb",
          regulatory_ref=None, entity="workout"):
    return cf_rules.Rule(
        id=rule_id, rule_type=rule_type, description=text or rule_id, roles=tuple(roles),
        primitive=expression["primitive"],
        params={"primitive": expression["primitive"], "expression": expression,
                "condition": condition, "roles": list(roles)},
        entity=entity, framework=framework, severity=severity, tolerance=tolerance,
        exceptions_spec=tuple(exceptions), exception_notes=tuple(notes),
        regulatory_ref=regulatory_ref, kb_rule_id=f"kb_{rule_id}")


def _match(role, table, column, score=1.0, via="auto"):
    return cf_roles.RoleMatch(role, table, column, score, "fixture", via)


def _resolved(table, roles):
    return {r: _match(r, table, r) for r in roles}


def _vocab(roles, entity="workout"):
    return {r: {"entity": entity, "dtype": None, "synonyms": [r]} for r in roles}


# ═══════════════════════════════════════════════════════════════════════════
#  6-T3 — the nine primitives
# ═══════════════════════════════════════════════════════════════════════════

class PrimitiveTests(unittest.TestCase):
    """Three cases per primitive: it holds, it is violated, and a
    missing/censored value is SKIPPED rather than counted as a violation.

    Five primitives are genuinely tri-state and return ``None`` for the
    third case. The other four cannot, by their own semantics:

      * ``presence`` / ``present_number`` MEASURE populated-ness — a missing
        value is the finding, not a skip. Their censoring case is carried by
        the rule's IF-condition (S9's IRB-03: "open workouts are censored
        and out of scope by construction"), asserted here through the
        engine, which is where it actually lives.
      * ``eq_cond`` / ``in_cond`` are SCOPE selectors — a missing value
        takes the row out of scope, which is the skip. Asserted here as
        "a censored row never becomes a violation".
    """

    # --- tri-state primitives -------------------------------------------
    def test_ineq_pass_violate_skip(self):
        fn = cf_rules.ineq("a", ">=", "b")
        self.assertIs(fn({"a": 5, "b": 3}), True)
        self.assertIs(fn({"a": 1, "b": 3}), False)
        self.assertIsNone(fn({"a": None, "b": 3}))

    def test_ineq_scaled_right_hand_side(self):
        # S9's "exposure_at_default <= original_balance * 1.5" shape.
        fn = cf_rules.ineq("ead", "<=", "ob", factor=1.5)
        self.assertIs(fn({"ead": 150.0, "ob": 100.0}), True)
        self.assertIs(fn({"ead": 151.0, "ob": 100.0}), False)
        self.assertIsNone(fn({"ead": 151.0, "ob": None}))

    def test_dateorder_pass_violate_skip(self):
        fn = cf_rules.dateorder("e", "l")
        self.assertIs(fn({"e": "2024-01-01", "l": "2024-02-01"}), True)
        self.assertIs(fn({"e": "2024-03-01", "l": "2024-02-01"}), False)
        self.assertIsNone(fn({"e": "2024-03-01", "l": None}))

    def test_identity_pass_violate_skip(self):
        formula = cf_rules.make_formula(
            {"operands": ["b", "v"], "operator": "/", "factor": 100.0}, {"b": "b", "v": "v"})
        fn = cf_rules.identity("t", formula, 0.5)
        self.assertIs(fn({"t": 50.0, "b": 100.0, "v": 200.0}), True)
        self.assertIs(fn({"t": 90.0, "b": 100.0, "v": 200.0}), False)
        self.assertIsNone(fn({"t": 50.0, "b": None, "v": 200.0}))

    def test_identity_skips_a_zero_divisor_rather_than_failing(self):
        formula = cf_rules.make_formula({"operands": ["b", "v"], "operator": "/"},
                                        {"b": "b", "v": "v"})
        self.assertIsNone(cf_rules.identity("t", formula, 0.01)({"t": 1.0, "b": 1.0, "v": 0}))

    def test_dom_range_pass_violate_skip(self):
        fn = cf_rules.dom_range("c", 0.0, 200.0)
        self.assertIs(fn({"c": 50}), True)
        self.assertIs(fn({"c": -1}), False)
        self.assertIsNone(fn({"c": None}))

    def test_dom_set_pass_violate_skip(self):
        fn = cf_rules.dom_set("c", ["closed", "open", "written_off"])
        self.assertIs(fn({"c": "closed"}), True)
        self.assertIs(fn({"c": "settled"}), False)
        self.assertIsNone(fn({"c": None}))

    def test_dom_set_coerces_a_numeric_domain(self):
        fn = cf_rules.dom_set("c", [0, 1])
        self.assertIs(fn({"c": 1.0}), True)
        self.assertIs(fn({"c": "1"}), True)
        self.assertIs(fn({"c": 2}), False)

    # --- presence-family: censoring lives in the rule's scope ------------
    def test_presence_pass_violate_and_censored_row_is_out_of_scope(self):
        fn = cf_rules.presence(["a", "b"])
        self.assertIs(fn({"a": 1, "b": 2}), True)
        self.assertIs(fn({"a": 1, "b": None}), False)
        # The censoring case: an "open" (censored) row never reaches the
        # presence check at all, because the rule's IF-condition excludes it.
        rule = _rule("P-1", "conditional", ["state", "value"],
                     {"primitive": "presence", "cols": ["value"], "must": True},
                     {"primitive": "in_cond", "col": "state", "values": ["closed"]})
        df = pd.DataFrame([{"state": "closed", "value": 1},
                           {"state": "open", "value": None}])   # censored
        res = cf_engine.evaluate_rule(rule, _resolved("t", ["state", "value"]), {},
                                      {"t": df}, "facility_id", "region", 3.0, 0.5)
        self.assertEqual(res.outcome, "PASS")
        self.assertEqual(res.scope_n, 1)          # the censored row never counted
        self.assertEqual(res.violation_n, 0)

    def test_present_number_pass_violate_and_censored_row_is_out_of_scope(self):
        fn = cf_rules.present_number("c")
        self.assertIs(fn({"c": 5}), True)
        self.assertIs(fn({"c": None}), False)
        rule = _rule("P-2", "conditional", ["stage", "dpd"],
                     {"primitive": "present_number", "col": "dpd"},
                     {"primitive": "in_cond", "col": "stage", "values": [2, 3]})
        df = pd.DataFrame([{"stage": 3, "dpd": 10}, {"stage": 1, "dpd": None}])
        res = cf_engine.evaluate_rule(rule, _resolved("t", ["stage", "dpd"]), {},
                                      {"t": df}, "facility_id", "region", 3.0, 0.5)
        self.assertEqual(res.outcome, "PASS")
        self.assertEqual(res.scope_n, 1)

    # --- condition primitives: a missing value IS the skip ---------------
    def test_eq_cond_selects_violates_and_skips_a_censored_row(self):
        fn = cf_rules.eq_cond("c", "closed")
        self.assertIs(fn({"c": "closed"}), True)
        self.assertIs(fn({"c": "open"}), False)
        self.assertIs(fn({"c": None}), False)      # censored -> out of scope
        rule = _rule("C-1", "conditional", ["state", "sale"],
                     {"primitive": "presence", "cols": ["sale"], "must": True},
                     {"primitive": "eq_cond", "col": "state", "value": "closed"})
        df = pd.DataFrame([{"state": "closed", "sale": "2024-01-01"},
                           {"state": None, "sale": None}])
        res = cf_engine.evaluate_rule(rule, _resolved("t", ["state", "sale"]), {},
                                      {"t": df}, "facility_id", "region", 3.0, 0.5)
        self.assertEqual((res.outcome, res.scope_n, res.violation_n), ("PASS", 1, 0))

    def test_in_cond_selects_violates_and_skips_a_censored_row(self):
        fn = cf_rules.in_cond("c", ["closed", "written_off"])
        self.assertIs(fn({"c": "written_off"}), True)
        self.assertIs(fn({"c": "open"}), False)
        self.assertIs(fn({"c": None}), False)
        rule = _rule("C-2", "conditional", ["state", "lgd"],
                     {"primitive": "presence", "cols": ["lgd"], "must": True},
                     {"primitive": "in_cond", "col": "state", "values": ["closed", "written_off"]})
        df = pd.DataFrame([{"state": "written_off", "lgd": 0.4}, {"state": None, "lgd": None}])
        res = cf_engine.evaluate_rule(rule, _resolved("t", ["state", "lgd"]), {},
                                      {"t": df}, "facility_id", "region", 3.0, 0.5)
        self.assertEqual((res.outcome, res.scope_n, res.violation_n), ("PASS", 1, 0))

    def test_all_nine_primitives_are_registered_in_the_governed_catalogue(self):
        import ai.test_kit as test_kit
        registered = {h.name for h in test_kit.list_helpers() if h.kind == "cross_field_primitive"}
        self.assertEqual(registered,
                         set(cf_rules.PRIMITIVES) | {"evaluate_rule"})
        self.assertEqual(len(cf_rules.PRIMITIVES), 9)


# ═══════════════════════════════════════════════════════════════════════════
#  6-T4 — the role-resolution ladder
# ═══════════════════════════════════════════════════════════════════════════

class RoleLadderTests(unittest.TestCase):
    """Each fixture reaches ONE tier's branch and is asserted on the score
    AND the reason string that branch produces — not on a constant."""

    def test_tier1_exact_role_name(self):
        score, why = cf_roles.score_column("drawn_balance", {"synonyms": ["drawn_balance"]},
                                           "drawn_balance", "", [1, 2, 3])
        self.assertEqual(score, 1.0)
        self.assertIn("exact", why)

    def test_tier2_recorded_normalized_form_variant(self):
        # DX-04's own example: the KB records the role as a collapsed
        # extraction ("exposureatdefault") while the rule text spells it
        # "exposure_at_default". That pairing IS the synonym — derived, not
        # curated — and the column matches the variant exactly, not the role.
        vocab = cf_roles.derive_vocabulary([{
            "semantic_roles_json": ["exposureatdefault"], "entity": "workout",
            "rule_text": "exposure_at_default >= drawnbalance", "binding_params_json": {},
            "parse_hazards_json": [],
        }])
        self.assertIn("exposure_at_default", vocab["exposureatdefault"]["synonyms"])
        score, why = cf_roles.score_column("exposureatdefault", vocab["exposureatdefault"],
                                           "exposure_at_default", "", [1.0, 2.0])
        self.assertEqual(score, 0.92)
        self.assertIn("synonym", why)

    def test_tier3_dictionary_description_phrase(self):
        score, why = cf_roles.score_column("region_code", {"synonyms": ["region_code"]},
                                           "geo_bucket",
                                           "The region code for the facility.", ["a", "b"])
        self.assertEqual(score, 0.88)
        self.assertIn("dictionary description", why)

    def test_tier4_synonym_token_subset(self):
        score, why = cf_roles.score_column("balance", {"synonyms": ["balance"]},
                                           "closing_balance_amount", "", [1, 2])
        self.assertEqual(score, 0.80)
        self.assertIn("tokens contain all", why)

    def test_tier5_fuzzy_character_similarity_is_graded(self):
        score, why = cf_roles.score_column("region_code", {"synonyms": ["region_code"]},
                                           "region_codes", "", ["a", "b"])
        self.assertIn("similar to", why)
        self.assertGreaterEqual(score, 0.55)
        self.assertLessEqual(score, 0.75)

    def test_tier6_partial_token_overlap_is_graded(self):
        score, why = cf_roles.score_column("loan_balance", {"synonyms": ["loan_balance"]},
                                           "loan_id", "", [1, 2])
        self.assertIn("partial token overlap", why)
        self.assertGreaterEqual(score, 0.35)
        self.assertLessEqual(score, 0.60)

    def test_dtype_gate_vetoes_a_strong_name_match_on_non_numeric_data(self):
        """A numeric-role candidate whose data is text scores 0 DESPITE an
        exact name match — the gate is a veto, not a tie-break."""
        strong, _ = cf_roles.score_column("exposure_at_default", {"synonyms": ["exposure_at_default"],
                                                                 "dtype": "number"},
                                          "exposure_at_default", "", [1.0, 2.0, 3.0])
        self.assertEqual(strong, 1.0)
        vetoed, why = cf_roles.score_column("exposure_at_default",
                                            {"synonyms": ["exposure_at_default"], "dtype": "number"},
                                            "exposure_at_default", "",
                                            ["alpha", "beta", "gamma"])
        self.assertEqual(vetoed, 0.0)
        self.assertIn("not numeric", why)

    def test_dtype_gate_vetoes_a_non_binary_column_for_a_flag_role(self):
        vetoed, why = cf_roles.score_column("cure_indicator",
                                            {"synonyms": ["cure_indicator"], "dtype": "flag"},
                                            "cure_indicator", "", [0, 1, 7])
        self.assertEqual(vetoed, 0.0)
        self.assertIn("not binary", why)

    def test_a_best_score_below_the_floor_is_reported_unresolved(self):
        vocab = {"loan_balance": {"entity": "facility", "dtype": None,
                                  "synonyms": ["loan_balance"]}}
        resolved = cf_roles.resolve_roles(vocab, {"t": ["loan_id"]},
                                          {("t", "loan_id"): [1, 2, 3]})
        match = resolved["loan_balance"]
        self.assertIsNone(match.column)
        self.assertEqual(match.via, "unresolved")
        self.assertLess(match.score, cf_roles.RESOLVED_FLOOR)
        self.assertIn("too weak", match.reason)

    def test_override_beats_the_ladder_and_is_recorded_as_such(self):
        vocab = {"amount": {"entity": "facility", "dtype": None, "synonyms": ["amount"]}}
        resolved = cf_roles.resolve_roles(vocab, {"t": ["amount", "other"]},
                                          {("t", "amount"): [1], ("t", "other"): [2]},
                                          overrides={"amount": "t.other"})
        self.assertEqual((resolved["amount"].table, resolved["amount"].column), ("t", "other"))
        self.assertEqual(resolved["amount"].via, "override")
        self.assertEqual(resolved["amount"].score, 1.0)

    def test_entity_binding_is_a_majority_vote_and_ignores_grain_roles(self):
        vocab = {"a": {"entity": "workout", "dtype": None, "synonyms": ["a"]},
                 "b": {"entity": "workout", "dtype": None, "synonyms": ["b"]},
                 "c": {"entity": "workout", "dtype": None, "synonyms": ["c"]},
                 "facility_id": {"entity": "*", "dtype": None, "synonyms": ["facility_id"]}}
        resolved = {"a": _match("a", "wk", "a"), "b": _match("b", "wk", "b"),
                    "c": _match("c", "other", "c"),
                    "facility_id": _match("facility_id", "other", "facility_id")}
        self.assertEqual(cf_roles.bind_entities(resolved, vocab), {"workout": "wk"})

    def test_vocabulary_carries_entity_and_dtype_from_the_rules_themselves(self):
        vocab = cf_roles.derive_vocabulary([{
            "semantic_roles_json": ["possession_date", "sale_date"], "entity": "workout",
            "rule_text": "possession_date <= sale_date",
            "binding_params_json": {"role_dtypes": {"possession_date": "date",
                                                     "sale_date": "date"}},
            "parse_hazards_json": [],
        }], extra_roles=("facility_id",))
        self.assertEqual(vocab["possession_date"]["entity"], "workout")
        self.assertEqual(vocab["possession_date"]["dtype"], "date")
        self.assertEqual(vocab["facility_id"]["entity"], "*")
        self.assertIsNone(vocab["facility_id"]["dtype"])


# ═══════════════════════════════════════════════════════════════════════════
#  6-T6 — verdict gate, ordering, exceptions, evidence cap, pattern read
# ═══════════════════════════════════════════════════════════════════════════

def _concentration_frame():
    """60 rows: 40 'north', 20 'south'. Ten violations, ALL in 'south'.

    Hand-computed lift for 'south':
        violations_at_south / total_violations = 10/10 = 1.0
        scope_at_south      / total_scope      = 20/60 = 0.3333
        lift = 1.0 / 0.3333 = 3.0   >= cluster_lift (3.0)
        coverage of high-lift levels = 10/10 = 1.0  >= cluster_coverage (0.5)
    => CLUSTERED.
    """
    rows = []
    for i in range(1, 61):
        region = "north" if i <= 40 else "south"
        bad = region == "south" and i % 2 == 0
        rows.append({"facility_id": i, "region": region,
                     "ead": 80.0 if bad else 100.0, "drawn": 90.0})
    return pd.DataFrame(rows)


def _scattered_frame():
    """60 rows evenly split; violations spread proportionally (6 north, 3
    south out of 40/20) so every level's lift is ~1.0 => SCATTERED."""
    rows = []
    for i in range(1, 61):
        region = "north" if i <= 40 else "south"
        bad = i % 7 == 0
        rows.append({"facility_id": i, "region": region,
                     "ead": 80.0 if bad else 100.0, "drawn": 90.0})
    return pd.DataFrame(rows)


IENQ = {"primitive": "ineq", "left": "ead", "op": ">=", "right_role": "drawn"}


class VerdictGateTests(unittest.TestCase):

    def _run(self, df, rule, thresholds=None):
        resolved = {**_resolved("t", ["ead", "drawn"]),
                    "facility_id": _match("facility_id", "t", "facility_id"),
                    "region": _match("region", "t", "region")}
        return cf_engine.evaluate_rule(rule, resolved, {}, {"t": df}, "facility_id", "region",
                                       (thresholds or THRESHOLDS)["cluster_lift"],
                                       (thresholds or THRESHOLDS)["cluster_coverage"])

    def test_rate_above_tolerance_is_a_violation(self):
        res = self._run(_concentration_frame(), _rule("R-1", "inequality", ["ead", "drawn"], IENQ))
        self.assertEqual(res.outcome, "VIOLATION")
        self.assertEqual(res.violation_n, 10)
        self.assertEqual(res.scope_n, 60)
        self.assertAlmostEqual(res.violation_rate, 10 / 60)

    def test_rate_at_or_below_tolerance_is_a_pass(self):
        rule = _rule("R-2", "inequality", ["ead", "drawn"], IENQ, tolerance=0.20)
        res = self._run(_concentration_frame(), rule)
        self.assertEqual(res.outcome, "PASS")   # 0.1667 <= 0.20
        self.assertEqual(res.violation_n, 10)   # severity/tolerance never hide the count

    def test_empty_scope_is_not_applicable_with_the_reason_never_pass(self):
        rule = _rule("R-3", "conditional", ["ead", "drawn"], IENQ,
                     {"primitive": "eq_cond", "col": "ead", "value": -999})
        res = self._run(_concentration_frame(), rule)
        self.assertEqual(res.outcome, "NOT-APPLICABLE")
        self.assertEqual(res.na_reason, "empty scope (no rows meet IF-condition)")

    def test_all_rows_censored_is_not_applicable_with_the_reason(self):
        df = _concentration_frame()
        df["ead"] = None
        res = self._run(df, _rule("R-4", "inequality", ["ead", "drawn"], IENQ))
        self.assertEqual(res.outcome, "NOT-APPLICABLE")
        self.assertEqual(res.na_reason, "all in-scope rows had missing/censored values")
        self.assertEqual(res.skipped_semantics_n, 60)

    def test_an_unmapped_role_is_not_applicable_naming_the_role(self):
        rule = _rule("R-5", "inequality", ["ead", "drawn"], IENQ)
        resolved = {"ead": _match("ead", "t", "ead"),
                    "drawn": cf_roles.RoleMatch("drawn", None, None, 0.4, "too weak", "unresolved")}
        res = cf_engine.evaluate_rule(rule, resolved, {}, {"t": _concentration_frame()},
                                      "facility_id", "region", 3.0, 0.5)
        self.assertEqual(res.outcome, "NOT-APPLICABLE")
        self.assertEqual(res.na_reason, "role 'drawn' unmapped")

    def test_partially_censored_rows_are_skipped_not_counted_as_violations(self):
        df = _concentration_frame()
        df.loc[df.index[:5], "ead"] = None       # 5 censored, all previously fine
        res = self._run(df, _rule("R-6", "inequality", ["ead", "drawn"], IENQ))
        self.assertEqual(res.skipped_semantics_n, 5)
        self.assertEqual(res.scope_n, 55)
        self.assertEqual(res.violation_n, 10)

    def test_exceptions_are_applied_before_counting_and_reported_with_a_reason(self):
        df = _concentration_frame()
        df["cure"] = [1 if (r.region == "south" and r.ead == 80.0) else 0
                      for r in df.itertuples()]
        rule = cf_rules.Rule(
            id="R-7", rule_type="inequality", description="ead >= drawn",
            roles=("ead", "drawn", "cure"), primitive="ineq",
            params={"primitive": "ineq", "expression": IENQ, "condition": None},
            severity="MATERIAL", exceptions_spec=(("cure", 1),),
            exception_notes=("Cured workouts settle below the drawn balance by design.",))
        resolved = {**_resolved("t", ["ead", "drawn", "cure"]),
                    "facility_id": _match("facility_id", "t", "facility_id"),
                    "region": _match("region", "t", "region")}
        res = cf_engine.evaluate_rule(rule, resolved, {}, {"t": df}, "facility_id", "region",
                                      3.0, 0.5)
        self.assertEqual(res.excepted_n, 10)
        self.assertEqual(res.violation_n, 0)
        self.assertEqual(res.outcome, "PASS")
        entry = cf_result.build_structured_result([res], {}).rules[0]
        self.assertEqual(entry["exceptions_applied"], 10)
        # An exception count NEVER travels without its stated reason.
        structured = cf_result.build_structured_result([res], {})
        self.assertTrue(rule.exception_notes)
        self.assertEqual(structured.rollup["PASS"], 1)

    def test_evidence_is_capped_at_five_rows_and_keyed_by_the_grain_column(self):
        res = self._run(_concentration_frame(), _rule("R-8", "inequality", ["ead", "drawn"], IENQ))
        self.assertEqual(len(res.examples), 5)
        for row in res.examples:
            self.assertEqual(list(row)[0], "facility_id")
            self.assertEqual(set(row), {"facility_id", "ead", "drawn"})

    def test_clustered_pattern_on_a_known_concentration_fixture(self):
        res = self._run(_concentration_frame(), _rule("R-9", "inequality", ["ead", "drawn"], IENQ))
        self.assertEqual(res.pattern, "CLUSTERED")
        self.assertIn("lift=3.0", res.pattern_detail)
        self.assertIn("feed/segment fault", res.pattern_detail)

    def test_scattered_pattern_when_violations_track_the_population(self):
        res = self._run(_scattered_frame(), _rule("R-10", "inequality", ["ead", "drawn"], IENQ))
        self.assertEqual(res.outcome, "VIOLATION")
        self.assertEqual(res.pattern, "SCATTERED")
        self.assertIn("capture error", res.pattern_detail)

    def test_violation_pattern_formula_matches_the_hand_computed_lift(self):
        scope = [{"region": "north"}] * 40 + [{"region": "south"}] * 20
        violations = [{"region": "south"}] * 10
        pattern, detail = cf_engine.violation_pattern(scope, violations, "region", 3.0, 0.5)
        self.assertEqual(pattern, "CLUSTERED")
        self.assertIn("3.0", detail)
        # One notch above the observed lift and the same data is SCATTERED.
        pattern, _ = cf_engine.violation_pattern(scope, violations, "region", 3.1, 0.5)
        self.assertEqual(pattern, "SCATTERED")


class OrderingAndShapeTests(unittest.TestCase):

    def _structured(self):
        df = _concentration_frame()
        resolved = {**_resolved("t", ["ead", "drawn"]),
                    "facility_id": _match("facility_id", "t", "facility_id"),
                    "region": _match("region", "t", "region")}
        rules = [
            _rule("Z-MINOR", "inequality", ["ead", "drawn"], IENQ, severity="MINOR"),
            _rule("A-MATERIAL", "inequality", ["ead", "drawn"], IENQ, severity="MATERIAL"),
            _rule("M-CRITICAL", "inequality", ["ead", "drawn"], IENQ, severity="CRITICAL"),
            _rule("P-PASS", "inequality", ["ead", "drawn"], IENQ, severity="CRITICAL",
                  tolerance=1.0),
            _rule("N-NA", "conditional", ["ead", "drawn"], IENQ,
                  {"primitive": "eq_cond", "col": "ead", "value": -1}, severity="CRITICAL"),
        ]
        return cf_engine.execute_run(rules, resolved, _vocab(["ead", "drawn"]), {"t": df},
                                     THRESHOLDS, preamble={"use_case": "irb"})

    def test_violations_come_first_ordered_critical_material_minor(self):
        structured = self._structured()
        order = [r["rule_id"] for r in structured.rules]
        self.assertEqual(order[:3], ["M-CRITICAL", "A-MATERIAL", "Z-MINOR"])
        self.assertEqual(structured.rollup, {"PASS": 1, "VIOLATION": 3, "NOT-APPLICABLE": 1})
        self.assertEqual(order[3], "P-PASS")
        self.assertEqual(order[4], "N-NA")

    def test_not_applicable_entries_always_state_a_reason(self):
        for entry in self._structured().by_outcome("NOT-APPLICABLE"):
            self.assertTrue((entry.get("na_reason") or "").strip())

    def test_preamble_carries_every_s6_field(self):
        preamble = self._structured().preamble
        for key in ("use_case", "rules_loaded", "by_type", "roles_total", "roles_resolved",
                    "override_count", "auto_count", "parameters", "role_verification"):
            self.assertIn(key, preamble)
        self.assertEqual(preamble["roles_resolved"], preamble["roles_total"])
        self.assertEqual(preamble["override_count"] + preamble["auto_count"],
                         preamble["roles_resolved"])
        self.assertFalse(preamble["role_verification"]["enabled"])

    def test_text_rendering_derives_from_the_structure(self):
        structured = self._structured()
        text = cf_result.render_text(structured)
        self.assertIn("DATASET ROLL-UP", text)
        self.assertIn("NOT-APPLICABLE  (stated, never hidden)", text)
        self.assertIn("Roles resolved    : 4/4 (0 override, 4 auto)", text)
        # Severity ordering survives into the rendering.
        self.assertLess(text.index("M-CRITICAL"), text.index("A-MATERIAL"))
        self.assertLess(text.index("A-MATERIAL"), text.index("Z-MINOR"))


# ═══════════════════════════════════════════════════════════════════════════
#  6-T7 — determinism
# ═══════════════════════════════════════════════════════════════════════════

class DeterminismTests(unittest.TestCase):

    def test_two_runs_of_one_rule_set_are_byte_identical(self):
        df = _concentration_frame()
        resolved = {**_resolved("t", ["ead", "drawn"]),
                    "facility_id": _match("facility_id", "t", "facility_id"),
                    "region": _match("region", "t", "region")}
        rules = [_rule("D-1", "inequality", ["ead", "drawn"], IENQ, severity="CRITICAL"),
                 _rule("D-2", "inequality", ["ead", "drawn"], IENQ, tolerance=1.0)]
        first = cf_engine.execute_run(rules, resolved, _vocab(["ead", "drawn"]), {"t": df},
                                      THRESHOLDS, preamble={"use_case": "irb"})
        second = cf_engine.execute_run(rules, resolved, _vocab(["ead", "drawn"]), {"t": df.copy()},
                                       THRESHOLDS, preamble={"use_case": "irb"})
        self.assertEqual(first.to_json(), second.to_json())   # byte-identical, not "close"

    def test_evidence_values_serialize_to_stable_json(self):
        df = _concentration_frame()
        df.loc[df.index[41], "ead"] = float("nan")
        resolved = {**_resolved("t", ["ead", "drawn"]),
                    "facility_id": _match("facility_id", "t", "facility_id"),
                    "region": _match("region", "t", "region")}
        structured = cf_engine.execute_run(
            [_rule("D-3", "inequality", ["ead", "drawn"], IENQ)], resolved,
            _vocab(["ead", "drawn"]), {"t": df}, THRESHOLDS)
        import json
        json.loads(structured.to_json())   # raises if a NaN or numpy scalar leaked through


# ═══════════════════════════════════════════════════════════════════════════
#  6-T8 / 6-T13 — purity and no-codegen, by source inspection
# ═══════════════════════════════════════════════════════════════════════════

CORE_MODULES = ("rules.py", "roles.py", "engine.py", "result.py", "primitives.py")
CORE_DIR = BACKEND_ROOT / "dq_diagnostics" / "engines" / "cross_field"

FORBIDDEN_IN_CORE = ("print(", "input(", "argparse", "open(", "sys.exit")


class PurityTests(unittest.TestCase):

    def test_core_modules_contain_no_io_or_cli_constructs(self):
        for name in CORE_MODULES:
            source = (CORE_DIR / name).read_text(encoding="utf-8")
            # Strip docstrings/comments so prose mentioning a construct does
            # not fail the check — the assertion is about CODE.
            code = _strip_docs_and_comments(source)
            for token in FORBIDDEN_IN_CORE:
                self.assertNotIn(token, code, f"{name} contains {token!r}")

    def test_no_cfr_llm_api_key_anywhere_under_backend(self):
        # Assembled at runtime so THIS file is not itself a match (CFR-11).
        needle = "CFR" + "_LLM_API_KEY"
        hits = [str(p.relative_to(BACKEND_ROOT)) for p in BACKEND_ROOT.rglob("*.py")
                if needle in p.read_text(encoding="utf-8", errors="ignore")]
        self.assertEqual(hits, [], f"{needle} survived in {hits}")

    def test_core_imports_no_model_provider(self):
        """6-T5 (static half) — nothing on the run path can reach a model."""
        banned = re.compile(r"\b(import\s+(openai|anthropic)|from\s+(openai|anthropic|ai\.llm)\s+import"
                            r"|from\s+ai\s+import\s+llm)\b")
        for name in CORE_MODULES + ("binder.py",):
            source = (CORE_DIR / name).read_text(encoding="utf-8")
            self.assertIsNone(banned.search(source), f"{name} imports a model provider")
        for name in ("readiness.py", "manifest.py", "runner_cross_field.py"):
            source = (BACKEND_ROOT / "dq_diagnostics" / name).read_text(encoding="utf-8")
            self.assertIsNone(banned.search(source), f"{name} imports a model provider")

    def test_roles_module_holds_no_per_role_vocabulary_table(self):
        """DX-04 / mirrors 5-T8: no module-level dict literal shaped like
        ``{"role_name": {...}}`` with more than ten entries survives."""
        tree = ast.parse((CORE_DIR / "roles.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            string_keys = [k for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            dict_values = [v for v in node.values if isinstance(v, ast.Dict)]
            self.assertFalse(len(string_keys) > 10 and dict_values,
                             "roles.py contains a per-role vocabulary literal")

    def test_no_codegen_on_the_new_run_path(self):
        """6-T13 — no exec/eval/compile anywhere in dq_diagnostics or ingest."""
        offenders = []
        for root in ("dq_diagnostics", "ingest"):
            for path in (BACKEND_ROOT / root).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                            and node.func.id in {"exec", "eval", "compile"}:
                        offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_no_snippet_or_sandbox_mechanism_on_the_new_run_path(self):
        for path in (BACKEND_ROOT / "dq_diagnostics").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("code_sandbox", source, f"{path.name} reaches the snippet sandbox")
            self.assertNotIn("snippet_code", source, f"{path.name} reads a code blob")


def _strip_docs_and_comments(source: str) -> str:
    tree = ast.parse(source)
    doc_spans = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_spans.update(range(body[0].lineno, body[0].end_lineno + 1))
    out = []
    for i, line in enumerate(source.splitlines(), start=1):
        if i in doc_spans:
            continue
        stripped = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        out.append(stripped)
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════════════
#  6-T14 — decision-type shapes are structurally distinct
# ═══════════════════════════════════════════════════════════════════════════

class DecisionTypeShapeTests(unittest.TestCase):

    def test_a_cross_field_verdict_payload_is_rejected_as_a_candidate_flag(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="candidate_flag", diagnostic_id=4,
                             verdict="violation", metric=0.1667)

    def test_a_candidate_flag_payload_is_rejected_as_a_verdict(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="verdict", diagnostic_id=4,
                             review_state="open", metric=0.1667)

    def test_a_not_applicable_verdict_must_carry_its_reason(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="verdict", diagnostic_id=4, verdict="not_applicable")
        ok = DiagnosticResult(decision_type="verdict", diagnostic_id=4,
                              verdict="not_applicable",
                              na_reason="no knowledge base published for scope irb")
        self.assertEqual(ok.verdict, "not_applicable")

    def test_the_cross_field_verdict_shape_is_accepted(self):
        res = DiagnosticResult(decision_type="verdict", diagnostic_id=4, verdict="violation",
                               metric=0.1667, evidence={"rollup": {"VIOLATION": 1}})
        self.assertIsNone(res.review_state)


if __name__ == "__main__":
    unittest.main()
