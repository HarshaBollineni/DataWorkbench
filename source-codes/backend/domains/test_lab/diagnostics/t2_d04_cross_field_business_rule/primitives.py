"""CFR-06 — the nine primitives, re-exported.

Plan 6.1 names a ``primitives.py``; the implementations live in
``rules.py`` next to the :class:`~.rules.Rule` they build predicates for,
so there is exactly ONE definition of each primitive. This module is a
pure re-export so `from ... import primitives` reads naturally and the
catalogue registration (``ai/test_kit.py``) has one obvious import site.

``evaluate_rule`` — the tenth catalogue entry, the orchestrator rather
than a primitive — lives in ``engine.py`` and is re-exported here too.
"""
from __future__ import annotations

from .rules import (  # noqa: F401
    CONDITION_PRIMITIVES,
    EXPRESSION_PRIMITIVES,
    PRIMITIVES,
    dateorder,
    dom_range,
    dom_set,
    eq_cond,
    identity,
    in_cond,
    ineq,
    presence,
    present_number,
)

__all__ = [
    "PRIMITIVES", "CONDITION_PRIMITIVES", "EXPRESSION_PRIMITIVES",
    "ineq", "dateorder", "identity", "dom_range", "dom_set",
    "presence", "eq_cond", "in_cond", "present_number", "evaluate_rule",
]


def __getattr__(name: str):
    # Deferred so importing the primitives never drags in the engine (and
    # pandas' join machinery) when a caller only wants the predicates.
    if name == "evaluate_rule":
        from .engine import evaluate_rule
        return evaluate_rule
    raise AttributeError(name)
