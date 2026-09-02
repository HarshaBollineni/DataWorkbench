"""
bind_cli.py - the confirmation step.

For each KB it predicts which column fills each semantic role, shows the
prediction, and asks the user to confirm it or pick a different column from a
numbered list (the CLI equivalent of a dropdown).

Usage
-----
    python bind_cli.py --folder .
    python bind_cli.py --folder . --yes        # accept every prediction, no prompts
    python bind_cli.py --folder . --dataset CRE_IFRS-9_PD_Dataset.xlsx

Writes binding.json, which run_tests.py consumes. Nothing is judged here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# See README.md: the product interpreter is intentionally isolated, so this
# standalone entry point declares its own sibling-module root.
_EXPERIMENT_ROOT = str(Path(__file__).resolve().parent)
if _EXPERIMENT_ROOT not in sys.path:
    sys.path.insert(0, _EXPERIMENT_ROOT)

import derivation as D

BAR = "=" * 72


def choose_column(role: str, cands, profiles, auto: bool,
                  override: dict | None = None) -> str | None:
    """
    Show the prediction for one role and return the confirmed column, or None.

    `override` is the agent's correction for this role, if it made one. When the
    agent overrode the pattern matcher, the agent's answer becomes the default
    and both are shown, so the user can see what changed and why.
    """
    spec = D.ROLES[role]
    top = cands[0] if cands else None
    regex_col = top.column if top else None

    print(f"\n  Role: {spec['label']}  ({spec['kind']})")

    if override:
        default = override["to"]
        print(f"    pattern match -> {regex_col}")
        print(f"    AGENT OVERRIDE -> {default}   [{override.get('confidence','?')}]")
        print(f"    because          {override.get('reason','')}")
        if default:
            print(f"    column is        {profiles[default].describe()}")
    else:
        default = regex_col
        if top:
            conf = "high" if top.score >= 75 else "medium" if top.score >= 55 else "low"
            print(f"    predicted -> {top.column}")
            print(f"    because     {top.reason}  [confidence: {conf}]")
            print(f"    column is   {profiles[top.column].describe()}")
        else:
            print("    predicted -> (nothing matched)")

    top = type("_T", (), {"column": default})() if default else None

    if auto:
        print("    [--yes] accepted" if top else "    [--yes] left unbound")
        return top.column if top else None

    while True:
        prompt = "    [Enter]=yes  n=pick another  s=leave unbound  q=quit > " if top \
                 else "    [Enter]=leave unbound  n=pick a column  q=quit > "
        ans = input(prompt).strip().lower()

        if ans == "q":
            raise SystemExit("aborted; nothing written")
        if ans == "s" or (ans == "" and not top):
            return None
        if ans == "" or ans == "y":
            return top.column if top else None
        if ans == "n":
            cols = list(profiles)
            print()
            for i, c in enumerate(cols, 1):
                mark = "*" if any(x.column == c for x in cands[:3]) else " "
                print(f"      {i:>3}{mark} {c:<42} {profiles[c].describe()}")
            print("        0  (leave unbound)")
            pick = input("    number > ").strip()
            if pick.isdigit():
                n = int(pick)
                if n == 0:
                    return None
                if 1 <= n <= len(cols):
                    return cols[n - 1]
            print("    not a valid number")
            continue
        print("    unrecognised; press Enter, or n / s / q")


def tag_status_values(df, column: str, auto: bool) -> dict:
    """
    Asked once per database: which values of the workout status column mean
    finished, and which bucket each finished value belongs to.
    Guard G3 requires that an untagged value halt the rule rather than be read
    as not-finished, so this mapping must be explicit.
    """
    values = df[column].dropna().value_counts()
    print(f"\n  Tagging the values of '{column}' (asked once per database):")
    for v, n in values.items():
        print(f"    {v!r:<28} {n:>8,} rows")

    buckets = ["open", "cure", "write-off", "payoff", "closure"]
    tags = {}
    for v in values.index:
        if auto:
            tags[str(v)] = "open"
            continue
        print(f"\n    value {v!r}")
        for i, b in enumerate(buckets, 1):
            print(f"      {i} {b}")
        pick = input("    number > ").strip()
        tags[str(v)] = buckets[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= 5 else "open"
    if auto:
        print("    [--yes] every value tagged 'open' - edit binding.json before trusting results")
    return tags


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=".", help="folder holding the KB json files and the dataset")
    ap.add_argument("--dataset", default=None, help="dataset filename (default: first xlsx/csv found)")
    ap.add_argument("--out", default=None, help="output path (default: <folder>/binding.json)")
    ap.add_argument("--yes", action="store_true", help="accept every prediction without prompting")
    ap.add_argument("--agent", action="store_true",
                    help="let the AI layer in agent.py review and override the predictions")
    args = ap.parse_args()

    folder = Path(args.folder)
    out_path = Path(args.out) if args.out else folder / "binding.json"

    kbs = D.load_kbs(folder)
    if not kbs:
        raise SystemExit(f"no KB json files found in {folder}")
    df, dataset_name = D.load_dataset(folder, args.dataset)
    profiles = D.profile(df)

    print(BAR)
    print(f"dataset : {dataset_name}   {len(df):,} rows x {len(df.columns)} columns")
    print(f"KBs     : {', '.join(kbs)}")
    print(BAR)

    dictionary, agent_results = {}, {}
    if args.agent:
        import agent as A
        dictionary = A.load_dictionary(folder, dataset_name)
        cfg = A.resolve_config()
        print(f"agent   : {cfg['model'] or '(unset)'} [{cfg['style']}], "
              f"credentials {'set' if cfg['api_key'] else 'NOT SET - offline pass'}")
        print(f"dict    : {len(dictionary)} of {len(df.columns)} columns described"
              if dictionary else "dict    : none found")
        print(BAR)
        for kb_id, kb in kbs.items():
            regex_binding = {}
            for r in D.roles_for_kb(kb):
                c = D.predict(r, df, profiles)
                regex_binding[r] = c[0].column if c else None
            res = A.run_for_kb(kb_id, kb, df, profiles, regex_binding, dictionary)
            agent_results[kb_id] = {c["role"]: c for c in res.corrections}

    binding = {"_dataset": dataset_name, "_as_of": None, "kbs": {}}

    for kb_id, kb in kbs.items():
        needed = D.roles_for_kb(kb)
        print(f"\n{BAR}\nKB: {kb_id}  -  {kb.get('diagnostic', '')}")
        print(f"{len(kb['rules'])} rules, {len(needed)} roles to bind")
        print(BAR)

        overrides = agent_results.get(kb_id, {})
        if args.agent:
            print(f"agent proposed {len(overrides)} override(s) for this KB")

        roles: dict[str, str | None] = {}
        for role in needed:
            roles[role] = choose_column(role, D.predict(role, df, profiles), profiles,
                                        args.yes, overrides.get(role))

        # Auto bindings can hand one column to two roles; the interactive path lets
        # the user allow that deliberately, but under --yes we keep the stronger
        # match and unbind the rest so a rule never reads a mis-shared column.
        if args.yes:
            roles, conflict_notes = D.resolve_conflicts(roles, df, profiles)
            for n in conflict_notes:
                print(f"  conflict: {n}")

        entry: dict = {"roles": roles}
        if overrides:
            entry["agent_overrides"] = list(overrides.values())

        if roles.get("workout_status"):
            entry["status_tags"] = tag_status_values(df, roles["workout_status"], args.yes)

        unbound = [D.ROLES[r]["label"] for r, c in roles.items() if not c]
        if unbound:
            print(f"\n  unbound: {', '.join(unbound)}")
            print("  rules needing these will return NOT-APPLICABLE, per guard CG1 / G1")

        binding["kbs"][kb_id] = entry

    if not args.yes:
        a = input("\nas-of date (YYYY-MM-DD), or Enter to use the latest period > ").strip()
        binding["_as_of"] = a or None

    out_path.write_text(json.dumps(binding, indent=2))
    print(f"\nwritten: {out_path}\nnext:    python run_tests.py --folder {args.folder}")


if __name__ == "__main__":
    main()
