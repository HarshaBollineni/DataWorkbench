#!/usr/bin/env python
"""Exercise the test-RECOMMENDATION layer from the command line.

Modes
-----
  search    (default, NO Azure key) — the deterministic Hypatia "Search". Returns
            existing-battery library tests whose ``input_spec`` matches the
            selected column(s)' datatype / date requirements. Pure rules, no LLM.

  deep      (needs Azure key) — Hypatia "Deep Search": the LLM ranks the EXISTING
            library only and never authors a new test.

  generate  (needs Azure key + system_state.db) — the full Gauss New-Test Manager
            multi-agent loop: Fisher -> Poincare -> Fermat -> Euler -> Hypatia ->
            synthesis, then a dry-run of the freshly authored ``python_code``
            against the real table. Prints every Agent-Console event.

Examples
--------
  python recommend_tests.py --table retail_accounts --columns bureau_score
  python recommend_tests.py --mode deep     --table retail_accounts --columns bureau_score \
         --description "distribution drift on the bureau score feature"
  python recommend_tests.py --mode generate --table retail_accounts --columns bureau_score \
         --description "detect distribution drift over time" --category C1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[4] / "backend"
sys.path.insert(0, str(BACKEND))

from database import TABLE_KEYS, load_table          # noqa: E402
from seeds.test_library_seed import seed_rows        # noqa: E402


def _context(table: str, target: str | None) -> dict:
    """Build the screening context straight from the warehouse (no system DB)."""
    df = load_table(table)
    date_col = TABLE_KEYS.get(table, {}).get("date_col")
    datatypes = {}
    for c in df.columns:
        datatypes[c] = "date" if c == date_col else str(df[c].dtype)
    return {"datatypes": datatypes, "date_col": date_col,
            "target_variable": target, "logical_db": None}


def run_search(table, fields, description, target) -> None:
    from ai.agents import test_screening
    library = seed_rows()
    res = test_screening.screen(table, fields, description, library,
                                mode="search", context=_context(table, target))
    print(f"[Hypatia/Search] searched {res['searched_count']} library tests")
    if not res["recommended"]:
        print("  (none)  ->", res["reason"])
    for t in res["recommended"]:
        print(f"  + {t['test_id']:<14} {t['name']}  ({t['confidence']})")
        print(f"    {t['reason']}")


def run_deep(table, fields, description, target) -> None:
    from ai.agents import test_screening
    from ai.effort import EffortPolicy
    library = seed_rows()
    res = test_screening.screen(table, fields, description, library,
                                EffortPolicy("medium"), mode="deep",
                                context=_context(table, target))
    print(f"[Hypatia/DeepSearch] {res['message']}")
    for t in res["recommended"]:
        print(f"  + {t['test_id']:<14} {t['name']}  ({t['confidence']})")
        print(f"    {t['reason']}")


def run_generate(table, fields, category, description) -> None:
    from ai import test_manager
    final = None
    for ev in test_manager.stream_generate(table, fields, category, description):
        if ev.get("final"):
            final = ev
            continue
        agent = ev.get("agent", "?")
        phase = ev.get("phase", "?")
        print(f"  [{agent}/{phase}] {ev.get('thought', '')}")
    print("\n=== AUTHORED TEST ===")
    test = (final or {}).get("test") or {}
    for k in ("name", "category", "thresholds", "library_recommendations",
              "executable"):
        print(f"  {k}: {test.get(k)}")
    print("  dry_run.ok:", (test.get("dry_run") or {}).get("ok"))
    print("\n--- generated python_code ---")
    print(test.get("python_code", "(none)"))
    print("\n--- dry-run stdout (executed against the real table) ---")
    print((test.get("dry_run") or {}).get("stdout", "(none)"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["search", "deep", "generate"], default="search")
    ap.add_argument("--table", required=True)
    ap.add_argument("--columns", default="", help="comma-separated field name(s)")
    ap.add_argument("--description", default="", help="natural-language request / intent")
    ap.add_argument("--category", default=None, help="C1..C4 (generate mode hint)")
    ap.add_argument("--target", default=None, help="target/outcome column name (context)")
    args = ap.parse_args()

    fields = [c.strip() for c in args.columns.split(",") if c.strip()]
    print(f"# mode={args.mode} table={args.table} fields={fields}")
    print("-" * 72)
    if args.mode == "search":
        run_search(args.table, fields, args.description, args.target)
    elif args.mode == "deep":
        run_deep(args.table, fields, args.description, args.target)
    else:
        run_generate(args.table, fields, args.category, args.description)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
