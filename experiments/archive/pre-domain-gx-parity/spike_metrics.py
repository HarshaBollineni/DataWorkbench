"""Spike: verify metrics.py against portfolio_alt_master.db + the planted-issue
answer key, with NO Great Expectations dependency. Run:  python backend/spike_metrics.py
Exits non-zero if any calibrated expectation does not match the planted truth.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "source-codes" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from database import DOWNTURN_END, DOWNTURN_START, get_contract, load_table
from gx import metrics as M

checks: list[tuple[str, bool]] = []


def expect(name, got, want):
    ok = got == want
    checks.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got} want={want}")


def main() -> int:
    dec = load_table("mr_decisioning")
    irb = load_table("mr_wholesale_irb")
    dec_c = get_contract("decisioning")
    irb_c = get_contract("wholesale_irb")

    # PSI — I1 bureau-score shift FAILS on decisioning; clean leverage PASSES on irb
    psi_dec = M.compute_psi(dec, dec_c["psi_feature"], dec_c["date_col"])
    psi_irb = M.compute_psi(irb, irb_c["psi_feature"], irb_c["date_col"])
    print(f"  psi decisioning/{dec_c['psi_feature']}={psi_dec:.4f}  irb/{irb_c['psi_feature']}={psi_irb:.4f}")
    expect("PSI decisioning FAILS (>0.25)", psi_dec > M.PSI_THRESHOLD, True)
    expect("PSI irb PASSES (<=0.25)", psi_irb <= M.PSI_THRESHOLD, True)

    # Leakage — I4 post-outcome col present on decisioning; none on irb
    lk_dec = M.detect_leakage(dec, dec_c["post_outcome_cols"])
    lk_irb = M.detect_leakage(irb, irb_c["post_outcome_cols"])
    print(f"  leakage decisioning={lk_dec}  irb={lk_irb}")
    expect("Leakage decisioning FAILS", lk_dec["leaked"], True)
    expect("Leakage irb PASSES", lk_irb["leaked"], False)

    # Regime — decisioning overlaps downturn (PASS); irb 2021-23 N/A (vacuous PASS)
    rg_dec = M.downturn_regime_coverage(dec, dec_c["date_col"], DOWNTURN_START, DOWNTURN_END)
    rg_irb = M.downturn_regime_coverage(irb, irb_c["date_col"], DOWNTURN_START, DOWNTURN_END)
    print(f"  regime decisioning={rg_dec}  irb={rg_irb}")
    expect("Regime decisioning applicable+covered", rg_dec["applicable"] and rg_dec["coverage"] >= M.REGIME_MIN_COVERAGE, True)
    expect("Regime irb not applicable (vacuous PASS)", rg_irb["applicable"], False)

    # Vintage — both clear 24 months
    expect("Vintage decisioning >=24m", M.observation_window_months(dec, dec_c["date_col"]) >= M.VINTAGE_MIN_MONTHS, True)
    expect("Vintage irb >=24m", M.observation_window_months(irb, irb_c["date_col"]) >= M.VINTAGE_MIN_MONTHS, True)

    # KS by segment — irb leverage by sector is representative (PASS)
    ks_irb = M.compute_ks_by_segment(irb, "leverage", "sector")
    print(f"  ks irb leverage/sector={ks_irb}")
    expect("KS irb PASSES (<=0.20)", ks_irb["ks"] <= M.KS_THRESHOLD, True)

    # Conditional relationships — irb rating_num vs interest_coverage / leverage
    rho_ic = M.conditional_spearman(irb, "rating_num", "interest_coverage")
    rho_lev = M.conditional_spearman(irb, "rating_num", "leverage")
    print(f"  spearman rating_num~interest_coverage={rho_ic:.4f}  rating_num~leverage={rho_lev:.4f}")
    expect("rating_num~interest_coverage is inverse", M.relationship_holds(rho_ic, "inverse"), True)
    expect("rating_num~leverage is monotone_increasing", M.relationship_holds(rho_lev, "monotone_increasing"), True)

    # Target stability — irb default rate stable across quarters
    ts_irb = M.target_rate_quarterly_range(irb, irb_c["target"], irb_c["date_col"])
    print(f"  target irb quarterly range={ts_irb}")
    expect("Target irb stable (<=0.15)", ts_irb["range"] <= M.TARGET_RATE_MAX_RANGE, True)

    passed = sum(ok for _, ok in checks)
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
