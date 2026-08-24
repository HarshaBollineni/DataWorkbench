"""0.4.0 verification gate — the 9-diagnostic framework register (rebuilt, Phase 3).

Rebuilt against the new register per plan rule 7 / C-19: the previous Plan-8
gate asserted the retired control-plane/tool-registry stack and was broken at
baseline (F-01 — it imported modules that imported the `database` module
deleted at 0.2.0). This gate asserts the replacement framework:

  register (9 rows, one executable) · taxonomy (6/11/6) · refusal semantics ·
  old-registry retirement · threshold semantic layer · guard-chain skeleton ·
  delivery seam · coverage honesty

Run from backend/:  ../.venv/Scripts/python.exe verify_plan8.py
Assert-based (no pytest dependency). Exits non-zero on any failure. Runs on a
THROWAWAY database — never touches the real system_state.db. No LLM calls.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# A throwaway DB, resolved BEFORE system_db is imported.
_tmp = tempfile.mkdtemp(prefix="verify-040-")
os.environ["SYSTEM_DB_PATH"] = str(Path(_tmp) / "verify_state.db")

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


import system_db  # noqa: E402

system_db.init_schema()

from dq_diagnostics import register as reg  # noqa: E402
from dq_diagnostics import thresholds as th  # noqa: E402
from dq_diagnostics.guards import (  # noqa: E402
    GuardedScope, GuardUnavailableError, apply_guards)

reg.seed_register()

# --- 1. The register -------------------------------------------------------
rows = reg.list_register()
ids = sorted(r["diagnostic_id"] for r in rows)
check("register: exactly 9 rows", len(rows) == 9, str(len(rows)))
check("register: ids are S8 rows {2,4,6,8,11,12,14,17,20}",
      ids == [2, 4, 6, 8, 11, 12, 14, 17, 20], str(ids))
executable = [r for r in rows if r["workflow_status"] == "executable"]
check("register: exactly two executable diagnostics", len(executable) == 2, str(len(executable)))
check("register: executable diagnostics are #2 and #4",
      [row["diagnostic_id"] for row in executable] == [2, 4])
check("register: executable rows carry enabling decisions (FWK-18)",
      all(row.get("enabled_by") for row in executable))
check("register: every row has a valid decision_type",
      all(r.get("decision_type") in {"verdict", "candidate flag", "contextual",
                                     "classification output", "SME gate",
                                     "candidate_flag", "classification_output",
                                     "sme_gate", "threshold+SME"} for r in rows),
      str(sorted({r.get("decision_type") for r in rows})))

# --- 2. Taxonomy -----------------------------------------------------------
tax = system_db.query("framework_taxonomy")
themes = {t["l1_theme"] for t in tax}
areas = system_db.query("framework_test_areas")
check("taxonomy: 6 L1 themes", len(themes) == 6, str(sorted(themes)))
check("taxonomy: 11 L2 areas", len(tax) == 11, str(len(tax)))
check("taxonomy: 6 test areas T1-T6", len(areas) == 6
      and sorted(a["area_id"] for a in areas) == ["T1", "T2", "T3", "T4", "T5", "T6"])

# --- 3. Refusal semantics (FWK-17, D-17) ------------------------------------
try:
    reg.require_executable(8)
    check("refusal: workflow-pending #8 refused", False)
except reg.WorkflowPendingError as exc:
    check("refusal: workflow-pending #8 refused with the message",
          "workflow not yet defined" in str(exc), str(exc))
except Exception as exc:  # noqa: BLE001
    check("refusal: workflow-pending #8 refused", False, repr(exc))

msgs = []
for bad_id in (3, 999):  # 3 is an S8 defer row: must be indistinguishable from nonsense
    try:
        reg.get_diagnostic(bad_id)
        msgs.append(None)
    except KeyError as exc:
        msgs.append(str(exc).replace(str(bad_id), "<id>"))
check("refusal: defer-row id and nonsense id are the same unknown",
      msgs[0] is not None and msgs[0] == msgs[1], repr(msgs))

# --- 4. The old framework is gone (FWK-13, 3-T3) ----------------------------
from dq_tests import param_specs, registry  # noqa: E402

check("retired: TEST_NAMES empty", registry.TEST_NAMES == [])
check("retired: TEST_REGISTRY empty", registry.TEST_REGISTRY == {})
check("retired: PARAM_SPECS empty", param_specs.PARAM_SPECS == {})
try:
    registry.run_registered("PSI (Population Stability Index)", None)
    check("retired: run_registered refuses every name", False)
except KeyError:
    check("retired: run_registered refuses every name", True)
check("retired: test_library table empty", len(system_db.query("test_library")) == 0)
check("retired: fw_areas content gone", len(system_db.query("fw_areas")) == 0)
check("retired: fw_tests content gone", len(system_db.query("fw_tests")) == 0)

# --- 5. Threshold semantic layer (FWK-06) -----------------------------------
tol = th.effective_threshold(4, "tolerance")
check("thresholds: #4 tolerance resolves from settings",
      tol["value"] == 0.0 and tol["source"] == "default", str(tol))
psi = th.effective_threshold(14, "psi_watch")
check("thresholds: #14 psi_watch seeded", psi["value"] == 0.10, str(psi))
th.set_threshold(4, "tolerance", 0.05, scope="engagement", scope_ref="verify-eng", actor="verify")
tol2 = th.effective_threshold(4, "tolerance", engagement="verify-eng")
check("thresholds: engagement override wins with no code change",
      tol2["value"] == 0.05 and "engagement" in tol2["source"], str(tol2))

# --- 6. Guard-chain skeleton (FWK-09/10/11) ----------------------------------
try:
    apply_guards(columns=["x"], classifications={"x": "numerical"},
                 allowed_classes=["numerical"])
    check("guards: slice-1 statistical scope refused (producer not yet available)", False)
except GuardUnavailableError as exc:
    check("guards: slice-1 statistical scope refused (producer not yet available)",
          "producer not yet available" in str(exc), str(exc))
try:
    GuardedScope()  # type: ignore[call-arg]
    check("guards: GuardedScope not constructible outside apply_guards", False)
except Exception:  # noqa: BLE001
    check("guards: GuardedScope not constructible outside apply_guards", True)

# --- 7. Delivery seam (PLT-02) ----------------------------------------------
cols = {r["name"] for r in system_db.execute("PRAGMA table_info(dq_items)")}
needed = {"dataset_family_id", "delivery_seq", "as_of_date", "baseline_delivery_id"}
check("delivery: dq_items carries the PLT-02 columns", needed <= cols,
      str(sorted(needed - cols)))

# --- 8. Coverage honesty (FWK-14/15) ------------------------------------------
cov_map = reg.coverage_map()
cov = cov_map["areas"]
gaps = {c["l2_id"] for c in cov if c["status"] == "gap"}
thin = {c["l2_id"] for c in cov if c["status"] == "covered_thin"}
check("coverage: register split executable/pending is 2/7",
      cov_map["executable"] == [2, 4] and len(cov_map["workflow_pending"]) == 7,
      str(cov_map["executable"]))
check("coverage: the five named GAP areas",
      gaps == {"L2-03", "L2-05", "L2-08", "L2-09", "L2-11"}, str(sorted(gaps)))
check("coverage: six covered-thin, nothing rounds up to covered",
      len(thin) == 6 and not any(c["status"] == "covered" for c in cov), str(sorted(thin)))
check("coverage: every gap states its reason",
      all(c.get("reason") for c in cov if c["status"] == "gap"))

# --- report ------------------------------------------------------------------
EXPECTED_CHECKS = 28  # rule 7: the new count, asserted
print("0.4.0 framework verification gate (rebuilt Phase 3)")
allok = True
for name, ok, detail in results:
    allok &= ok
    print(f"  {PASS if ok else FAIL}  {name}" + (f"  [{detail}]" if (detail and not ok) else ""))
if len(results) != EXPECTED_CHECKS:
    allok = False
    print(f"  {FAIL}  check-count drift: {len(results)} checks, expected {EXPECTED_CHECKS}")
print(f"\nGATE: {'PASS' if allok else 'FAIL'} — {sum(o for _, o, _ in results)}/{len(results)} checks")
sys.exit(0 if allok else 1)
