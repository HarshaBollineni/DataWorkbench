"""Deterministic readiness per (item, diagnostic) — testlab-redesign §5 / §3.

The Coverage board is a *reading* surface: the user decides nothing there.
Everything it shows about whether a diagnostic can run on this item is
computed here, from data, with a stated reason for every refusal
(FWK-14/15: honesty over completeness).

Four board states, three of which come from here:

  ``ready``           preconditions met for this item
  ``not_applicable``  executable, but nothing in scope to run — e.g. no
                      published + bound KB rules for the item's use case
                      (CFR-04's own wording: "no knowledge base published
                      for scope <use_case>"). Never PASS, never silence.
  ``blocked``         executable and in scope, but a named precondition
                      fails (dataset not ingested, nothing profiled)
  ``workflow_pending``   NOT produced here — it is a register state
                      (FWK-17) answered by ``register.require_executable``.

No I/O beyond the system DB; no dataset rows are read (readiness must be
cheap enough to evaluate for all nine cards on every board load).
"""
from __future__ import annotations

import re
from typing import Any

import system_db as s

from .engines.base import Readiness
from .register import get_diagnostic

# The two regulatory scopes S9's rule table declares, plus the union. A
# rule's own framework label and an item's use case are both normalized
# into this vocabulary; anything unrecognized falls back to the documented
# default (``both``) rather than silently dropping rules.
USE_CASES = ("irb", "ifrs9", "both")
DEFAULT_USE_CASE = "both"

# These registered diagnostic families consume an outcome/target field. The
# readiness layer owns the refusal so it remains a NOT-APPLICABLE verdict,
# using the same vocabulary as every other pre-run refusal.
TARGET_REQUIRED_DIAGNOSTICS = frozenset({2, 11, 17})


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def normalize_use_case(raw: str | None) -> tuple[str, str]:
    """``"IRB / Basel"`` -> ``("irb", "item use_case")``.

    Returns ``(scope, source)``. An item whose use case names neither
    regulatory framework (e.g. "Stress Testing") gets the documented
    default ``both`` with source ``"default"`` — the caller records a
    ``default_applied`` decision for it (CFR-12).
    """
    token = _norm(raw)
    if not token:
        return DEFAULT_USE_CASE, "default"
    has_irb = "irb" in token
    has_ifrs = "ifrs" in token
    if has_irb and has_ifrs:
        return "both", "item use_case"
    if has_ifrs:
        return "ifrs9", "item use_case"
    if has_irb:
        return "irb", "item use_case"
    return DEFAULT_USE_CASE, "default"


def normalize_framework(raw: str | None) -> str | None:
    """A KB rule's framework label -> ``irb`` / ``ifrs9`` / ``both`` /
    ``None`` (no label recovered — the rule is unscoped and applies to
    every use case, which is reported rather than assumed away)."""
    token = _norm(raw)
    if not token:
        return None
    has_irb = "irb" in token
    has_ifrs = "ifrs" in token
    if token == "both" or (has_irb and has_ifrs):
        return "both"
    if has_ifrs:
        return "ifrs9"
    if has_irb:
        return "irb"
    return None


def rule_in_scope(rule: dict[str, Any], use_case: str) -> bool:
    """CFR-03 — S5's ``filter_kb_by_framework`` semantics: a rule runs when
    any of its framework tokens is in the chosen scope; a ``both`` rule runs
    under either; an unlabelled rule is unscoped and always runs."""
    framework = normalize_framework(rule.get("framework"))
    if framework is None or framework == "both" or use_case == "both":
        return True
    return framework == use_case


def eligible_cross_field_rules(tenant_id: str, use_case: str) -> tuple[list[dict], dict[str, int]]:
    """Published + bound + in-scope rules for diagnostic #4, plus the counts
    the manifest and the readiness reason both quote.

    Retrieval goes through ``kb.list_eligible_rules(require_bound=True)``
    (KB-12/16 — the module never queries kb_rules for rules of its own);
    the framework/use-case filter on top of it is CFR-03's and lives here.
    """
    import kb
    manifest = kb.list_eligible_rules(tenant_id, "cross_field_engine", require_bound=True)
    published_bound = manifest["rules"]
    in_scope = [r for r in published_bound if rule_in_scope(r, use_case)]
    counts = {
        "published_bound": len(published_bound),
        "in_scope": len(in_scope),
        "filtered_out_by_framework": len(published_bound) - len(in_scope),
    }
    return in_scope, counts


def _item_tables(item_id: str) -> list[dict[str, Any]]:
    return s.query("dq_item_tables", item_id=item_id, order_by="table_name")


def readiness(item_id: str, diagnostic_id: int, tenant_id: str = "bootstrap") -> Readiness:
    """The board's per-card verdict. Raises ``KeyError`` for an unregistered
    diagnostic and ``WorkflowPendingError`` for a registered-but-pending one
    (FWK-17) — the caller renders those, they are not readiness states.
    """
    from .register import require_executable
    require_executable(diagnostic_id)          # raises WorkflowPendingError / KeyError
    get_diagnostic(diagnostic_id)

    item = s.query_one("dq_items", item_id=item_id)
    if not item:
        raise KeyError(f"Unknown item: {item_id}")

    status = item.get("ingest_status")
    if status != "ready":
        return Readiness("blocked",
                         f"dataset is not ingested yet (ingest status: {status or 'unknown'})",
                         {"ingest_status": status})
    tables = _item_tables(item_id)
    if not tables:
        return Readiness("blocked", "dataset has no profiled tables to run against",
                         {"tables": 0})

    if diagnostic_id in TARGET_REQUIRED_DIAGNOSTICS and not item.get("target_variable"):
        return Readiness(
            "not_applicable",
            "target variable is not selected; set Target Variable in Data Sourcing before running this diagnostic",
            {"required_field": "target_variable", "where_to_set": "Data Sourcing"},
        )

    if diagnostic_id == 2:
        target = item.get("target_variable")
        inventory = s.query("variable_inventory", item_id=item_id)
        target_rows = [row for row in inventory if row.get("column_name") == target]
        target_tables = sorted({row.get("table_name") for row in target_rows if row.get("table_name")})
        if len(target_tables) != 1:
            return Readiness(
                "blocked", "confirmed target does not resolve to exactly one profiled table",
                {"target": target, "tables": target_tables},
            )
        from domains.test_lab.diagnostics.t1_d02_feature_target_separation.roles import is_eligible_feature
        eligible = [
            row["column_name"] for row in inventory
            if row.get("table_name") == target_tables[0]
            and row.get("column_name") != target
            and is_eligible_feature(row)
        ]
        if not eligible:
            return Readiness(
                "not_applicable", "no independent variables remain after excluding the confirmed target",
                {"target": target, "table": target_tables[0], "eligible_features": 0},
            )
        return Readiness(
            "ready", None,
            {"target": target, "table": target_tables[0], "eligible_features": len(eligible),
             "summary": f"{len(eligible)} selectable feature(s) against confirmed target {target}"},
        )

    if diagnostic_id == 14:
        inventory = s.query("variable_inventory", item_id=item_id)
        usable = sorted({row["column_name"] for row in inventory
                         if row.get("column_name") and str(row.get("role") or "").lower()
                         not in {"identifier", "primary_key", "foreign_key", "free_text"}})
        if not usable:
            return Readiness("not_applicable", "no PSI-compatible features are profiled",
                             {"eligible_features": 0})
        return Readiness("ready", None, {"eligible_features": len(usable),
                         "population_modes": ["one_snapshot", "two_snapshot"],
                         "summary": f"{len(usable)} feature(s) available for population comparison"})

    if diagnostic_id == 6:
        inventory = s.query("variable_inventory", item_id=item_id)
        by_table: dict[str, list[str]] = {}
        for row in inventory:
            if row.get("table_name") and row.get("column_name"):
                by_table.setdefault(row["table_name"], []).append(row["column_name"])
        eligible = {table: columns for table, columns in by_table.items() if len(columns) >= 2}
        if not eligible:
            return Readiness("not_applicable",
                             "row completeness requires a table with facility and reporting-period columns",
                             {"eligible_tables": 0})
        return Readiness("ready", None, {"eligible_tables": sorted(eligible),
                         "segment_optional": True,
                         "summary": "Configure a facility identifier, reporting period, grain, and one continuity floor."})

    if diagnostic_id == 11:
        target = item.get("target_variable")
        inventory = s.query("variable_inventory", item_id=item_id)
        target_rows = [row for row in inventory if row.get("column_name") == target]
        target_tables = sorted({row.get("table_name") for row in target_rows if row.get("table_name")})
        if len(target_tables) != 1:
            return Readiness("blocked", "confirmed target does not resolve to exactly one profiled table",
                             {"target": target, "tables": target_tables})
        rows = [row for row in inventory if row.get("table_name") == target_tables[0]
                and row.get("column_name") != target]
        numeric = [row for row in rows if any(token in str(row.get("data_type") or "").lower()
                   for token in ("int", "float", "double", "decimal", "numeric", "number"))]
        if not numeric:
            return Readiness("not_applicable", "no numeric independent variables are available for directionality",
                             {"target": target, "table": target_tables[0], "eligible_features": 0})
        return Readiness("ready", None, {"target": target, "table": target_tables[0],
                         "eligible_features": len(numeric), "segment_optional": True,
                         "summary": f"{len(numeric)} numeric feature(s) available for directionality review"})

    if diagnostic_id != 4:
        # Every other executable diagnostic would answer for itself through
        # its own engine; none exists in slice 1 and the register says so,
        # so require_executable above has already refused.
        return Readiness("blocked", "no engine is registered for this diagnostic",
                         {"diagnostic_id": diagnostic_id})

    use_case, use_case_source = normalize_use_case(item.get("use_case"))
    rules, counts = eligible_cross_field_rules(tenant_id, use_case)
    detail = {
        "use_case": use_case, "use_case_source": use_case_source,
        "tables": [t["table_name"] for t in tables],
        **counts,
    }
    if not rules:
        # CFR-04 verbatim: never PASS, never a generic failure — a stated
        # NOT-APPLICABLE naming the scope that has no knowledge behind it.
        return Readiness("not_applicable",
                         f"no knowledge base published for scope {use_case}", detail)
    severities: dict[str, int] = {}
    for r in rules:
        severities[r.get("severity") or "MATERIAL"] = severities.get(r.get("severity") or "MATERIAL", 0) + 1
    detail["by_severity"] = dict(sorted(severities.items()))
    return Readiness("ready",
                     None,
                     {**detail,
                      "summary": f"{len(rules)} bound rules in scope ({use_case})"})
