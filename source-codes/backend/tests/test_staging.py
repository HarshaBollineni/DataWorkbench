"""Phase 3 — staging/guard-chain behavioral tests (3-T6, 3-T7) plus the
FWK-07 DiagnosticResult contract.

docs/0.4.0/03-staging-contract.md: Stage 1 runs before Stage 2 (FWK-08); a
Stage-2 diagnostic whose dependency is unmet REFUSES rather than being
silently reordered or skipped (FWK-10); every statistical screen must be
invoked through a genuine GuardedScope minted by the FWK-09 guard chain,
in order (class eligibility -> material fields -> value semantics), or it
is a structural error (3-T6).

Standalone-runnable (``python -m unittest tests.test_staging``); same
SYSTEM_DB_PATH-before-first-import sandboxing convention as the sibling
Phase 3 test files (importing dq_diagnostics transitively imports
system_db through dq_diagnostics.register).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-staging.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

from dq_diagnostics.guards import (  # noqa: E402
    GuardedScope,
    GuardUnavailableError,
    apply_guards,
    class_eligibility,
)
from dq_diagnostics.result import DiagnosticResult, InvalidDiagnosticResultError  # noqa: E402
from dq_diagnostics.runner import execute_statistical, run_stage_ordered  # noqa: E402


class StageOrderingAndRefusalTests(unittest.TestCase):
    """3-T7 — Stage 1 before Stage 2; a Stage-2 diagnostic missing its
    declared dependency is refused in place, never silently reordered or
    dropped."""

    def test_mixed_list_runs_stage_one_before_stage_two_and_refuses_the_gap(self):
        executed: list[int] = []

        def execute_fn(diagnostic, ctx):
            executed.append(diagnostic["diagnostic_id"])
            return "ok"

        # Deliberately listed Stage-2-first in the INPUT, to prove the
        # runner — not the caller — imposes the ordering.
        diagnostics = [
            {"diagnostic_id": 2, "stage": "Stage 2", "depends_on": ["value_semantics_tags"]},
            {"diagnostic_id": 6, "stage": "Stage 1"},
            {"diagnostic_id": 4, "stage": "Both"},
        ]
        results = run_stage_ordered(diagnostics, {"available_dependencies": set()}, execute_fn)

        # Stage-1-first ordering: #6 (Stage 1) and #4 (Both, counts as
        # stage 1) both precede #2 (Stage 2) in the output.
        ordered_ids = [r["diagnostic_id"] for r in results]
        self.assertLess(ordered_ids.index(6), ordered_ids.index(2))
        self.assertLess(ordered_ids.index(4), ordered_ids.index(2))

        # The Stage-2 diagnostic with the missing dependency is REFUSED —
        # present in the output (not dropped), never executed.
        refused = next(r for r in results if r["diagnostic_id"] == 2)
        self.assertIn("refused", refused)
        self.assertIn("missing dependency", refused["refused"])
        self.assertIn("value_semantics_tags", refused["refused"])
        self.assertNotIn(2, executed, "a refused diagnostic must never reach execute_fn")

        # Stage 1 / Both diagnostics, having no unmet dependency, actually ran.
        self.assertIn(6, executed)
        self.assertIn(4, executed)

    def test_stage_two_diagnostic_runs_once_its_dependency_becomes_available(self):
        """The same fixture, but with the dependency now marked available —
        proves the refusal is data-driven, not a permanent block."""
        executed: list[int] = []

        def execute_fn(diagnostic, ctx):
            executed.append(diagnostic["diagnostic_id"])
            return "ok"

        diagnostics = [{"diagnostic_id": 2, "stage": "Stage 2", "depends_on": ["value_semantics_tags"]}]
        results = run_stage_ordered(
            diagnostics, {"available_dependencies": {"value_semantics_tags"}}, execute_fn
        )
        self.assertEqual(results, [{"diagnostic_id": 2, "result": "ok"}])
        self.assertIn(2, executed)


class GuardChainStructuralTests(unittest.TestCase):
    """3-T6 — the guard-order skeleton cannot be bypassed."""

    def test_execute_statistical_rejects_a_hand_built_stand_in(self):
        for fake_scope in ({"eligible_columns": [], "excluded": []}, object(), None, "not a scope"):
            with self.assertRaises(TypeError):
                execute_statistical(lambda scope: "should not run", fake_scope)

    def test_guarded_scope_cannot_be_constructed_directly(self):
        with self.assertRaises(TypeError):
            GuardedScope(eligible_columns=[], excluded=[], _mint_key=object())
        with self.assertRaises(TypeError):
            GuardedScope(eligible_columns=[], excluded=[], _mint_key=None)

    def test_apply_guards_fails_on_material_fields_before_reaching_value_semantics(self):
        """Slice 1 has no producer for either guard 2 or guard 3 — but the
        ORDER must still be class-eligibility -> material-fields ->
        value-semantics. Asserting on the exact message proves it failed
        at material_fields, not value_semantics (i.e. value_semantics was
        never even reached)."""
        with self.assertRaises(GuardUnavailableError) as ctx:
            apply_guards(["a", "b"], {"a": "numerical", "b": "numerical"}, {"numerical"})
        message = str(ctx.exception)
        self.assertIn("producer not yet available", message)
        self.assertIn("materiality derivation", message)
        self.assertNotIn("value-semantics", message)

    def test_class_eligibility_excludes_incompatible_classes_with_reasons(self):
        eligible, excluded = class_eligibility(
            ["a", "b", "c"],
            {"a": "numerical", "b": "categorical", "c": "free_text"},
            {"numerical"},
        )
        self.assertEqual(eligible, ["a"])
        self.assertEqual(len(excluded), 2)
        excluded_cols = {e["column"] for e in excluded}
        self.assertEqual(excluded_cols, {"b", "c"})
        for entry in excluded:
            self.assertTrue(entry["reason"], f"exclusion of {entry['column']} must carry a reason")

    def test_execute_statistical_runs_a_genuine_guarded_scope(self):
        """The positive path: a GuardedScope minted honestly (bypassing the
        unavailable guards 2/3 only for this structural test, via direct
        construction through the module's own mint key) is accepted."""
        import dq_diagnostics.guards as guards_module

        scope = GuardedScope(eligible_columns=["a"], excluded=[], _mint_key=guards_module._MINT)
        result = execute_statistical(lambda s: list(s.eligible_columns), scope)
        self.assertEqual(result, ["a"])


class DiagnosticResultContractTests(unittest.TestCase):
    """FWK-07 — every engine's result is decision-type-shaped."""

    def test_missing_decision_type_raises(self):
        with self.assertRaises(TypeError):
            DiagnosticResult()  # decision_type has no default

    def test_invalid_decision_type_value_raises(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="not_a_real_type")
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type=None)

    def test_verdict_result_without_verdict_raises(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="verdict")

    def test_verdict_result_with_invalid_verdict_value_raises(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="verdict", verdict="maybe")

    def test_not_applicable_without_na_reason_raises(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="verdict", verdict="not_applicable")

    def test_not_applicable_with_na_reason_succeeds(self):
        result = DiagnosticResult(decision_type="verdict", verdict="not_applicable",
                                  na_reason="open workouts are censored by construction")
        self.assertEqual(result.verdict, "not_applicable")

    def test_candidate_flag_without_review_state_raises(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="candidate_flag")

    def test_contextual_without_review_state_raises(self):
        with self.assertRaises(InvalidDiagnosticResultError):
            DiagnosticResult(decision_type="contextual")

    def test_valid_shapes_for_every_decision_type(self):
        DiagnosticResult(decision_type="verdict", verdict="pass")
        DiagnosticResult(decision_type="verdict", verdict="violation")
        DiagnosticResult(decision_type="candidate_flag", review_state="open")
        DiagnosticResult(decision_type="contextual", review_state="confirmed")
        DiagnosticResult(decision_type="classification_output")
        DiagnosticResult(decision_type="sme_gate")


if __name__ == "__main__":
    unittest.main()
