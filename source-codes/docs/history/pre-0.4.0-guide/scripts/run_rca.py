#!/usr/bin/env python
"""Drive the agentic AI-RCA end-to-end, headless.

The RCA agent (``ai/rca_agent``) investigates ONE failed diagnostic. It pauses
whenever it wants to run analysis code (the human-in-the-loop gate). This script
acts as the human and AUTO-APPROVES (or auto-rejects) each proposed snippet, then
prints the declared root cause + remediation and runs the Noether checker over
the finding.

Requires an Azure OpenAI key (the agent calls the LLM). It reads the physical
warehouse but does NOT need system_state.db.

Examples
--------
  # default: the planted PSI failure on retail_accounts.bureau_score (I1)
  python run_rca.py

  python run_rca.py --table transactions --column amount \
         --test "PSI (Population Stability Index)" \
         --expected "PSI <= 0.20" --actual "PSI = 0.205 (FAIL)"

  python run_rca.py --reject     # decline every proposed snippet (stress path)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[4] / "backend"
sys.path.insert(0, str(BACKEND))

from ai import rca_agent  # noqa: E402

MAX_ROUNDS = 8  # safety cap on approval rounds


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", default="retail_accounts")
    ap.add_argument("--column", default="bureau_score")
    ap.add_argument("--test", default="PSI (Population Stability Index)")
    ap.add_argument("--category", default="C1")
    ap.add_argument("--expected", default="PSI <= 0.20")
    ap.add_argument("--actual", default="PSI = 0.287 (FAIL)")
    ap.add_argument("--target", default="default_flag")
    ap.add_argument("--reject", action="store_true",
                    help="reject every proposed code snippet instead of approving")
    args = ap.parse_args()

    approve = not args.reject
    failed_test = {
        "id": f"{args.test}-{args.column}",
        "name": args.test,
        "column": f"{args.table}.{args.column}",
        "category": args.category,
        "l2_area": "Sample & Representativeness",
        "expected": args.expected,
        "actual": args.actual,
        "target": args.target,
        "post_outcome_cols": [],
    }

    print(f"# RCA on {failed_test['column']}  ({args.test})")
    print(f"# expected={args.expected!r}  actual={args.actual!r}  "
          f"approve_code={approve}")
    print("-" * 72)

    state = rca_agent.start_session_table(args.table, failed_test)
    sid = state["id"]

    rounds = 0
    while state["status"] == "awaiting_approval" and rounds < MAX_ROUNDS:
        rounds += 1
        print(f"\n[agent proposes code — round {rounds}]")
        print("rationale:", state.get("rationale"))
        print("--- proposed code ---")
        print(state.get("pending_code"))
        print(f"--- {'APPROVING' if approve else 'REJECTING'} ---")
        state = rca_agent.approve_code(sid, approve)

    print("\n" + "=" * 72)
    print("STATUS:", state["status"])
    result = state.get("result") or {}
    print("ROOT CAUSE :", result.get("root_cause"))
    print("EVIDENCE   :", result.get("evidence"))
    print("REMEDIATION:", f"{result.get('remediation_key')} -> {result.get('remediation')}")

    print("\n[Noether checker — effective challenge]")
    try:
        verdict = rca_agent.run_checker(sid)
        print("  verdict :", verdict.get("verdict"), "| approved:", verdict.get("approved"))
        print("  note    :", verdict.get("note"))
        for issue in verdict.get("issues") or []:
            print("  issue   :", issue)
    except Exception as exc:  # noqa: BLE001
        print("  checker error:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
