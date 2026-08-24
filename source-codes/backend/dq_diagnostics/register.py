"""FWK-04/17/18 — the diagnostic register + framework taxonomy, seeded as
DATA (docs/0.4.0/00-framework.md is the source of truth; this module loads
it, upserts the reference tables, and is the ONLY place that parses
dq_framework_data.json — every other module reads the DB through the
query API below, never the JSON file directly).

`seed_register()` is the boot-time entrypoint (wired into main.py the same
way seed_dq_framework()/seed_platform_and_taxonomy() are today); it is
idempotent, safe to call on every boot.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import system_db as s

_KB_DIR = Path(__file__).resolve().parent.parent / "knowledge_base"
_FRAMEWORK_DATA_PATH = _KB_DIR / "dq_framework_data.json"

# FWK-17 — the exact refusal text for a registered-but-not-yet-executable
# diagnostic. Never rendered as broken/failed/missing.
REFUSAL_WORKFLOW_PENDING = "workflow not yet defined"


class WorkflowPendingError(RuntimeError):
    """Raised by require_executable() for a diagnostic whose workflow_status
    is 'workflow_pending' — a known, registered diagnostic that simply has
    no implementation yet (FWK-17), never confused with an unknown one."""


def _load_framework_data() -> dict[str, Any]:
    return json.loads(_FRAMEWORK_DATA_PATH.read_text(encoding="utf-8"))


def seed_register(force: bool = False) -> dict[str, int]:
    """Seed diagnostic_register / framework_taxonomy / framework_test_areas
    from dq_framework_data.json, plus the default threshold rows.

    Idempotent: the three reference tables are upserted every call (safe
    every boot; the ids are the stable natural keys — diagnostic_id / l2_id
    / area_id). threshold_settings default rows are inserted ONLY if none
    exists yet for (diagnostic_id, key, scope='default') — re-seeding must
    never duplicate a default row, nor clobber the audit history of a
    value an operator has since re-tuned via thresholds.set_threshold().

    `force` is accepted for symmetry with the other seeders in seeds/; the
    reference-table upserts are unconditionally safe to repeat either way,
    so it is not currently load-bearing.
    """
    data = _load_framework_data()
    now = s.now_ist()

    coverage_by_l2 = {row["l2_id"]: row for row in data["coverage"]}
    for area in data["l2_areas"]:
        cov = coverage_by_l2.get(area["l2_id"], {})
        s.upsert("framework_taxonomy", {
            "l2_id": area["l2_id"],
            "l1_theme": area["l1_theme"],
            "name": area["name"],
            "objective": area["objective"],
            "why_it_matters": area["why_it_matters"],
            "coverage_status": cov.get("status"),
            "coverage_reason": cov.get("reason"),
            "coverage_diagnostics_json": cov.get("diagnostics", []),
        })

    for area in data["test_areas"]:
        s.upsert("framework_test_areas", {
            "area_id": area["area_id"],
            "name": area["name"],
            "stage": area["stage"],
            "kb_dependency": area["kb_dependency"],
            "plan_a_dimensions": area["plan_a_dimensions"],
        })

    for diag in data["register"]:
        s.upsert("diagnostic_register", {
            "diagnostic_id": diag["diagnostic_id"],
            "area": diag["area"],
            "mode": diag["mode"],
            "name": diag["name"],
            "what_it_computes": diag["what_it_computes"],
            "metric": diag["metric"],
            "threshold_based": diag["threshold_based"],
            "threshold_rule_default": diag["threshold_rule_default"],
            "det_stat": diag["det_stat"],
            "decision_type": diag["decision_type"],
            "stage": diag["stage"],
            "kb_dependency": diag["kb_dependency"],
            "workflow_status": diag["workflow_status"],
            "l2_areas_json": diag["l2_areas"],
            "enabled_by": diag.get("enabled_by"),
            "updated_at": now,
        })

    thresholds_inserted = 0
    for t in data.get("threshold_defaults", []):
        existing = s.query_one("threshold_settings", diagnostic_id=t["diagnostic_id"],
                                key=t["key"], scope="default")
        if existing is None:
            s.insert("threshold_settings", {
                "diagnostic_id": t["diagnostic_id"], "key": t["key"], "value_json": t["value"],
                "scope": "default", "scope_ref": None, "actor": "seed_register", "ts": now,
            })
            thresholds_inserted += 1

    return {
        "l2_areas": len(data["l2_areas"]),
        "test_areas": len(data["test_areas"]),
        "register": len(data["register"]),
        "threshold_defaults_inserted": thresholds_inserted,
    }


def list_register() -> list[dict[str, Any]]:
    """All registered diagnostic rows (9 in 0.4.0), ordered by diagnostic_id."""
    return s.query("diagnostic_register", order_by="diagnostic_id")


def get_diagnostic(diagnostic_id: int) -> dict[str, Any]:
    """A single register row.

    Raises ``KeyError("unknown diagnostic: <id>")`` for ANY id that is not
    one of the 9 registered rows — an S8 defer-row id (e.g. 3) and pure
    nonsense (e.g. 999) are indistinguishable (FWK-05, D-17): same
    exception type, same message shape.
    """
    row = s.query_one("diagnostic_register", diagnostic_id=diagnostic_id)
    if row is None:
        raise KeyError(f"unknown diagnostic: {diagnostic_id}")
    return row


def require_executable(diagnostic_id: int) -> dict[str, Any]:
    """Return the register row only if workflow_status == 'executable'.

    Raises `WorkflowPendingError(REFUSAL_WORKFLOW_PENDING)` for a
    registered-but-pending diagnostic (FWK-17 — a refusal, never rendered
    as broken/failed/missing) or `KeyError` if the id isn't registered.
    """
    row = get_diagnostic(diagnostic_id)
    if row["workflow_status"] != "executable":
        raise WorkflowPendingError(REFUSAL_WORKFLOW_PENDING)
    return row


def coverage_map() -> dict[str, Any]:
    """The 11-area coverage board (status/reason/registered diagnostics per
    L2 area) plus the register split into executable vs workflow-pending
    (FWK-17 is a diagnostic-level state, distinct from area-level coverage
    — #20/T6 has no L2 of its own and is not counted in either area list).
    """
    areas = s.query("framework_taxonomy", order_by="l2_id")
    register = list_register()
    return {
        "areas": [
            {
                "l2_id": a["l2_id"],
                "name": a["name"],
                "l1_theme": a["l1_theme"],
                "status": a["coverage_status"],
                "reason": a["coverage_reason"],
                "diagnostics": a["coverage_diagnostics_json"] or [],
            }
            for a in areas
        ],
        "executable": [r["diagnostic_id"] for r in register if r["workflow_status"] == "executable"],
        "workflow_pending": [r["diagnostic_id"] for r in register if r["workflow_status"] != "executable"],
    }


def scoring_weights() -> dict[str, Any]:
    """FWK-16 — the re-derived scoring basis for the 9-row register, read
    straight from dq_framework_data.json (the only place a weight may live —
    never a Python constant). Carries `coverage_statement`: the honesty rule
    a score panel must apply while most core diagnostics are workflow_pending
    (a one-diagnostic score presented as a health score is a lie of
    omission), and `superseded_scheme_note`: the retired weighting scheme
    and a worked fixture delta, so a score movement is a reviewed decision,
    never a surprise (FWK-16's own requirement).
    """
    return _load_framework_data()["scoring_weights"]
