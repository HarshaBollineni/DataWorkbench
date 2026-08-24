"""FWK-08 / FWK-10 — staged-execution skeleton.

docs/0.4.0/03-staging-contract.md §1: execution is staged with a
dependency contract, not a flat sequential run. Stage 1 (hard/structural)
runs first and writes the value-semantics tags Stage 2 depends on; Stage 2
statistical screens whose dependency is unmet must REFUSE and report the
missing dependency rather than being silently reordered into compliance
or dropped from the run.

No statistical diagnostic is executable in slice 1, so this module is
exercised by fixture fakes in tests (3-T7), not by product paths yet.
"""
from __future__ import annotations

from typing import Any, Callable

from .guards import GuardedScope

# 'Both' diagnostics run their Stage-1 half first (they write the value-
# semantics tags Stage 2 consumes), so they sort alongside Stage 1 for
# ordering purposes even though they also have a Stage-2 half.
_STAGE_RANK = {"Stage 1": 1, "Both": 1, "Stage 2": 2}


def _stage_rank(diagnostic: dict[str, Any]) -> int:
    return _STAGE_RANK.get(diagnostic.get("stage"), 2)


def run_stage_ordered(
    diagnostics: list[dict[str, Any]],
    ctx: dict[str, Any],
    execute_fn: Callable[[dict[str, Any], dict[str, Any]], Any],
) -> list[dict[str, Any]]:
    """Execute `diagnostics` Stage-1-first (FWK-08); a stable sort so the
    relative order of same-stage diagnostics is preserved from the input.

    For every Stage-2 diagnostic that declares `depends_on` entries (e.g.
    ``["value_semantics_tags"]``), each dependency must be present in
    ``ctx["available_dependencies"]`` or the diagnostic is REFUSED
    (FWK-10) — recorded as
    ``{"diagnostic_id": ..., "refused": "missing dependency: <dep>"}`` in
    its normal stage-ordered position. It is never silently reordered
    ahead of its stage, retried, or dropped from the output. Everything
    else is handed to `execute_fn(diagnostic, ctx)`, whose return value is
    recorded as ``{"diagnostic_id": ..., "result": ...}``.
    """
    ordered = sorted(diagnostics, key=_stage_rank)
    available = ctx.get("available_dependencies", set())
    results: list[dict[str, Any]] = []
    for diagnostic in ordered:
        diag_id = diagnostic.get("diagnostic_id")
        if _stage_rank(diagnostic) == 2:
            missing = [dep for dep in diagnostic.get("depends_on", []) if dep not in available]
            if missing:
                results.append({"diagnostic_id": diag_id, "refused": f"missing dependency: {missing[0]}"})
                continue
        results.append({"diagnostic_id": diag_id, "result": execute_fn(diagnostic, ctx)})
    return results


def execute_statistical(screen_fn: Callable[[GuardedScope], Any], scope: GuardedScope) -> Any:
    """Invoke a statistical screen. `scope` MUST be a genuine `GuardedScope`
    minted by `guards.apply_guards()` — a hand-built dict/mock/bare object
    is a structural programming error (3-T6), not a data refusal, so this
    raises TypeError rather than returning a soft failure.
    """
    if not isinstance(scope, GuardedScope):
        raise TypeError(
            "execute_statistical requires a genuine GuardedScope minted by apply_guards(), "
            f"got {type(scope).__name__}"
        )
    return screen_fn(scope)
