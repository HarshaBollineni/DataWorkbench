"""Plan 3 / F3 — calibration re-derivation on the RAW tables (Phase-0 gate).

Tests now bind to raw tables (`retail_accounts`, `transactions`, `obligors`),
not the old `mr_*` marts. This spike re-measures every planted defect on its
raw target and asserts the locked thresholds still trip them. `_planted_issues`
is the invisible internal oracle; this script is the only place it is consulted.

Run:  python backend/spike_recalibrate.py
Pass: I1-I4 print FAIL and the clean control prints PASS (exit 0).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spike_fixtures import PSI_LIBRARY_CODE, load_table  # noqa: E402
from gx.metrics import (  # noqa: E402
    MISSINGNESS_TOL,
    PSI_THRESHOLD,
    compute_psi,
    detect_leakage,
    missingness_rate,
)


def _check(label: str, observed: float | bool, expect_fail: bool, rule: str) -> bool:
    """A test 'FAILs' (defect detected) per its rule; print + return ok-vs-oracle."""
    failed = bool(observed) if isinstance(observed, bool) else observed > _THRESH[label]
    verdict = "FAIL" if failed else "PASS"
    ok = failed == expect_fail
    obs = observed if isinstance(observed, bool) else round(float(observed), 4)
    print(f"  {label:6s} {verdict:4s}  observed={obs}  rule={rule}  "
          f"[{'ok' if ok else 'ORACLE MISMATCH'}]")
    return ok


_THRESH = {
    "I1": PSI_THRESHOLD,
    "I2": MISSINGNESS_TOL,
    "I3": PSI_THRESHOLD,
    "I4": 0,  # leakage is boolean
    "clean": PSI_THRESHOLD,
}


def _sandbox_psi(df, feature: str, date_col: str) -> float:
    """Run the LIBRARY's real PSI python_code through the execution sandbox —
    the SAME path the product uses at runtime (Plan 5). Proves the engine, not
    just the metric, trips the oracle."""
    from ai import code_sandbox
    out = code_sandbox.run(PSI_LIBRARY_CODE, df, {"columns": [feature],
                                      "params": {"date_col": date_col, "threshold": PSI_THRESHOLD}})
    if not out.get("ok"):
        raise RuntimeError(out.get("error") or "sandbox PSI failed")
    return float(out["result_obj"]["metric"])


def main() -> int:
    ra = load_table("retail_accounts")
    tx = load_table("transactions")
    ob = load_table("obligors")

    print("Plan 3 F3 calibration gate (raw tables) — thresholds: "
          f"PSI>{PSI_THRESHOLD}, missingness>{MISSINGNESS_TOL}")
    results = [
        _check("I1", compute_psi(ra, "bureau_score", "open_date"), True,
               "PSI(retail_accounts.bureau_score | open_date)"),
        _check("I2", missingness_rate(ra, "balance", "channel", "Online",
                                      "open_date", "2018-06", "2018-12")["rate"], True,
               "missingness(retail_accounts.balance | channel=Online, 2018-H2)"),
        _check("I3", compute_psi(tx, "amount", "txn_datetime"), True,
               "PSI(transactions.amount | txn_datetime)"),
        _check("I4", detect_leakage(ra, ["collections_contact_flag", "recovery_rate"])["leaked"],
               True, "leakage(retail_accounts.collections_contact_flag)"),
        _check("clean", compute_psi(ob, "leverage", "reporting_date"), False,
               "PSI(obligors.leverage | reporting_date)"),
    ]

    print("\nPlan 5 — same oracle through the REAL execution engine (sandbox + library code):")
    results += [
        _check("I1", _sandbox_psi(ra, "bureau_score", "open_date"), True,
               "sandbox PSI(retail_accounts.bureau_score)"),
        _check("I3", _sandbox_psi(tx, "amount", "txn_datetime"), True,
               "sandbox PSI(transactions.amount)"),
        _check("clean", _sandbox_psi(ob, "leverage", "reporting_date"), False,
               "sandbox PSI(obligors.leverage)"),
    ]
    ok = all(results)
    print("\nGATE:", "PASS — oracle aligned (metric + engine), demo defect story intact." if ok
          else "FAIL — recalibrate before proceeding.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
