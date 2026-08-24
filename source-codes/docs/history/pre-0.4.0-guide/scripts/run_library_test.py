#!/usr/bin/env python
"""Run ONE library DQ test (KS, PSI, vintage, ...) directly against the warehouse.

It loads the test's REAL, verbatim ``python_code`` from the seed library and
executes it through the very same hardened sandbox the app uses
(``ai/code_sandbox.run``). The number you get here is therefore identical to a
production Run-Validations result. **No Azure key and no system_state.db are
required** — this path only reads the physical warehouse.

Examples
--------
  python run_library_test.py --list

  # planted-issue I1 -> FAIL (PSI ~0.287)
  python run_library_test.py --test psi  --table retail_accounts --columns bureau_score

  # planted-issue I3 -> FAIL (PSI ~0.205)
  python run_library_test.py --test psi  --table transactions    --columns amount

  # clean control -> PASS (PSI ~0.016)
  python run_library_test.py --test psi  --table obligors        --columns leverage

  python run_library_test.py --test ks   --table transactions    --columns amount
  python run_library_test.py --test monotonicity --table obligors --columns rating_num,leverage \
         --param expected=monotone_increasing

  # restrict the analysis to a date window (filters on the table's date column)
  python run_library_test.py --test psi --table retail_accounts --columns bureau_score \
         --start 2018-01 --end 2020-12 --param threshold=0.2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# --- make the backend importable, wherever this script is run from -----------
BACKEND = Path(__file__).resolve().parents[4] / "backend"
sys.path.insert(0, str(BACKEND))

import pandas as pd                          # noqa: E402
from database import TABLE_KEYS, load_table  # noqa: E402
from seeds.test_library_seed import seed_rows  # noqa: E402
from ai import code_sandbox                  # noqa: E402

LIBRARY = {r["test_id"]: r for r in seed_rows()}


def _coerce(value: str):
    """Best-effort scalar coercion for --param values."""
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def _print_library() -> None:
    print("Available library tests (test_id -> name  [n_columns, needs_date]):\n")
    for tid, row in LIBRARY.items():
        spec = row.get("input_spec") or {}
        print(f"  {tid:<14} {row['name']}")
        print(f"  {'':14} cols={spec.get('n_columns')} "
              f"date={bool(spec.get('needs_date_col'))} "
              f"defaults={row.get('thresholds')}")
    print("\nColumn order matters for 2-column tests "
          "(corr_stability, monotonicity).")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", help="library test_id (psi, ks, vintage, ...)")
    ap.add_argument("--table", help="warehouse table name")
    ap.add_argument("--columns", default="",
                    help="comma-separated column(s), in the order the test expects")
    ap.add_argument("--date-col", help="override the table's default date column")
    ap.add_argument("--start", help="inclusive lower bound on date_col (YYYY-MM[-DD])")
    ap.add_argument("--end", help="inclusive upper bound on date_col")
    ap.add_argument("--param", action="append", default=[],
                    help="override a threshold/param, e.g. --param threshold=0.2 (repeatable)")
    ap.add_argument("--list", action="store_true", help="list available tests and exit")
    args = ap.parse_args()

    if args.list or not (args.test and args.table):
        _print_library()
        return 0 if args.list else 2

    if args.test not in LIBRARY:
        print(f"Unknown test '{args.test}'. Use --list to see valid ids.")
        return 2

    row = LIBRARY[args.test]
    code = row["python_code"]
    columns = [c.strip() for c in args.columns.split(",") if c.strip()]

    # date column: explicit override > the table's registered date column
    date_col = args.date_col or TABLE_KEYS.get(args.table, {}).get("date_col")

    # params precedence: library defaults < injected date_col < CLI overrides
    params = dict(row.get("thresholds") or {})
    if date_col:
        params.setdefault("date_col", date_col)
    for kv in args.param:
        if "=" not in kv:
            print(f"Bad --param '{kv}', expected key=value")
            return 2
        key, val = kv.split("=", 1)
        params[key.strip()] = _coerce(val.strip())

    # load the full table, then optionally restrict to a date window
    df = load_table(args.table)
    if (args.start or args.end) and date_col and date_col in df.columns:
        dt = pd.to_datetime(df[date_col], errors="coerce")
        mask = pd.Series(True, index=df.index)
        if args.start:
            mask &= dt >= pd.to_datetime(args.start)
        if args.end:
            mask &= dt <= pd.to_datetime(args.end)
        df = df[mask].reset_index(drop=True)

    print(f"# test={args.test}  table={args.table}  columns={columns or '(date-only)'}")
    print(f"# rows={len(df)}  date_col={date_col}  params={params}")
    print("-" * 72)

    out = code_sandbox.run(code, df, {"columns": columns, "params": params})

    if out.get("stdout"):
        print(out["stdout"].rstrip())           # the CLI diagnostics the test prints
    print("-" * 72)
    if out.get("ok"):
        print("RESULT:", out.get("result_obj"))  # {status, metric, threshold, ...}
    else:
        print("ERROR :", out.get("error"))
        if out.get("traceback"):
            print(out["traceback"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
