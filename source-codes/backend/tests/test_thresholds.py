"""Phase 3 — semantic layer behavioral tests (3-T5, FWK-06).

Thresholds are DATA in threshold_settings, never a Python constant; a
consumer function reads them through `effective_threshold` and its
OUTPUT changes after `set_threshold` with zero code changes to the
consumer itself. Precedence is engagement > dimension > default.

Standalone-runnable (``python -m unittest tests.test_thresholds``); same
SYSTEM_DB_PATH-before-first-import sandboxing convention as
test_admin_reset.py / test_taxonomy.py.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-thresholds.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
from dq_diagnostics import register  # noqa: E402
from dq_diagnostics.thresholds import ThresholdNotFoundError, effective_threshold, set_threshold  # noqa: E402


def _consumer(engagement: str | None = None, dimension: str | None = None) -> dict:
    """Stands in for a real diagnostic engine: it reads the live tolerance
    for diagnostic #4 through the semantic layer ONLY. The test proves
    this function's output changes after set_threshold() writes new data
    — with NO edit to this function's own code."""
    return effective_threshold(4, "tolerance", dimension=dimension, engagement=engagement)


class ThresholdDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        register.seed_register()

    def test_default_thresholds_are_seeded_from_the_framework_data(self):
        self.assertEqual(effective_threshold(4, "tolerance"), {"value": 0.0, "source": "default"})
        self.assertEqual(effective_threshold(14, "psi_watch"), {"value": 0.10, "source": "default"})
        self.assertEqual(effective_threshold(14, "psi_investigate"), {"value": 0.25, "source": "default"})

    def test_unknown_key_raises_threshold_not_found_which_is_a_keyerror(self):
        with self.assertRaises(ThresholdNotFoundError):
            effective_threshold(4, "no_such_key_at_all")
        with self.assertRaises(KeyError):
            effective_threshold(4, "no_such_key_at_all")


class ConsumerFlipsWithNoCodeChangeTests(unittest.TestCase):
    """3-T5's central proof: the semantic layer, not code, controls the
    live value."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        register.seed_register()

    def test_consumer_output_flips_after_set_threshold_same_consumer_code(self):
        eng = f"eng-flip-{uuid.uuid4().hex[:8]}"

        before = _consumer(engagement=eng)
        self.assertEqual(before, {"value": 0.0, "source": "default"})

        set_threshold(4, "tolerance", 0.05, "engagement", eng, actor="t")

        after = _consumer(engagement=eng)
        self.assertEqual(after, {"value": 0.05, "source": f"engagement ({eng})"})
        self.assertNotEqual(before, after)

        # A different, untouched engagement still sees the plain default —
        # a scoped tune is never a global side effect.
        untouched_eng = f"eng-untouched-{uuid.uuid4().hex[:8]}"
        self.assertEqual(_consumer(engagement=untouched_eng), {"value": 0.0, "source": "default"})

    def test_audit_row_written_to_transaction_log_on_set_threshold(self):
        eng = f"eng-audit-{uuid.uuid4().hex[:8]}"
        matching_before = [r for r in s.query("transaction_log", event="set_threshold")
                           if r["payload"].get("scope_ref") == eng]
        self.assertEqual(matching_before, [])

        set_threshold(4, "tolerance", 0.07, "engagement", eng, actor="audit-tester")

        matching_after = [r for r in s.query("transaction_log", event="set_threshold")
                          if r["payload"].get("scope_ref") == eng]
        self.assertEqual(len(matching_after), 1)
        audit_row = matching_after[0]
        self.assertEqual(audit_row["actor"], "audit-tester")
        self.assertEqual(audit_row["payload"]["diagnostic_id"], 4)
        self.assertEqual(audit_row["payload"]["key"], "tolerance")
        self.assertEqual(audit_row["payload"]["value"], 0.07)
        self.assertEqual(audit_row["payload"]["scope"], "engagement")


class PrecedenceTests(unittest.TestCase):
    """engagement > dimension > default, proven with three rows for the
    same (diagnostic_id, key)."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        register.seed_register()

    def test_precedence_engagement_over_dimension_over_default(self):
        dim = f"dim-{uuid.uuid4().hex[:8]}"
        eng = f"eng-{uuid.uuid4().hex[:8]}"

        # Row 1 (already seeded): the plain default.
        self.assertEqual(effective_threshold(14, "psi_watch", dimension=dim, engagement=eng),
                         {"value": 0.10, "source": "default"})

        # Row 2: a dimension-scoped override beats the default.
        set_threshold(14, "psi_watch", 0.15, "dimension", dim, actor="t")
        self.assertEqual(effective_threshold(14, "psi_watch", dimension=dim, engagement=eng),
                         {"value": 0.15, "source": f"dimension ({dim})"})

        # Row 3: an engagement-scoped override beats the dimension row too.
        set_threshold(14, "psi_watch", 0.20, "engagement", eng, actor="t")
        self.assertEqual(effective_threshold(14, "psi_watch", dimension=dim, engagement=eng),
                         {"value": 0.20, "source": f"engagement ({eng})"})

        # A caller that never names this dimension/engagement still gets
        # the plain default — the overrides are scoped, never global.
        self.assertEqual(effective_threshold(14, "psi_watch"), {"value": 0.10, "source": "default"})

    def test_set_threshold_rejects_an_invalid_scope(self):
        with self.assertRaises(ValueError):
            set_threshold(4, "tolerance", 1, "bogus_scope", None, actor="t")

    def test_no_threshold_value_is_a_hardcoded_python_constant_in_this_module(self):
        """A light structural guard for the FWK-06 rule itself: the
        thresholds module's source contains no bare numeric threshold
        literal outside of scope-name plumbing."""
        import inspect

        import dq_diagnostics.thresholds as thresholds_module
        src = inspect.getsource(thresholds_module)
        # The module may reference 0 only as a fallback list index / no
        # magic threshold floats (0.05, 0.9, 0.25, ...) are permitted.
        import re
        floats = re.findall(r"\b0\.\d+\b", src)
        self.assertEqual(floats, [], f"found hardcoded float constant(s) in thresholds.py: {floats}")


if __name__ == "__main__":
    unittest.main()
