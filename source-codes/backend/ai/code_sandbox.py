"""Plan 3 / P5 — secure code sandbox for the SME coding screen.

Hardens the inline sandbox in rca_agent.run_sandboxed:
  - ast.parse syntax validation (readable SyntaxError, never a bare 500)
  - import allowlist: blocks os / subprocess / sys / importlib and dunder /
    __import__ escapes
  - pandas / numpy-only namespace, no builtins __import__
Returns {ok, result|error, traceback} so the UI can render the traceback inline.

Phase 2 / PLT-08 note: this pass only adds type hints, docstrings, and a debug
log line per :func:`run` call (through the one logging helper,
``ai/_helper_log.py``) — no behavioural change. ``run`` is LIVE (imported by
``ai/v2/service.py``); its signature, return shape, and error handling are
unchanged.
"""
from __future__ import annotations

import ast
import builtins as _builtins
import io
import traceback as _tb
from contextlib import redirect_stdout
from typing import Any, Callable

import numpy as np
import pandas as pd

from ai._helper_log import logged_call
from ai import sandbox_capabilities as capabilities

# Roots that self-contained test code may import (e.g. `from scipy.stats import
# ks_2samp`). Everything else is refused by the import shim below — os /
# subprocess / sys etc. are also rejected by the shared capability validator.
# statsmodels / sklearn (Test Lab Phase 1) unlock the credit-risk gap battery
# (ADF/KPSS/VIF stationarity & multicollinearity; AUC/Gini decay). The AST and
# attribute guards below still apply unchanged regardless of the import root.
_ALLOWED_IMPORT_ROOTS = capabilities.IMPORT_ROOTS


def _safe_import(name: str, globals: dict | None = None, locals: dict | None = None,
                  fromlist: tuple = (), level: int = 0):
    """A locked-down ``__import__``: only the analytics allowlist, absolute only."""
    root = (name or "").split(".")[0]
    if level == 0 and root in _ALLOWED_IMPORT_ROOTS:
        return _builtins.__import__(name, globals, locals, fromlist, level)
    raise ImportError(f"Import of '{name}' is not permitted in the sandbox.")

# A minimal safe builtins set + a whitelisted __import__ (analytics modules only).
_SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
    for k in capabilities.BUILTIN_NAMES
}
_SAFE_BUILTINS["__import__"] = _safe_import


class _ForwardingBuffer(io.StringIO):
    """StringIO that also forwards writes to a callback for live terminals."""

    def __init__(self, callback: Callable[[str], None] | None = None):
        super().__init__()
        self._callback = callback

    def write(self, s: str) -> int:  # noqa: D401 - mirrors StringIO.write
        n = super().write(s)
        if self._callback and s:
            self._callback(s)
        return n


def run(code: str, df: pd.DataFrame, extra: dict[str, Any] | None = None,
        stdout_callback: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Validate + execute pandas/numpy/scipy code. ``df`` is the table; any
    ``extra`` names (e.g. ``columns``, ``params``) are injected into the sandbox
    namespace; the code should assign ``result``. Returns
    {ok, result(str)|error, result_obj(raw), stdout, traceback}.
    ``stdout_callback`` receives raw print chunks while execution is in progress.

    Logs one DEBUG line per call (name="code_sandbox.run") through the one
    logging helper; ``run`` itself never raises (every failure path returns
    ``{"ok": False, ...}``), so the logged outcome is always "ok" — the log
    line still records the call and its duration.
    """
    return logged_call(
        "code_sandbox.run", _run,
        {"code": code, "df": df, "extra": extra, "stdout_callback": stdout_callback},
    )


def _run(code: str, df: pd.DataFrame, extra: dict[str, Any] | None = None,
         stdout_callback: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The actual sandboxed-execution implementation behind :func:`run`."""
    if not code or not code.strip():
        return {"ok": False, "error": "No code provided.", "traceback": ""}
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return {"ok": False, "error": f"SyntaxError: {exc.msg} (line {exc.lineno})",
                "traceback": _tb.format_exc(limit=1)}

    errors = capabilities.validate_capabilities(code, {"pd", "np", "df"} | set(extra or {}))
    if errors:
        return {"ok": False, "error": "; ".join(errors), "traceback": ""}

    sandbox = {"pd": pd, "np": np, "df": df.copy(), "__builtins__": _SAFE_BUILTINS}
    if extra:
        sandbox.update(extra)
    buf = _ForwardingBuffer(stdout_callback)
    try:
        with redirect_stdout(buf):
            exec(compile(tree, "<sandbox>", "exec"), sandbox)  # noqa: S102
    except Exception as exc:  # noqa: BLE001 — readable traceback for the UI
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "stdout": buf.getvalue()[:8000], "traceback": _tb.format_exc(limit=3)}

    result = sandbox.get("result", None)
    stdout = buf.getvalue()
    if result is None and not stdout:
        return {"ok": True, "result": "<no `result` variable assigned>",
                "result_obj": None, "traceback": "", "stdout": stdout}
    return {"ok": True, "result": str(result)[:4000], "result_obj": result,
            "stdout": stdout[:8000], "traceback": ""}
