"""
dq.py - single entry point (dispatcher) for the data-quality toolkit.

Each subcommand routes to an existing module UNCHANGED; all arguments after the
subcommand are passed straight through, so every module keeps its own CLI.

  bind      role binding for KB tests                      -> bind_cli.py
  test      run KB tests (completeness / resolutionrate)   -> run_tests.py
  metrics   PSI + AUC / Gini (statistical tests)           -> dq_metrics.py
  agent     AI binding review (optional)                   -> agent.py
  propose   AI incremental-check proposal loop             -> ai_incremental.py
  rca       root-cause analysis (stage 1)                  -> rca_orchestrator.py

Examples
--------
  python dq.py bind    --folder . --yes
  python dq.py test    --binding binding.json --product MORTGAGE
  python dq.py metrics --files mr_pd_sample.csv --target target_default_12m
  python dq.py propose --test completeness --dataset mr_pd_sample.csv
  python dq.py rca     --dataset mr_lgd_censored.csv --failure failure.json
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROUTES = {
    "bind": "bind_cli.py",
    "test": "run_tests.py",
    "metrics": "dq_metrics.py",
    "agent": "agent.py",
    "propose": "ai_incremental.py",
    "rca": "rca_orchestrator.py",
}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        print("subcommands:", ", ".join(ROUTES))
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in ROUTES:
        print(f"unknown subcommand '{cmd}'. choose one of: {', '.join(ROUTES)}")
        return 2
    script = Path(__file__).with_name(ROUTES[cmd])
    return subprocess.run([sys.executable, str(script), *rest]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
