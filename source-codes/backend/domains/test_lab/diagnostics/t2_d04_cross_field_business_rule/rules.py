"""CFR-02/CFR-06 — the cross-field rule model and the nine primitives.

A faithful port of S5 (``cross_field_rule_engine.py`` sections 2 and the
primitive block) with exactly two deliberate differences, both required by
0.4.0's own rules:

1. **No built-in KB.** S5's ``build_generic_kb()`` hardcoded 49 rules and
   carried a ``build=lambda m: ...`` closure per rule. Rules here are
   constructed from published, *bound* ``kb_rules`` rows (KB-01/KB-09,
   CFR-03) and their predicates are assembled by :func:`build_predicates`
   from the declarative ``binding_params_json`` captured at binding time —
   never from a callable stored in, or a string compiled out of, the KB.
   There is no ``eval``/``exec``/``compile`` on this path (6-T13).
2. **``ineq`` gains ``factor``/``offset``.** S9 states bounds of the form
   ``exposure_at_default <= original_balance * 1.5``; S5 expressed those
   with a bespoke per-rule lambda, which cannot survive rule 1. Defaults
   (``factor=1.0``, ``offset=0.0``) make the primitive behaviourally
   identical to S5's for every other call.

Purity (CFR-01, 6-T8): no print/input/argparse/open/sys.exit anywhere in
this module — it is called by the engine, which is called by the API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import pandas as pd

RULE_TYPES = ("inequality", "date_ordering", "identity", "conditional", "domain")
SEVERITIES = ("CRITICAL", "MATERIAL", "MINOR")

# CFR-08 — severity orders the report; it never changes pass/fail maths.
SEVERITY_RANK = {"CRITICAL": 0, "MATERIAL": 1, "MINOR": 2}


def severity_rank(severity: str | None) -> int:
    return SEVERITY_RANK.get(severity or "", 3)


# ============================================================================
#  Value coercion (S5 section 0, verbatim)
# ============================================================================

def is_missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(v, str) and v.strip().lower() in ("", "nan", "none", "null", "na", "n/a"):
        return True
    return False


def to_number(v: Any) -> float | None:
    if is_missing(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_date(v: Any) -> datetime | None:
    if is_missing(v):
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y",
                "%Y/%m/%d", "%Y-%m", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ============================================================================
#  The rule record
# ============================================================================

@dataclass(frozen=True)
class Rule:
    """One executable cross-field rule, projected from a bound ``kb_rules`` row.

    ``tolerance`` is the *violation-rate* tolerance and comes from the
    semantic layer (FWK-06 / ``threshold_settings``), never from the rule
    text — a numeric tolerance INSIDE a predicate (identity's ``+/- 0.01``)
    lives in ``params`` instead.
    """

    id: str
    rule_type: str
    description: str
    roles: tuple[str, ...]
    primitive: str
    params: dict[str, Any]
    entity: str | None = None
    framework: str | None = None
    severity: str = "MATERIAL"
    tolerance: float = 0.0
    exceptions_spec: tuple[tuple[str, Any], ...] = ()
    exception_notes: tuple[str, ...] = ()
    regulatory_ref: str | None = None
    kb_rule_id: str | None = None
    source_ref: str | None = None

    @property
    def condition_spec(self) -> dict[str, Any] | None:
        return self.params.get("condition")

    @property
    def expression_spec(self) -> dict[str, Any]:
        return self.params.get("expression") or {}


@dataclass
class RuleResult:
    """S5's RuleResult, unchanged in shape (CFR-07's scope accounting)."""

    rule: Rule
    outcome: str
    scope_n: int = 0
    excepted_n: int = 0
    skipped_semantics_n: int = 0
    violation_n: int = 0
    violation_rate: float = 0.0
    examples: list = field(default_factory=list)
    pattern: str | None = None
    pattern_detail: str = ""
    na_reason: str = ""
    resolved_map: dict = field(default_factory=dict)
    tables_used: str = ""


# ============================================================================
#  SECTION 2.  The nine primitives (CFR-06) — S5 verbatim
# ============================================================================

def always(_row: dict) -> bool:
    """The vacuous IF-condition (S5's ``_always``): every row is in scope."""
    return True


def ineq(colA, op, colB_or_lit, tol=0.0, factor=1.0, offset=0.0):
    """``colA <op> colB_or_lit`` with an absolute tolerance.

    ``colB_or_lit`` is a column name, or a literal written ``"#<number>"``
    (S5's convention). ``factor``/``offset`` scale the right-hand side
    (``b*factor + offset``) so a KB bound of the form ``a <= b * 1.5``
    binds without a bespoke predicate; at their defaults this is S5's
    ``ineq`` exactly. Returns ``None`` when either side is missing or
    non-numeric — the engine counts that row as skipped/censored, never as
    a violation.
    """
    lit = colB_or_lit[1:] if isinstance(colB_or_lit, str) and colB_or_lit.startswith("#") else None
    lit = float(lit) if lit is not None else None

    def _e(row):
        a = to_number(row.get(colA))
        b = lit if lit is not None else to_number(row.get(colB_or_lit))
        if a is None or b is None:
            return None
        b = b * factor + offset
        return {">=": a >= b - tol, "<=": a <= b + tol, ">": a > b - tol,
                "<": a < b + tol, "==": abs(a - b) <= tol,
                "!=": abs(a - b) > tol}[op]
    return _e


def dateorder(earlier, later, allow_equal=True):
    """``earlier <= later`` (or ``<`` when ``allow_equal`` is False).
    ``None`` when either date is missing or unparseable."""
    def _e(row):
        a, b = to_date(row.get(earlier)), to_date(row.get(later))
        if a is None or b is None:
            return None
        return a <= b if allow_equal else a < b
    return _e


def identity(target, formula, tol):
    """``|target - formula(row)| <= tol``. ``formula`` is a row callable
    built by :func:`make_formula` from declarative params — never compiled
    from KB text."""
    def _e(row):
        t = to_number(row.get(target))
        f = formula(row)
        if t is None or f is None:
            return None
        return abs(t - f) <= tol
    return _e


def dom_range(col, lo, hi):
    """Inclusive numeric bound; ``None`` for either end means unbounded."""
    def _e(row):
        x = to_number(row.get(col))
        if x is None:
            return None
        if lo is not None and x < lo:
            return False
        if hi is not None and x > hi:
            return False
        return True
    return _e


def dom_set(col, allowed):
    """Membership of an enumerated domain. Missing -> ``None`` (skipped)."""
    def _e(row):
        v = row.get(col)
        if is_missing(v):
            return None
        if v in allowed:
            return True
        # A numeric domain written {0,1} must still match a float column
        # (0.0/1.0) and a text column ("0"/"1") — a coercion, not a
        # widening of the domain.
        n = to_number(v)
        if n is not None:
            for a in allowed:
                an = to_number(a)
                if an is not None and an == n:
                    return True
        return False
    return _e


def presence(cols, must=True):
    """All of ``cols`` populated (``must``) or none of them."""
    def _e(row):
        pres = [not is_missing(row.get(c)) for c in cols]
        return all(pres) if must else (not any(pres))
    return _e


def eq_cond(col, val):
    """IF-condition: ``col`` is populated and equals ``val``."""
    def _c(row):
        return not is_missing(row.get(col)) and row.get(col) == val
    return _c


def in_cond(col, vals):
    """IF-condition: ``col`` is populated and is one of ``vals``."""
    def _c(row):
        if is_missing(row.get(col)):
            return False
        v = row.get(col)
        if v in vals:
            return True
        n = to_number(v)
        if n is None:
            return False
        return any(to_number(a) == n for a in vals if to_number(a) is not None)
    return _c


def present_number(col):
    """Expression: ``col`` is populated (S5's presence-of-one shorthand)."""
    def _e(row):
        return not is_missing(row.get(col))
    return _e


def eq_cond_num(col, val):
    """Internal detail of :func:`eq_cond` (S5 keeps it separate, CFR-06 keeps
    it unregistered): numeric equality so ``1`` matches ``1.0``."""
    def _c(row):
        x = to_number(row.get(col))
        return x is not None and x == val
    return _c


# The nine registered primitives (CFR-06). ``evaluate_rule`` — the tenth
# catalogue entry — is the orchestrator and lives in engine.py.
PRIMITIVES: dict[str, Callable[..., Any]] = {
    "ineq": ineq,
    "dateorder": dateorder,
    "identity": identity,
    "dom_range": dom_range,
    "dom_set": dom_set,
    "presence": presence,
    "eq_cond": eq_cond,
    "in_cond": in_cond,
    "present_number": present_number,
}

# Which of the nine may act as an IF-condition vs. a violation expression.
CONDITION_PRIMITIVES = ("eq_cond", "in_cond", "ineq")
EXPRESSION_PRIMITIVES = ("ineq", "dateorder", "identity", "dom_range", "dom_set",
                         "presence", "present_number", "in_cond")


# ============================================================================
#  Declarative predicate assembly (replaces S5's per-rule build= lambda)
# ============================================================================

class BindingError(ValueError):
    """A binding spec that cannot be assembled into a predicate — a
    programming/parse error, never a data verdict."""


def make_formula(spec: dict[str, Any], role_to_col: dict[str, str]) -> Callable[[dict], float | None]:
    """Build an identity rule's right-hand side from declarative params.

    ``{"operands": ["a", "b"], "operator": "/", "factor": 100.0}`` becomes
    ``a / b * 100.0``, returning ``None`` when an operand is missing or the
    divisor is zero (S5's own guard). Supported operators: ``/ * + -`` and
    a single-operand pass-through. No expression text is ever evaluated.
    """
    operands = [role_to_col[r] for r in spec.get("operands", [])]
    operator = spec.get("operator")
    factor = float(spec.get("factor", 1.0))
    offset = float(spec.get("offset", 0.0))
    if not operands:
        raise BindingError("identity formula needs at least one operand")

    def _f(row):
        values = [to_number(row.get(c)) for c in operands]
        if any(v is None for v in values):
            return None
        acc = values[0]
        for v in values[1:]:
            if operator == "/":
                if v == 0:
                    return None
                acc = acc / v
            elif operator == "*":
                acc = acc * v
            elif operator == "+":
                acc = acc + v
            elif operator == "-":
                acc = acc - v
            else:
                raise BindingError(f"unsupported identity operator: {operator!r}")
        return acc * factor + offset
    return _f


def _build_one(spec: dict[str, Any], role_to_col: dict[str, str]) -> Callable[[dict], Any]:
    """Assemble ONE primitive call from its declarative spec."""
    primitive = spec.get("primitive")
    if primitive not in PRIMITIVES:
        raise BindingError(f"unknown primitive: {primitive!r}")

    def col(key: str) -> str:
        role = spec.get(key)
        if role not in role_to_col:
            raise BindingError(f"role {role!r} not resolved for primitive {primitive!r}")
        return role_to_col[role]

    if primitive == "ineq":
        right = spec.get("right_role")
        right_arg = role_to_col[right] if right else f"#{float(spec['right_literal'])}"
        return ineq(col("left"), spec["op"], right_arg,
                    tol=float(spec.get("tolerance", 0.0)),
                    factor=float(spec.get("factor", 1.0)),
                    offset=float(spec.get("offset", 0.0)))
    if primitive == "dateorder":
        return dateorder(col("earlier"), col("later"),
                         allow_equal=bool(spec.get("allow_equal", True)))
    if primitive == "identity":
        return identity(col("target"), make_formula(spec, role_to_col),
                        float(spec.get("tolerance", 0.0)))
    if primitive == "dom_range":
        lo, hi = spec.get("lo"), spec.get("hi")
        return dom_range(col("col"), None if lo is None else float(lo),
                         None if hi is None else float(hi))
    if primitive == "dom_set":
        return dom_set(col("col"), list(spec.get("allowed", [])))
    if primitive == "presence":
        cols = [role_to_col[r] for r in spec.get("cols", [])]
        if not cols:
            raise BindingError("presence needs at least one role")
        return presence(cols, must=bool(spec.get("must", True)))
    if primitive == "present_number":
        return present_number(col("col"))
    if primitive == "eq_cond":
        if spec.get("numeric"):
            return eq_cond_num(col("col"), spec["value"])
        return eq_cond(col("col"), spec["value"])
    if primitive == "in_cond":
        return in_cond(col("col"), list(spec.get("values", [])))
    raise BindingError(f"unhandled primitive: {primitive!r}")  # pragma: no cover


def build_predicates(rule: Rule, role_to_col: dict[str, str]) -> tuple[Callable, Callable]:
    """S5's ``rule.build(role_to_col)`` -> ``(condition_fn, expression_fn)``.

    The condition is *truthified* (``None`` -> out of scope), matching S5's
    hand-written conditions, which all guarded on a value being present
    before comparing. The expression keeps tri-state semantics: ``None``
    means the row is skipped as missing/censored (CFR-07), never violating.
    """
    expression = _build_one(rule.expression_spec, role_to_col)
    cond_spec = rule.condition_spec
    if not cond_spec:
        return always, expression
    raw = _build_one(cond_spec, role_to_col)

    def condition(row):
        return bool(raw(row))
    return condition, expression
