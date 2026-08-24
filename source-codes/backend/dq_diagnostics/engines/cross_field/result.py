"""CFR-10 — the structured result is primary; every rendering derives from it.

S6 (``report.txt``) is a *rendering*. This module holds the structure it
renders from: preamble, roll-up, and one entry per rule in one of three
shapes (VIOLATION / PASS / NOT-APPLICABLE), ordered violations-first by
severity. :meth:`StructuredResult.to_dict` is the canonical serialization —
byte-identical across two runs of one frozen manifest on one data snapshot
(CFR-16, 6-T7) — and :func:`render_text` / :func:`render_pdf` are pure
derivations of it.

Purity (CFR-01, 6-T8): no print/input/argparse/open/sys.exit. ``render_pdf``
returns bytes; writing them anywhere is the API layer's job.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from .rules import RuleResult, severity_rank

OUTCOMES = ("VIOLATION", "PASS", "NOT-APPLICABLE")


def jsonable(value: Any) -> Any:
    """Coerce a cell value into something JSON-stable (evidence rows carry
    raw dataset values). NaN/NaT become ``None`` so a serialized result is
    valid JSON and two runs compare equal."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    for attr in ("item", "isoformat"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return jsonable(fn())
            except (TypeError, ValueError):
                break
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


@dataclass
class StructuredResult:
    preamble: dict[str, Any] = field(default_factory=dict)
    rollup: dict[str, int] = field(default_factory=dict)
    rules: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"preamble": self.preamble, "rollup": self.rollup, "rules": self.rules}

    def to_json(self) -> str:
        """Canonical serialization: sorted keys, fixed separators. Two runs
        on one frozen manifest produce byte-identical output (6-T7)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)

    def by_outcome(self, outcome: str) -> list[dict[str, Any]]:
        return [r for r in self.rules if r["outcome"] == outcome]

    def severity_counts(self) -> dict[str, dict[str, int]]:
        """``{severity: {PASS, VIOLATION, NOT-APPLICABLE}}`` — the roll-up the
        score panel needs (FWK-16), computed from the same structure."""
        out: dict[str, dict[str, int]] = {}
        for entry in self.rules:
            sev = entry.get("severity") or "MATERIAL"
            bucket = out.setdefault(sev, {o: 0 for o in OUTCOMES})
            bucket[entry["outcome"]] += 1
        return out


def _entry_for(res: RuleResult, evidence_cap: int) -> dict[str, Any]:
    rule = res.rule
    common = {
        "outcome": res.outcome,
        "rule_id": rule.id,
        "kb_rule_id": rule.kb_rule_id,
        "severity": rule.severity,
        "framework": rule.framework,
        "rule_type": rule.rule_type,
        "rule_text": rule.description,
        "entity": rule.entity,
    }
    if res.outcome == "NOT-APPLICABLE":
        # CFR-04/CFR-08 — a reason is mandatory and is never hidden.
        return {**common, "na_reason": res.na_reason,
                "resolved_roles": dict(sorted(res.resolved_map.items()))}
    if res.outcome == "PASS":
        return {**common,
                "scope_rows_evaluated": res.scope_n,
                "scope_rows_skipped": res.skipped_semantics_n,
                "violation_count": 0,
                "rate": 0.0,
                "tolerance": rule.tolerance,
                "exceptions_applied": res.excepted_n,
                "resolved_roles": dict(sorted(res.resolved_map.items()))}
    entry = {
        **common,
        "regulatory_ref": rule.regulatory_ref,
        "resolved_roles": dict(sorted(res.resolved_map.items())),
        "tables_used": res.tables_used,
        "scope_rows_evaluated": res.scope_n,
        "scope_rows_skipped": res.skipped_semantics_n,
        "scope_skipped_reason": "missing/censored" if res.skipped_semantics_n else None,
        "violation_count": res.violation_n,
        "rate": res.violation_rate,
        "tolerance": rule.tolerance,
        "exceptions_applied": res.excepted_n,
        # "silent exceptions are how rules rot" (CFR-07): the count NEVER
        # travels without its stated reason.
        "exception_notes": list(rule.exception_notes),
        "evidence": [{k: jsonable(v) for k, v in ex.items()} for ex in res.examples[:evidence_cap]],
        "pattern": res.pattern,
        "pattern_detail": res.pattern_detail,
    }
    return entry


def build_structured_result(results: list[RuleResult], preamble: dict[str, Any],
                            evidence_cap: int = 5) -> StructuredResult:
    """Assemble the CFR-10 structure from per-rule results.

    Ordering (CFR-10): violations first, by severity CRITICAL -> MATERIAL ->
    MINOR, then by descending rate, then rule id; then passes by rule id;
    then NOT-APPLICABLE by rule id. Deterministic in every tie.
    """
    violations = sorted((r for r in results if r.outcome == "VIOLATION"),
                        key=lambda r: (severity_rank(r.rule.severity), -r.violation_rate, r.rule.id))
    passes = sorted((r for r in results if r.outcome == "PASS"), key=lambda r: r.rule.id)
    na = sorted((r for r in results if r.outcome == "NOT-APPLICABLE"), key=lambda r: r.rule.id)
    rollup = {"PASS": len(passes), "VIOLATION": len(violations), "NOT-APPLICABLE": len(na)}
    entries = [_entry_for(r, evidence_cap) for r in (violations + passes + na)]
    return StructuredResult(preamble=preamble, rollup=rollup, rules=entries)


# ============================================================================
#  Renderings — derived, never a second source of truth
# ============================================================================

_LINE = "=" * 78
_THIN = "-" * 78


def render_text(result: StructuredResult) -> str:
    """S6's report layout, rebuilt from the structure alone."""
    p = result.preamble
    params = p.get("parameters", {})
    L: list[str] = []
    W = L.append
    W(_LINE)
    W("CROSS-FIELD BUSINESS RULE TEST  --  REPORT")
    W(_LINE)
    W("\nPREAMBLE\n" + _THIN)
    W(f"  Rule-set source   : {p.get('kb_source', '(unknown)')}")
    W(f"  Framework scope   : {p.get('use_case', '(unspecified)')}")
    W(f"  Dataset           : {p.get('dataset', '(unknown)')}")
    W(f"  Rules loaded      : {p.get('rules_loaded', 0)}")
    by_type = p.get("by_type") or {}
    W("  Rules by type     : " + (", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "(none)"))
    W(f"  Roles resolved    : {p.get('roles_resolved', 0)}/{p.get('roles_total', 0)} "
      f"({p.get('override_count', 0)} override, {p.get('auto_count', 0)} auto)")
    W(f"  Pattern knobs     : cluster_lift={_val(params.get('cluster_lift'))}, "
      f"cluster_coverage={_val(params.get('cluster_coverage'))}")
    W(f"  Tolerance         : {_val(params.get('tolerance'))}")
    W("")
    W("DATASET ROLL-UP\n" + _THIN)
    W(f"  PASS           : {result.rollup.get('PASS', 0)}")
    W(f"  VIOLATION      : {result.rollup.get('VIOLATION', 0)}")
    W(f"  NOT-APPLICABLE : {result.rollup.get('NOT-APPLICABLE', 0)}\n")

    violations = result.by_outcome("VIOLATION")
    if violations:
        W("VIOLATIONS  (verdicts, ordered by severity)\n" + _LINE)
        for r in violations:
            W(f"[{r['severity']}] {r['rule_id']}  ({r.get('framework')} / {r.get('rule_type')})")
            W(f"   Rule      : {r.get('rule_text')}")
            if r.get("regulatory_ref"):
                W(f"   Reg ref   : {r['regulatory_ref']}")
            W("   Resolved  : " + ", ".join(f"{k}->{v}" for k, v in (r.get("resolved_roles") or {}).items()))
            skipped = r.get("scope_rows_skipped") or 0
            W(f"   Scope     : {r.get('scope_rows_evaluated', 0)} rows evaluated"
              + (f", {skipped} skipped ({r.get('scope_skipped_reason')})" if skipped else ""))
            W(f"   Violations: {r.get('violation_count', 0)}  "
              f"(rate {r.get('rate', 0.0):.4%}, tol {r.get('tolerance')})")
            if r.get("exceptions_applied"):
                W(f"   Exceptions applied: {r['exceptions_applied']}")
                for note in r.get("exception_notes") or []:
                    W(f"       - {note}")
            if r.get("pattern"):
                W(f"   Pattern   : {r.get('pattern_detail')}")
            W("   Evidence  (up to 5 rows):")
            for ex in r.get("evidence") or []:
                W("       - " + ", ".join(f"{k}={v}" for k, v in ex.items()))
            W("")
    else:
        W("VIOLATIONS\n" + _THIN + "\n  None.\n")

    passes = result.by_outcome("PASS")
    if passes:
        W("PASSES\n" + _THIN)
        for r in passes:
            W(f"  [{r['severity']}] {r['rule_id']}  scope={r.get('scope_rows_evaluated', 0)}  "
              f"viol=0  ({r.get('rule_text')})")
        W("")
    na = result.by_outcome("NOT-APPLICABLE")
    if na:
        W("NOT-APPLICABLE  (stated, never hidden)\n" + _THIN)
        for r in na:
            W(f"  {r['rule_id']}  ({r.get('framework')})  -> {r.get('na_reason')}")
        W("")
    W(_LINE)
    W("End of report.  Evidence rows available in full on request.")
    W(_LINE)
    return "\n".join(L)


def _val(v: Any) -> str:
    return "(unset)" if v is None else str(v)


def render_pdf(result: StructuredResult) -> bytes:
    """The same structure as a PDF. Returns bytes — this module never writes
    a file (CFR-01). Falls back to the text rendering encoded as UTF-8 when
    the PDF library is unavailable, so a report is always obtainable."""
    try:
        from fpdf import FPDF
    except ImportError:  # pragma: no cover - fpdf ships with the backend
        return render_text(result).encode("utf-8")
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=10, top=10, right=10)
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Courier", "", 7)
    for line in render_text(result).splitlines():
        safe = line.encode("latin-1", "replace").decode("latin-1")
        # new_x/new_y keep every line starting at the left margin; without
        # them fpdf2 leaves x at the right edge and the next full-width cell
        # has no room left at all.
        pdf.multi_cell(0, 3.2, safe or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
