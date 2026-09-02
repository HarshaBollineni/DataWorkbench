"""Phase 2 / PLT-08 behavioural tests for the ai/ helper layer (plan 2.2-2.8;
test criteria 2-T1..2-T4 + the invariant-bite proof, plan rule 6).

2-T1 (N) — every module under backend/ai/ imports cleanly in a fresh
            subprocess, except a small documented allowlist; no helper-layer
            module references the name ``database`` at module level.
2-T2 (U/N) — ``ai.test_kit`` is the ONE registry: a duplicate name raises;
             ``ai.tool_registry`` has no catalogue of its own (it delegates).
2-T3 (U) — every registered helper is documented (fn docstring + purpose);
           each of the 12 ``ai.rca_helpers`` probes gets a real unit test on
           a small, fully deterministic pandas fixture.
2-T4 (S) — a helper that raises produces the one structured log line
           (logger "archimedes.helpers", DEBUG) with the caller's context id.

Invariant-bites proof (plan rule 6): ``test_register_duplicate_name_raises``
and ``test_duplicate_registration_guard_actually_bites`` both exercise the
same guard in ``ai.test_kit.register``. To verify by hand that the guard is
load-bearing (not a test that would pass either way): comment out the
``if helper.name in _CATALOGUE: raise ValueError(...)`` lines in
``ai/test_kit.py``'s ``register()`` and rerun this file — both tests fail
because no exception is raised.
"""
from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from domains.rca import analysis_helpers as rh
import ai.test_kit as tk
import ai.tool_registry as tr

BACKEND = Path(__file__).resolve().parents[3]
AI_ROOT = BACKEND / "ai"

# ---------------------------------------------------------------------------
# 2-T1 — module reachability sweep
# ---------------------------------------------------------------------------
# Modules under backend/ai/ that are known-broken at baseline and intentionally
# NOT fixed by this phase's assigned file list (backend/ai/test_kit.py,
# rca_helpers.py, tool_registry.py, code_sandbox.py, llm.py, control_plane.py,
# _helper_log.py). All three share one root cause: a top-level
# ``from database import ...`` — ``database.py`` was deleted at 0.2.0.
_IMPORT_ALLOWLIST: dict[str, str] = {
    # Empty since Phase 3: ai.test_manager (the last preserved-broken module)
    # was deleted with the verify_plan8.py rebuild; ai.frame_assembler and
    # ai.rule_generator went in the Phase-2 sweep. Every ai/ module must now
    # import cleanly in isolation — add an entry here only with a recorded
    # keep-reason in docs/0.4.0/08-helper-layer.md.
}


def _enumerate_ai_modules() -> dict[str, Path]:
    """Every ``ai.*`` dotted module name (including ``ai.v2``/``ai.agents``)
    mapped to its source file, discovered by walking backend/ai/ on disk."""
    out: dict[str, Path] = {}
    for path in sorted(AI_ROOT.rglob("*.py")):
        rel = path.relative_to(BACKEND).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
            if not parts:
                continue
        out[".".join(parts)] = path
    return out


_IMPORT_SWEEP_SCRIPT = (
    "import importlib, json, sys\n"
    "mods = json.loads(sys.argv[1])\n"
    "out = {}\n"
    "for m in mods:\n"
    "    try:\n"
    "        importlib.import_module(m)\n"
    "        out[m] = None\n"
    "    except Exception as exc:\n"
    "        out[m] = f'{type(exc).__name__}: {exc}'\n"
    "print(json.dumps(out))\n"
)


def _import_sweep(mod_names: list[str]) -> dict[str, str | None]:
    """Import every name in ``mod_names`` in ONE fresh subprocess (a clean
    interpreter — none of this test session's already-cached ``sys.modules``
    can hide an import bug), returning {module: None-if-ok-else-error-message}."""
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_SWEEP_SCRIPT, json.dumps(mod_names)],
        cwd=str(BACKEND), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        f"import-sweep subprocess itself crashed (rc={proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_every_ai_module_imports_cleanly_except_the_documented_allowlist():
    modules = _enumerate_ai_modules()
    results = _import_sweep(sorted(modules))
    unexpected_failures = {
        m: err for m, err in results.items()
        if err is not None and m not in _IMPORT_ALLOWLIST
    }
    assert not unexpected_failures, (
        f"module(s) under backend/ai/ failed to import cleanly and are not in "
        f"the documented allowlist: {unexpected_failures}"
    )
    # Keep the allowlist honest: if an allowlisted module starts importing
    # cleanly (e.g. a later phase fixes it), this test should force the row
    # to be dropped rather than silently keep it around forever.
    now_clean = {m for m in _IMPORT_ALLOWLIST if results.get(m) is None}
    assert not now_clean, (
        f"module(s) now import cleanly — remove from _IMPORT_ALLOWLIST: {now_clean}"
    )


def _references_database_at_module_level(path: Path) -> bool:
    """True if ``path`` has a TOP-LEVEL (not inside a function/class body)
    ``import database`` or ``from database import ...``. A lazy, function-
    scoped import of ``database`` does not break importing the module and is
    out of scope for this check (e.g. ai/tool_registry.py's
    ``_execute_sandboxed_code`` keeps one, documented, for a later pass)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:  # module top level ONLY
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "database" for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "database":
                return True
    return False


def test_no_module_level_database_reference_outside_the_allowlist():
    modules = _enumerate_ai_modules()
    offenders = [
        m for m, path in modules.items()
        if m not in _IMPORT_ALLOWLIST and _references_database_at_module_level(path)
    ]
    assert not offenders, (
        f"module(s) still reference the deleted `database` module at module "
        f"level (the root cause this phase removes): {offenders}"
    )


# ---------------------------------------------------------------------------
# 2-T2 — ai.test_kit is the ONE registry; ai.tool_registry delegates
# ---------------------------------------------------------------------------
def test_register_duplicate_name_raises():
    """Registering an already-used helper name raises ValueError loudly."""
    existing = tk.get_helper("psi")
    assert existing is not None
    with pytest.raises(ValueError):
        tk.register(tk.Helper(
            name="psi", fn=lambda **_: None, purpose="dup", kind="metric",
            inputs="", outputs="", failure_modes="",
        ))


def test_duplicate_registration_guard_actually_bites():
    """Invariant-bite proof (plan rule 6): the guard is load-bearing, not a
    vacuous check — attempting a duplicate under a brand-new name pair, then
    immediately re-attempting the SAME name, must raise the second time."""
    name = "test_helpers___probe_helper___not_a_real_tool"
    tk.register(tk.Helper(name=name, fn=lambda **_: 1, purpose="probe",
                           kind="metric", inputs="", outputs="", failure_modes=""))
    try:
        with pytest.raises(ValueError):
            tk.register(tk.Helper(name=name, fn=lambda **_: 2, purpose="probe again",
                                   kind="metric", inputs="", outputs="", failure_modes=""))
    finally:
        tk._CATALOGUE.pop(name, None)  # don't leak a probe entry into the catalogue


def test_tool_registry_has_no_catalogue_of_its_own():
    """ai.tool_registry._REGISTRY IS ai.test_kit's dict (no parallel store),
    and every tool_registry.list_tools() id is present, unchanged, in
    ai.test_kit.list_helpers() — same object count/name set for the "tool" kind."""
    assert tr._REGISTRY is tk._CATALOGUE

    tool_ids = {t["tool_id"] for t in tr.list_tools()}
    catalogue_tool_names = {h.name for h in tk.list_helpers() if h.kind == "tool"}
    assert tool_ids == catalogue_tool_names
    assert len(tr.list_tools()) == len(catalogue_tool_names)
    # raise_mitigation_ticket was deleted this phase (routers/tickets.py gone).
    assert "raise_mitigation_ticket" not in tool_ids


# ---------------------------------------------------------------------------
# 2-T3 — every registered helper is documented; real unit tests per rca_helper
# ---------------------------------------------------------------------------
def test_every_registered_helper_has_a_docstring_and_a_purpose():
    helpers = tk.list_helpers()
    assert len(helpers) >= 22  # 10 gx metrics + 12 rca helpers, at minimum
    undocumented = [
        h.name for h in helpers
        if not (h.fn.__doc__ or "").strip() or not (h.purpose or "").strip()
    ]
    assert not undocumented, f"helper(s) missing fn docstring or purpose text: {undocumented}"


def test_rca_helpers_dict_has_exactly_twelve_entries_matching_test_kit():
    assert len(rh.HELPERS) == 12
    catalogue_rca = {h.name for h in tk.list_helpers() if h.kind == "rca_helper"}
    assert catalogue_rca == set(rh.HELPERS)


# --- real behavioural fixtures, one per rca_helper (12) ---------------------

def test_time_window_drift_detects_a_seeded_mean_shift():
    df = pd.DataFrame({
        "x": [0.0] * 5 + [10.0] * 5,
        "date": pd.date_range("2020-01-01", periods=10),
    })
    out = rh.run_helper("time_window_drift", df, {"column": "x", "split_quantile": 0.5})
    m = out["result"]["metrics"]
    assert m["early_mean"] == pytest.approx(0.0)
    assert m["recent_mean"] == pytest.approx(10.0)
    assert m["mean_delta"] == pytest.approx(10.0)


def test_psi_ks_decomposition_detects_a_seeded_distribution_shift():
    early = np.linspace(-2, 2, 50)
    recent = np.linspace(1, 5, 50)
    df = pd.DataFrame({
        "x": np.concatenate([early, recent]),
        "date": pd.date_range("2020-01-01", periods=100),
    })
    out = rh.run_helper("psi_ks_decomposition", df, {"column": "x", "split_quantile": 0.5, "bins": 5})
    m = out["result"]["metrics"]
    assert m["psi"] > 1.0        # a real, large shift — not a rounding artifact
    assert m["ks_gap"] > 0.5


def test_segment_attribution_finds_the_drifting_segment():
    df = pd.DataFrame({
        "seg": ["A"] * 10 + ["B"] * 10,
        "x": [1.0] * 10 + [0.0] * 5 + [10.0] * 5,   # A: no drift; B: 0 -> 10
        "date": list(pd.date_range("2020-01-01", periods=10)) * 2,
    })
    out = rh.run_helper("segment_attribution", df,
                        {"column": "x", "segment_col": "seg", "split_quantile": 0.5})
    rows = out["result"]["evidence_rows"]
    assert rows[0]["segment"] == "B"          # sorted by |mean_delta| desc
    assert rows[0]["mean_delta"] == pytest.approx(10.0)
    assert [r for r in rows if r["segment"] == "A"][0]["mean_delta"] == pytest.approx(0.0)


def test_missingness_analysis_computes_the_exact_rate_change():
    df = pd.DataFrame({
        "x": [1, 2, 3, 4, 5, None, 6, None, 8, None],
        "date": pd.date_range("2020-01-01", periods=10),
    })
    out = rh.run_helper("missingness_analysis", df, {"column": "x", "split_quantile": 0.5})
    m = out["result"]["metrics"]
    assert m["early_missing_rate"] == pytest.approx(0.0)
    assert m["recent_missing_rate"] == pytest.approx(0.6)
    assert m["delta"] == pytest.approx(0.6)


def test_outlier_profile_finds_exactly_the_planted_outlier():
    df = pd.DataFrame({"x": [1, 2, 3, 4, 5, 6, 7, 8, 9, 1000]})
    out = rh.run_helper("outlier_profile", df, {"column": "x"})
    m = out["result"]["metrics"]
    assert m["outlier_count"] == 1
    assert m["outlier_rate"] == pytest.approx(0.1)
    assert m["upper_bound"] < 1000


def test_relationship_drift_detects_a_correlation_flip():
    df = pd.DataFrame({
        "a": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
        "b": [1, 2, 3, 4, 5, 5, 4, 3, 2, 1],   # early: +1 corr, recent: -1 corr
        "date": pd.date_range("2020-01-01", periods=10),
    })
    out = rh.run_helper("relationship_drift", df, {"columns": ["a", "b"], "split_quantile": 0.5})
    m = out["result"]["metrics"]
    assert m["early_corr"] == pytest.approx(1.0)
    assert m["recent_corr"] == pytest.approx(-1.0)
    assert m["corr_delta"] == pytest.approx(-2.0)


def test_join_reference_integrity_computes_exact_null_and_duplicate_rates():
    df = pd.DataFrame({"k": [1, 1, 2, 3, 4, None]})
    out = rh.run_helper("join_reference_integrity", df, {"key_col": "k"})
    m = out["result"]["metrics"]
    assert m["null_rate"] == pytest.approx(1 / 6)
    assert m["duplicate_rate"] == pytest.approx(2 / 6)
    assert m["distinct_count"] == 4


def test_duplicate_key_integrity_finds_the_planted_duplicates():
    df = pd.DataFrame({"k": [1, 1, 1, 2, 3]})
    out = rh.run_helper("duplicate_key_integrity", df, {"key_col": "k"})
    m = out["result"]["metrics"]
    assert m["duplicated_values"] == 1
    assert m["duplicated_rows"] == 3
    assert out["result"]["evidence_rows"][0] == {"key": "1", "count": 3}


def test_freshness_gap_check_finds_the_exact_max_gap():
    df = pd.DataFrame({"date": pd.to_datetime(
        ["2020-01-01", "2020-01-02", "2020-01-03", "2020-02-01"])})
    out = rh.run_helper("freshness_gap_check", df, {})
    m = out["result"]["metrics"]
    assert m["distinct_dates"] == 4
    assert m["max_gap_days"] == 29


def test_target_leakage_check_computes_exact_correlation():
    df = pd.DataFrame({"c": [1, 2, 3, 4, 5], "t": [1, 2, 3, 4, 5]})
    out = rh.run_helper("target_leakage_check", df, {"column": "c", "target_col": "t"})
    assert out["result"]["metrics"]["correlation"] == pytest.approx(1.0)


def test_cross_table_reconciliation_computes_exact_key_profile():
    df = pd.DataFrame({"k": [1, 2, 2, 3, None]})
    out = rh.run_helper("cross_table_reconciliation", df, {"key_col": "k"})
    m = out["result"]["metrics"]
    assert m["rows"] == 5
    assert m["distinct_keys"] == 3
    assert m["null_keys"] == 1


def test_schema_type_anomaly_detects_numeric_stored_as_text():
    df = pd.DataFrame({"c": ["1", "2", "3", "x", "5"]})
    out = rh.run_helper("schema_type_anomaly", df, {"column": "c"})
    m = out["result"]["metrics"]
    assert m["numeric_parse_rate"] == pytest.approx(0.8)
    assert m["null_rate"] == pytest.approx(0.0)


@pytest.mark.parametrize("helper_id", sorted(rh.HELPERS))
def test_every_rca_helper_has_a_docstring(helper_id):
    """Companion to test_every_registered_helper_has_a_docstring_and_a_purpose,
    scoped to rca_helpers.py's own implementation functions."""
    assert rh.HELPERS[helper_id].fn.__doc__, f"{helper_id} implementation fn has no docstring"
    assert rh.HELPERS[helper_id].description.strip()


# ---------------------------------------------------------------------------
# 2-T4 — a helper that raises produces the structured log line
# ---------------------------------------------------------------------------
def test_helper_failure_logs_structured_line_with_context_id(caplog):
    caplog.set_level(logging.DEBUG, logger="archimedes.helpers")
    df = pd.DataFrame({"other_column": [1, 2, 3]})
    with pytest.raises(ValueError):
        tk.call("outlier_profile", context_id="case-99", df=df, column="does_not_exist")

    records = [r for r in caplog.records if r.name == "archimedes.helpers"]
    assert records, "expected a debug log line from the failed helper call"
    msg = records[-1].getMessage()
    assert "outlier_profile" in msg
    assert "case-99" in msg
    assert "outcome=error" in msg
    assert "ValueError" in msg


def test_helper_success_logs_with_dash_when_no_context_id(caplog):
    caplog.set_level(logging.DEBUG, logger="archimedes.helpers")
    tk.call("relationship_holds", rho=0.5, relationship="monotone_increasing")
    records = [r for r in caplog.records if r.name == "archimedes.helpers"]
    assert records
    msg = records[-1].getMessage()
    assert "relationship_holds" in msg
    assert "context_id=-" in msg
    assert "outcome=ok" in msg
