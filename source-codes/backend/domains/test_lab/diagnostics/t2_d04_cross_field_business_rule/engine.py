"""CFR-07/08/09 — evaluation, verdict gate and pattern read.

A verbatim port of S5 section 7 (``evaluate_rule`` / ``violation_pattern``),
with the per-rule ``build=`` closure replaced by the declarative assembly in
``rules.build_predicates`` (KB-09: a predicate recovered from prose is never
executed — only a signature-matched binding is).

Semantics preserved exactly:

* any role unmapped                       -> NOT-APPLICABLE ("role 'x' unmapped")
* a rule spanning >1 table                -> left-join on the grain role's
  column, first-match dedup on the right side; an unresolvable join is
  NOT-APPLICABLE with the reason, never a silent skip
* rows where the IF-condition is false    -> out of scope; empty scope is
  NOT-APPLICABLE ("empty scope (no rows meet IF-condition)"), never PASS
* expression -> ``None``                  -> skipped as missing/censored;
  all-skipped is NOT-APPLICABLE ("all in-scope rows had missing/censored
  values"). This IS slice 1's censoring mechanism (staging contract §3) —
  the rule's own encoded exceptions and verdict logic, not FWK-10/11's
  general gate, which no statistical engine is wired to yet.
* exceptions are applied BEFORE a row counts as a violation, and are
  counted and reported with their stated reason (CFR-07)
* rate = violations / evaluated; ``rate <= tolerance`` -> PASS else VIOLATION
* evidence: at most 5 rows, keyed by the grain column when resolved (CFR-15)
* pattern: lift-based CLUSTERED vs SCATTERED on the segment role (CFR-09)

Purity (CFR-01, 6-T8): no print/input/argparse/open/sys.exit; progress is
an injected callback.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Generator
from typing import Any, Callable

from .result import StructuredResult, build_structured_result
from .roles import RoleMatch, bind_entities
from .rules import Rule, RuleResult, build_predicates, eq_cond

EVIDENCE_CAP = 5


def evaluate_rule(rule: Rule, resolved: dict[str, RoleMatch], binding: dict[str, str],
                  tables: dict[str, Any], grain_role: str, segment_role: str,
                  cluster_lift: float, cluster_coverage: float) -> RuleResult:
    """Evaluate ONE rule against the resolved dataset. S5 section 7, ported.

    ``binding`` (entity -> table, from the vote in
    :func:`roles.bind_entities`) is carried for provenance exactly as S5
    carries it; the join itself is driven by each role's own resolved
    table, which is what that vote produced.
    """
    role_to_col: dict[str, str] = {}
    for role in rule.roles:
        m = resolved.get(role)
        if not m or not m.column:
            return RuleResult(rule=rule, outcome="NOT-APPLICABLE",
                              na_reason=f"role '{role}' unmapped")
        role_to_col[role] = m.column

    role_tables = {role: resolved[role].table for role in rule.roles}
    needed_tables = set(role_tables.values())

    if len(needed_tables) == 1:
        table = next(iter(needed_tables))
        if table not in tables:
            return RuleResult(rule=rule, outcome="NOT-APPLICABLE", resolved_map=role_to_col,
                              na_reason=f"table '{table}' is not in scope for this run")
        df = tables[table]
    else:
        gm = resolved.get(grain_role)
        if not gm or not gm.column:
            return RuleResult(rule=rule, outcome="NOT-APPLICABLE", resolved_map=role_to_col,
                              na_reason="cross-table rule but grain key unresolved; cannot join")
        key = gm.column
        base_t = role_tables[rule.roles[0]]
        if base_t not in tables:
            return RuleResult(rule=rule, outcome="NOT-APPLICABLE", resolved_map=role_to_col,
                              na_reason=f"table '{base_t}' is not in scope for this run")
        if key not in tables[base_t].columns:
            return RuleResult(rule=rule, outcome="NOT-APPLICABLE", resolved_map=role_to_col,
                              na_reason=f"grain key '{key}' absent in base table '{base_t}'")
        df = tables[base_t].copy()
        joined = [base_t]
        for role in rule.roles:
            rt = role_tables[role]
            col = role_to_col[role]
            if rt == base_t or col in df.columns:
                continue
            right_df = tables.get(rt)
            if right_df is None or key not in right_df.columns or col not in right_df.columns:
                return RuleResult(rule=rule, outcome="NOT-APPLICABLE", resolved_map=role_to_col,
                                  na_reason=f"role '{role}' in '{rt}' not joinable on '{key}'")
            right = right_df[[key, col]].dropna(subset=[key]).groupby(key, as_index=False).first()
            df = df.merge(right, on=key, how="left")
            if rt not in joined:
                joined.append(rt)
        table = "+".join(joined)

    missing = [c for c in role_to_col.values() if c not in df.columns]
    if missing:
        return RuleResult(rule=rule, outcome="NOT-APPLICABLE", resolved_map=role_to_col,
                          tables_used=table,
                          na_reason=f"column(s) absent in {table}: {', '.join(sorted(set(missing)))}")

    condition, expression = build_predicates(rule, role_to_col)
    exc_fns = [eq_cond(role_to_col[r], v) for (r, v) in rule.exceptions_spec if r in role_to_col]

    grain_match = resolved.get(grain_role)
    seg_match = resolved.get(segment_role)
    grain_col = grain_match.column if grain_match else None
    seg_col = seg_match.column if seg_match else None

    records = df.to_dict("records")
    scope = [row for row in records if condition(row)]
    if not scope:
        return RuleResult(rule=rule, outcome="NOT-APPLICABLE", tables_used=table,
                          na_reason="empty scope (no rows meet IF-condition)",
                          resolved_map=role_to_col)

    violations: list[dict] = []
    excepted = evaluated = skipped = 0
    for row in scope:
        v = expression(row)
        if v is None:
            skipped += 1
            continue
        evaluated += 1
        if v is True:
            continue
        if any(fn(row) for fn in exc_fns):
            excepted += 1
            continue
        violations.append(row)

    if evaluated == 0:
        return RuleResult(rule=rule, outcome="NOT-APPLICABLE", scope_n=len(scope),
                          skipped_semantics_n=skipped, resolved_map=role_to_col,
                          tables_used=table,
                          na_reason="all in-scope rows had missing/censored values")

    rate = len(violations) / evaluated
    outcome = "PASS" if rate <= rule.tolerance else "VIOLATION"
    examples = []
    for row in violations[:EVIDENCE_CAP]:
        ex: dict[str, Any] = {}
        if grain_col and grain_col in row:
            ex[grain_col] = row[grain_col]
        for c in role_to_col.values():
            ex[c] = row.get(c)
        examples.append(ex)

    res = RuleResult(rule=rule, outcome=outcome, scope_n=evaluated, excepted_n=excepted,
                     skipped_semantics_n=skipped, violation_n=len(violations),
                     violation_rate=rate, examples=examples, resolved_map=role_to_col,
                     tables_used=table)
    if outcome == "VIOLATION" and seg_col:
        res.pattern, res.pattern_detail = violation_pattern(
            scope, violations, seg_col, cluster_lift, cluster_coverage)
    return res


def violation_pattern(scope: list[dict], violations: list[dict], seg_col: str,
                      cluster_lift: float, cluster_coverage: float) -> tuple[str | None, str]:
    """CFR-09 — CLUSTERED vs SCATTERED by lift, S5 verbatim.

    ``lift[level] = (violations_at_level / total_violations) /
    (scope_at_level / total_scope)``. CLUSTERED requires the TOP lift to
    reach ``cluster_lift`` AND the high-lift levels together to cover at
    least ``cluster_coverage`` of the violations; otherwise SCATTERED.
    """
    st, vt = len(scope), len(violations)
    if st == 0 or vt == 0 or not seg_col:
        return None, "no axis"
    sc = Counter(r.get(seg_col) for r in scope)
    vc = Counter(r.get(seg_col) for r in violations)
    lifts = {lvl: (v / vt) / (sc.get(lvl, 0) / st) if sc.get(lvl, 0) else float("inf")
             for lvl, v in vc.items()}
    if not lifts:
        return None, "no axis"
    top = max(lifts, key=lifts.get)
    high = [lvl for lvl, lf in lifts.items() if lf >= cluster_lift]
    cover = sum(vc[lvl] for lvl in high) / vt
    if lifts[top] >= cluster_lift and cover >= cluster_coverage:
        return "CLUSTERED", (f"CLUSTERED on '{seg_col}': top {top!r} lift={lifts[top]:.1f}; "
                             f"high-lift levels cover {cover:.0%} (lift>={cluster_lift}, "
                             f"coverage>={cluster_coverage}). Suggests a feed/segment fault.")
    return "SCATTERED", (f"SCATTERED on '{seg_col}': top {top!r} lift={lifts[top]:.1f}, "
                         f"coverage {cover:.0%} (lift>={cluster_lift}, coverage>={cluster_coverage}). "
                         f"Suggests capture error.")


# ============================================================================
#  Run orchestration (S5's main(), minus every interactive / file-IO step)
# ============================================================================

def iter_run(rules: list[Rule], resolved: dict[str, RoleMatch],
             vocab: dict[str, dict[str, Any]], tables: dict[str, Any],
             thresholds: dict[str, Any], preamble: dict[str, Any] | None = None,
             evidence_cap: int = EVIDENCE_CAP) -> Generator[dict[str, Any], None, StructuredResult]:
    """Evaluate every rule, YIELDING one progress dict per rule and
    RETURNING the CFR-10 structured result.

    A generator so the SSE layer can stream ``progress`` frames as they
    happen without a thread, and :func:`execute_run` can drain it for
    callers that just want the result. Nothing here writes to the dataset
    (CFR-15) — ``tables`` is read, copied for joins, never persisted back.
    """
    meta = preamble or {}
    binding = bind_entities(resolved, vocab)
    grain_role = thresholds["grain_role"]
    segment_role = thresholds["segment_role"]
    cluster_lift = float(thresholds["cluster_lift"])
    cluster_coverage = float(thresholds["cluster_coverage"])

    total = len(rules)
    results: list[RuleResult] = []
    for index, rule in enumerate(rules, start=1):
        res = evaluate_rule(rule, resolved, binding, tables, grain_role, segment_role,
                            cluster_lift, cluster_coverage)
        results.append(res)
        yield {"phase": "progress", "done": index, "total": total,
               "rule_id": rule.id, "severity": rule.severity, "outcome": res.outcome}

    head = {
        "kb_source": meta.get("kb_source"),
        "kb_documents": meta.get("kb_documents", []),
        "use_case": meta.get("use_case"),
        "dataset": meta.get("dataset"),
        "item_id": meta.get("item_id"),
        "tables": meta.get("tables", sorted(tables)),
        "rules_loaded": total,
        "by_type": dict(sorted(Counter(r.rule_type for r in rules).items())),
        "by_severity": dict(sorted(Counter(r.severity for r in rules).items())),
        "roles_total": len(resolved),
        "roles_resolved": sum(1 for m in resolved.values() if m.column),
        "override_count": sum(1 for m in resolved.values() if m.via == "override"),
        "auto_count": sum(1 for m in resolved.values() if m.via == "auto"),
        "unresolved_roles": sorted(r for r, m in resolved.items() if not m.column),
        "entity_binding": dict(sorted(binding.items())),
        "parameters": {k: thresholds[k] for k in sorted(thresholds)},
        "parameter_sources": dict(sorted((meta.get("parameter_sources") or {}).items())),
        # CFR-11 / D-08: verification provenance never enters evaluation.
        # A verified manifest therefore has the same deterministic result
        # bytes as its unverified counterpart.
        "role_verification": meta.get("role_verification", {"enabled": False}),
    }
    return build_structured_result(results, head, evidence_cap=evidence_cap)


def execute_run(rules: list[Rule], resolved: dict[str, RoleMatch],
                vocab: dict[str, dict[str, Any]], tables: dict[str, Any],
                thresholds: dict[str, Any], preamble: dict[str, Any] | None = None,
                emit: Callable[[dict], None] | None = None,
                evidence_cap: int = EVIDENCE_CAP) -> StructuredResult:
    """Drain :func:`iter_run`, handing each progress dict to ``emit``.

    The convenience form for callers that want the result rather than a
    stream (tests, the PDF report, a future batch runner).
    """
    runner = iter_run(rules, resolved, vocab, tables, thresholds, preamble, evidence_cap)
    while True:
        try:
            event = next(runner)
        except StopIteration as stop:
            return stop.value
        if emit is not None:
            emit(event)
