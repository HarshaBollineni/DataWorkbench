"""Integration spike: run EVERY custom expectation through real GX against the
DB and assert the planted-truth outcomes. Run: python backend/spike_integration.py
"""
from __future__ import annotations

import sys

from spike_fixtures import get_contract, load_table
from gx.custom_expectations.conditional_relationship import ExpectConditionalColumnRelationship
from gx.custom_expectations.ks_test import ExpectColumnKSBySegmentToBeBelowThreshold
from gx.custom_expectations.leakage import ExpectPostOutcomeColumnsToBeClean
from gx.custom_expectations.psi import ExpectColumnPSIToBeBelowThreshold
from gx.custom_expectations.regime import ExpectDownturnRegimeCoverageToMeetMinimum
from gx.custom_expectations.target_stability import ExpectTargetRateToBeStructurallyStable
from gx.custom_expectations.vintage import ExpectObservationWindowDepthToMeetMinimum
from gx.runner import run_suite

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: outcome={got} want={want}")


def outcomes(df, exps):
    parsed = run_suite(df, exps)
    return {r["id"]: r for r in parsed["results"]}


def main() -> int:
    dec_c = get_contract("decisioning")
    irb_c = get_contract("wholesale_irb")
    dec = load_table(dec_c["table"])
    irb = load_table(irb_c["table"])

    # decisioning: psi + leakage FAIL (I1, I4); regime/vintage PASS
    o = outcomes(dec, [
        ExpectColumnPSIToBeBelowThreshold(column="bureau_score", date_col="open_date", meta={"id": "psi"}),
        ExpectPostOutcomeColumnsToBeClean(post_outcome_cols=dec_c["post_outcome_cols"], meta={"id": "leakage"}),
        ExpectDownturnRegimeCoverageToMeetMinimum(date_col="open_date", meta={"id": "regime"}),
        ExpectObservationWindowDepthToMeetMinimum(date_col="open_date", meta={"id": "vintage"}),
    ])
    print("  decisioning observed:", {k: v["actual"] for k, v in o.items()})
    check("decisioning PSI", o["psi"]["outcome"], "failed")
    check("decisioning leakage", o["leakage"]["outcome"], "failed")
    check("decisioning regime", o["regime"]["outcome"], "passed")
    check("decisioning vintage", o["vintage"]["outcome"], "passed")

    # wholesale_irb (clean): everything PASSES
    o = outcomes(irb, [
        ExpectColumnPSIToBeBelowThreshold(column="leverage", date_col="reporting_date", meta={"id": "psi"}),
        ExpectPostOutcomeColumnsToBeClean(post_outcome_cols=irb_c["post_outcome_cols"], meta={"id": "leakage"}),
        ExpectDownturnRegimeCoverageToMeetMinimum(date_col="reporting_date", meta={"id": "regime"}),
        ExpectObservationWindowDepthToMeetMinimum(date_col="reporting_date", meta={"id": "vintage"}),
        ExpectColumnKSBySegmentToBeBelowThreshold(column="leverage", segment_col="sector", meta={"id": "ks"}),
        ExpectTargetRateToBeStructurallyStable(target_col="default_flag", date_col="reporting_date", meta={"id": "target"}),
        ExpectConditionalColumnRelationship(column_A="rating_num", column_B="interest_coverage", relationship="inverse", meta={"id": "cond"}),
    ])
    print("  wholesale observed:", {k: v["actual"] for k, v in o.items()})
    for tid in ["psi", "leakage", "regime", "vintage", "ks", "target", "cond"]:
        check(f"wholesale {tid}", o[tid]["outcome"], "passed")

    n = sum(results)
    print(f"\n{n}/{len(results)} integration checks passed")
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
