"""GX spike: prove the custom-expectation plumbing end-to-end through real GX.
Run: python backend/spike_gx.py  (exits non-zero on failure)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "source-codes" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

import great_expectations as gx
import great_expectations.expectations as gxe

from spike_fixtures import get_contract, load_table
from gx.custom_expectations.leakage import ExpectPostOutcomeColumnsToBeClean
from gx.runner import run_suite


def main() -> int:
    c = get_contract("decisioning")
    df = load_table(c["table"])

    expectations = [
        # custom: leakage — must FAIL (I4: collections_contact_flag present)
        ExpectPostOutcomeColumnsToBeClean(
            post_outcome_cols=c["post_outcome_cols"],
            meta={"id": "leakage", "name": "Post-Outcome Leakage",
                  "l1_theme": "Temporal Integrity", "l2_area": "Feature Leakage",
                  "criticality": "Critical", "expected": "no post-outcome columns"},
        ),
        # native: uniqueness on PK — must PASS
        gxe.ExpectColumnValuesToBeUnique(
            column="account_id",
            meta={"id": "uniqueness", "name": "PK Uniqueness", "expected": "all unique"},
        ),
    ]

    parsed = run_suite(df, expectations, c)
    print(json.dumps(parsed, indent=2, default=str))

    by_id = {r["id"]: r for r in parsed["results"]}
    ok = (
        by_id["leakage"]["outcome"] == "failed"
        and by_id["leakage"]["actual"] == "['collections_contact_flag']"
        and by_id["uniqueness"]["outcome"] == "passed"
        and by_id["leakage"]["l2_area"] == "Feature Leakage"
    )
    print("\nSPIKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
