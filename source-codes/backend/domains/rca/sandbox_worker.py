"""Private child-process entry point for generated RCA analysis code."""
from __future__ import annotations

import io
import json
import sys
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

from ai import code_sandbox


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def main() -> None:
    payload = json.loads(sys.stdin.read())
    frame = pd.read_json(io.StringIO(payload["frame"]), orient="table")
    started = perf_counter()
    outcome = code_sandbox.run(
        str(payload.get("code") or ""), frame, {"params": payload.get("params") or {}}
    )
    outcome["analysis_seconds"] = round(perf_counter() - started, 3)
    sys.stdout.write(json.dumps(outcome, default=_json_default))


if __name__ == "__main__":
    main()
