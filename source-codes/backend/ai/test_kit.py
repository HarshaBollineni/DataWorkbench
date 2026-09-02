"""ai/test_kit.py — THE single governed helper catalogue (Phase 2 / PLT-08).

The name is kept (rather than renamed to something like ``ai/registry.py``)
because CFR-06/APL-37 specify that future primitives register in "ai/test_kit
.py's governed catalogue" — this IS that catalogue.

Design (deliberately small): one flat namespace of :class:`Helper` records.
Every helper is a plain, already-existing deterministic callable — this module
never reimplements analytics, it only federates:

  * gx metric primitives   (``gx/metrics.py``)      — kind="metric"
  * vetted RCA probes      (``ai/rca_helpers.HELPERS``) — kind="rca_helper"
  * role-gated tool wrappers (registered by ``ai/tool_registry.py`` when that
    module is imported — see its module docstring) — kind="tool"

Retired at this phase: the old catalogue additionally read a ``test_library``
table from ``system_db`` at import time (``_library_tools()``). That table
still exists but the read-and-wrap-as-a-tool behaviour is OUT of scope for
Phase 2 and is retired here; it belongs to the Test Lab work in a later phase
(not reintroduced without a fresh design pass) and does not run at import —
this module now does NO I/O at import time, by rule (plan 2.10/2-T1).

Every call goes through the ONE logging helper (``ai/_helper_log.py``): a
DEBUG line on logger "archimedes.helpers" with the helper name, a context id,
outcome (ok/error) and duration, so every helper invocation platform-wide is
observable the same way regardless of which of the three kinds it is.

# Phase 6 registers the nine cross-field primitives here (CFR-06).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from gx import metrics as _m
from domains.rca.analysis_helpers import HELPERS as _RCA_HELPERS
from ai._helper_log import logged_call


@dataclass(frozen=True)
class Helper:
    """One governed catalogue entry.

    ``meta`` is an opaque extension bucket — e.g. ``ai/tool_registry.py``
    stashes its role/egress/schema metadata there so IT needs no catalogue of
    its own (see that module's docstring). Nothing in this module reads
    ``meta``; it exists purely so wrappers can attach their own concerns
    without this module growing new fields per wrapper.
    """
    name: str
    fn: Callable[..., Any]
    purpose: str                      # one sentence: what this helper is for
    kind: str                         # "metric" | "rca_helper" | "tool"
    inputs: str                       # human-readable description of kwargs
    outputs: str                      # human-readable description of the return value
    failure_modes: str                # what can raise / how it fails, and why
    version: str = "1.0"
    meta: dict[str, Any] = field(default_factory=dict)


_CATALOGUE: dict[str, Helper] = {}


def register(helper: Helper) -> Helper:
    """Add ``helper`` to the single catalogue. Raises ``ValueError`` loudly if
    ``helper.name`` is already registered — silent overwrite is never allowed,
    so a copy-pasted registration collides instead of shadowing the original."""
    if helper.name in _CATALOGUE:
        raise ValueError(f"Helper already registered: {helper.name!r}")
    _CATALOGUE[helper.name] = helper
    return helper


def get_helper(name: str) -> Helper | None:
    """Return the :class:`Helper` for ``name``, or ``None`` if unregistered."""
    return _CATALOGUE.get(name)


def list_helpers() -> list[Helper]:
    """Every registered helper, in registration order."""
    return list(_CATALOGUE.values())


def call(name: str, *, context_id: str | None = None, **kwargs: Any) -> Any:
    """Invoke the registered helper ``name`` with ``kwargs``, through the one
    logging helper (DEBUG line on logger "archimedes.helpers" with ``name``,
    ``context_id`` or "-", outcome, duration_ms). Raises ``KeyError`` for an
    unknown name; re-raises whatever the helper itself raises, after logging
    ``outcome=error``.
    """
    helper = _CATALOGUE.get(name)
    if helper is None:
        raise KeyError(f"Unknown helper: {name!r}")
    return logged_call(helper.name, helper.fn, kwargs, context_id=context_id)


# ---------------------------------------------------------------------------
# 1. gx metric primitives (gx/metrics.py) — referenced, never copied.
# ---------------------------------------------------------------------------
def _metric(name: str, fn: Callable[..., Any], purpose: str, inputs: str, outputs: str,
            failure_modes: str = "Raises ValueError/TypeError on malformed inputs; "
                                  "see the function's own docstring in gx/metrics.py.") -> None:
    register(Helper(name, fn, purpose, "metric", inputs, outputs, failure_modes, version="1.0"))


_metric("psi", _m.compute_psi,
        "Population Stability Index of a feature between an early reference "
        "vintage and the current window.",
        "df, feature, date_col; optional ref_frac, bins, eps.",
        "float PSI (>0.20 is the calibrated FAIL threshold; see PSI_THRESHOLD).")
def _ks_2samp(a, b):
    """Two-sample Kolmogorov-Smirnov statistic (max CDF gap, pure-numpy, no
    scipy). Thin, behaviour-preserving pass-through to ``gx.metrics._ks_2samp``
    (a private, undocumented helper there) — documented here instead of in
    ``gx/metrics.py``, which is out of Phase 2's assigned file list."""
    return _m._ks_2samp(a, b)


_metric("ks_2samp", _ks_2samp,
        "Two-sample Kolmogorov-Smirnov statistic (pure-numpy, no scipy).",
        "a, b — two 1-D numeric arrays.",
        "float max CDF gap in [0, 1].")
_metric("ks_by_segment", _m.compute_ks_by_segment,
        "Max KS of a feature between the largest segment (baseline) and every "
        "other segment — a representativeness proxy.",
        "df, feature, segment_col.",
        "dict {ks, segment_col, baseline, worst}.")
_metric("missingness_rate", _m.missingness_rate,
        "Null-rate of a column, optionally scoped to a segment and/or a date window.",
        "df, column; optional segment_col, segment_value, date_col, date_start, date_end.",
        "dict {rate, n}.")
_metric("leakage", _m.detect_leakage,
        "Flags any post-outcome column that is present and non-null (target leakage).",
        "df, post_outcome_cols (list[str]).",
        "dict {leaked: bool, leaked_columns: list[str]}.")
_metric("regime_coverage", _m.downturn_regime_coverage,
        "Fraction of rows whose date falls in a downturn window (vacuous pass "
        "if the dataset's range doesn't overlap the window at all).",
        "df, date_col, downturn_start, downturn_end.",
        "dict {applicable: bool, coverage: float}.")
_metric("observation_window", _m.observation_window_months,
        "Inclusive whole-month span between min and max date (vintage depth).",
        "df, date_col.",
        "int months (>=24 is the calibrated minimum; see VINTAGE_MIN_MONTHS).")
_metric("target_rate_range", _m.target_rate_quarterly_range,
        "Range and std of the mean target rate across calendar quarters.",
        "df, target_col, date_col.",
        "dict {range, std}.")
_metric("spearman", _m.conditional_spearman,
        "Spearman rank correlation between two columns (pandas, no scipy).",
        "df, col_a, col_b.",
        "float rho in [-1, 1] (0.0 when fewer than 3 paired rows).")
_metric("relationship_holds", _m.relationship_holds,
        "Whether a Spearman rho confirms an expected monotone/inverse relationship.",
        "rho (float), relationship (str); optional min_strength.",
        "bool.",
        failure_modes="Raises ValueError for an unrecognized relationship name.")


# ---------------------------------------------------------------------------
# 2. Vetted RCA probes (ai/rca_helpers.HELPERS) — imported, never forked.
#    RCAHelper.fn takes (df, params); wrapped so the flat call(**kwargs)
#    convention still works — pass df= plus the probe's own params as kwargs.
# ---------------------------------------------------------------------------
def _wrap_rca(fn: Callable[[Any, dict[str, Any]], dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def _call(*, df: Any, **params: Any) -> dict[str, Any]:
        return fn(df, params)
    _call.__doc__ = fn.__doc__
    return _call


for _hid, _h in _RCA_HELPERS.items():
    register(Helper(
        name=_hid,
        fn=_wrap_rca(_h.fn),
        purpose=_h.description,
        kind="rca_helper",
        inputs=f"df (DataFrame) plus: {_h.param_schema}",
        outputs="RCA result dict: summary/metrics/evidence_rows/"
                "interpretation_hints/recommended_followups (see "
                "domains.rca.analysis_helpers.OUTPUT_SCHEMA).",
        failure_modes="Raises ValueError on missing/invalid columns or params — "
                      "see the probe's own docstring in ai/rca_helpers.py.",
        version="1.0",
    ))
del _hid, _h


# ---------------------------------------------------------------------------
# 3. Cross-field primitives (CFR-06) — the nine that KB-08 binds to and that
#    APL-20 will compile to, plus `evaluate_rule`, the orchestrator (a tenth
#    catalogue entry, not a tenth primitive). Referenced from
#    dq_diagnostics/engines/cross_field/, never reimplemented here.
#
#    Each of the nine is a FACTORY: it takes resolved column names and
#    returns a row predicate returning True / False / None, where None means
#    "missing or censored — skipped, never a violation" (CFR-07).
# ---------------------------------------------------------------------------
from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import primitives as _cf  # noqa: E402

_CF_KIND = "cross_field_primitive"
_CF_TRISTATE = ("Row predicate returning True (holds) / False (violated) / "
                "None (missing or censored -> skipped).")


def _cross_field(name: str, fn: Callable[..., Any], purpose: str, inputs: str,
                 outputs: str = _CF_TRISTATE,
                 failure_modes: str = "Raises KeyError for an unsupported operator; "
                                       "never raises on missing data (returns None instead).") -> None:
    register(Helper(name, fn, purpose, _CF_KIND, inputs, outputs, failure_modes, version="1.0"))


_cross_field("ineq", _cf.ineq,
             "Numeric comparison of a column against another column or a literal, "
             "with an absolute tolerance and an optional scale/offset on the right side.",
             "colA, op ('>=','<=','>','<','==','!='), colB_or_lit ('#<number>' for a literal); "
             "optional tol, factor, offset.")
_cross_field("dateorder", _cf.dateorder,
             "Chronology: one date column must not fall after another.",
             "earlier, later; optional allow_equal (default True).")
_cross_field("identity", _cf.identity,
             "A derived value must equal its formula within a tolerance.",
             "target, formula (row callable built from declarative params), tol.")
_cross_field("dom_range", _cf.dom_range,
             "Inclusive numeric bound; either end may be None for unbounded.",
             "col, lo, hi.")
_cross_field("dom_set", _cf.dom_set,
             "Membership of an enumerated domain, with numeric coercion so {0,1} "
             "matches 0.0/1.0 and '0'/'1'.",
             "col, allowed (list/set).")
_cross_field("presence", _cf.presence,
             "All of the given columns are populated (or, with must=False, none are).",
             "cols (list), must (default True).")
_cross_field("eq_cond", _cf.eq_cond,
             "IF-condition: the column is populated and equals a value (scope selector).",
             "col, val.",
             outputs="Row predicate returning True/False (scope membership).")
_cross_field("in_cond", _cf.in_cond,
             "IF-condition: the column is populated and is one of a set of values.",
             "col, vals (list/set).",
             outputs="Row predicate returning True/False (scope membership).")
_cross_field("present_number", _cf.present_number,
             "Expression: the column is populated.",
             "col.",
             outputs="Row predicate returning True/False.")
register(Helper(
    "evaluate_rule", _cf.evaluate_rule,
    "Orchestrator (not a primitive): evaluates one bound cross-field rule over the "
    "resolved dataset — scope, censoring skips, exceptions, verdict gate and the "
    "clustered/scattered pattern read.",
    _CF_KIND,
    "rule (Rule), resolved (role -> RoleMatch), binding (entity -> table), tables "
    "(name -> DataFrame), grain_role, segment_role, cluster_lift, cluster_coverage.",
    "RuleResult: outcome PASS/VIOLATION/NOT-APPLICABLE with scope, exception, "
    "violation, evidence and pattern accounting (CFR-07..CFR-09).",
    "Raises rules.BindingError if the rule's binding params cannot be assembled; "
    "data problems become NOT-APPLICABLE with a stated reason, never an exception.",
    version="1.0",
))
