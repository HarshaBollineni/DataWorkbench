"""Diagnostic #4 — cross-field business rule engine (CFR-01..CFR-18).

A port of S5 (`cross_field_rule_engine.py`) as a PURE core: rules and roles
come from the published knowledge base and the ingested dictionary, never
from code (KB-01/DX-04); nothing here prints, prompts, parses argv, opens a
file or exits; progress is an injected callback and the structured result is
the single source every rendering derives from (CFR-10).

Layout:
  ``rules.py``   the rule record, the nine primitives, declarative predicate
                 assembly (no eval/exec anywhere)
  ``primitives.py`` re-export of the nine, for the governed catalogue
  ``roles.py``   S5's scoring ladder over a KB-derived vocabulary (DX-04)
  ``engine.py``  ``evaluate_rule`` / ``violation_pattern`` / ``execute_run``
  ``result.py``  the CFR-10 structured result + its text/PDF renderings
  ``binder.py``  KB rule -> primitive signature matcher (writes ``kb_rules``;
                 the only non-pure module here, and never on the run path)
"""
from __future__ import annotations

from .engine import EVIDENCE_CAP, evaluate_rule, execute_run, iter_run, violation_pattern
from .result import StructuredResult, build_structured_result, render_pdf, render_text
from .roles import RoleMatch, bind_entities, derive_vocabulary, resolve_roles, score_column
from .rules import (
    PRIMITIVES,
    RULE_TYPES,
    SEVERITIES,
    Rule,
    RuleResult,
    build_predicates,
    severity_rank,
)

ENGINE_VERSION = "cross_field/1.0.0"

__all__ = [
    "ENGINE_VERSION",
    "EVIDENCE_CAP",
    "PRIMITIVES",
    "RULE_TYPES",
    "SEVERITIES",
    "Rule",
    "RuleResult",
    "RoleMatch",
    "StructuredResult",
    "bind_entities",
    "build_predicates",
    "build_structured_result",
    "derive_vocabulary",
    "evaluate_rule",
    "execute_run",
    "iter_run",
    "render_pdf",
    "render_text",
    "resolve_roles",
    "score_column",
    "severity_rank",
    "violation_pattern",
]
