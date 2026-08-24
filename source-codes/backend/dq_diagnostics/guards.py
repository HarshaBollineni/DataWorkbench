"""FWK-09 / D-18 — the guard chain for every STATISTICAL screen.

Guard order (docs/0.4.0/03-staging-contract.md §2), always exactly this
sequence:

  1. class_eligibility  — a metric never runs on an incompatible column class.
  2. material_fields     — a field is material only when it maps to a KB
                            semantic role, sits in the declared feature/
                            target set, or is dictionary-flagged for the use
                            case (D-18, superseding D-05). Derived at
                            runtime — never a hardcoded list.
  3. value_semantics      — never compute over values tagged censored /
                            sentinel / N-A (FWK-11).

Slice 1 ships only guard 1 as a real producer. Guards 2 and 3 have no
producer yet (materiality derivation lands with the first statistical
workflow; value-semantics tags are diagnostic #8's output, still
workflow_pending) — they raise an honest `GuardUnavailableError` rather
than silently passing everything through or being skipped. No statistical
engine may bypass this chain: `apply_guards` is the only way to mint a
`GuardedScope`, and `runner.execute_statistical` structurally refuses
anything else (3-T6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Module-private mint key. Not exported; a GuardedScope built with anything
# else raises in __post_init__, so only code inside this module (i.e.
# apply_guards) can ever produce a genuine instance.
_MINT = object()


class GuardUnavailableError(RuntimeError):
    """A guard's producer has not shipped yet (the slice-1 marker)."""


@dataclass(frozen=True)
class GuardedScope:
    """Proof that a statistical screen passed the full FWK-09 guard chain.

    Never construct this directly. The constructor demands the
    module-private mint key (`_MINT`), so a hand-built stand-in — a dict,
    a mock, a bare object with matching attributes — can never masquerade
    as a genuine scope; only `apply_guards()` can produce one.
    """

    eligible_columns: list[str]
    excluded: list[dict[str, Any]]
    _mint_key: object = field(repr=False)

    def __post_init__(self) -> None:
        if self._mint_key is not _MINT:
            raise TypeError(
                "GuardedScope must not be constructed directly — obtain one from apply_guards()"
            )


def class_eligibility(
    columns: list[str], classifications: dict[str, str], allowed_classes: set[str]
) -> tuple[list[str], list[dict[str, str]]]:
    """Guard 1 — a metric never runs on an incompatible column class.

    Returns (eligible_columns, excluded), where excluded is
    [{"column": ..., "reason": ...}] for every column whose classification
    is not in `allowed_classes` — always reported, never silently dropped.
    """
    eligible: list[str] = []
    excluded: list[dict[str, str]] = []
    for col in columns:
        cls = classifications.get(col)
        if cls in allowed_classes:
            eligible.append(col)
        else:
            excluded.append({
                "column": col,
                "reason": f"class {cls!r} not in allowed classes {sorted(allowed_classes)}",
            })
    return eligible, excluded


def material_fields(columns: list[str], ctx: dict | None = None) -> list[str]:
    """Guard 2 — the materiality filter (D-18, reinstated, superseding
    D-05). No producer exists yet: materiality must be derived at runtime
    from KB semantic roles + the declared feature/target set + dictionary
    flags, and that derivation lands with the first statistical workflow.
    Until then this guard refuses rather than pass everything through
    unfiltered."""
    raise GuardUnavailableError(
        "producer not yet available: materiality derivation (D-18) lands with the first statistical workflow"
    )


def value_semantics(columns: list[str], ctx: dict | None = None) -> list[str]:
    """Guard 3 — the value-semantics gate (FWK-11): never compute over
    values tagged censored / sentinel / N-A. No producer exists yet —
    diagnostic #8 (the classification that writes those tags) is still
    workflow_pending (backlog B1, DIA-01)."""
    raise GuardUnavailableError(
        "producer not yet available: value-semantics tags land with diagnostic #8 (backlog B1)"
    )


def apply_guards(
    columns: list[str],
    classifications: dict[str, str],
    allowed_classes: set[str],
    ctx: dict | None = None,
) -> GuardedScope:
    """Run the FWK-09 guard chain IN ORDER — class eligibility, then
    material fields, then value semantics — and mint a `GuardedScope` on
    success. This is the ONLY function that can produce a GuardedScope.

    In slice 1, guards 2 and 3 always raise `GuardUnavailableError` (no
    statistical diagnostic is executable yet), so this never actually
    succeeds in product paths today; it exists so the guard-order
    skeleton is exercised now (fixture fakes in tests) and no later
    statistical engine can ever be wired to skip it.
    """
    eligible, excluded = class_eligibility(columns, classifications, allowed_classes)
    eligible = material_fields(eligible, ctx)
    eligible = value_semantics(eligible, ctx)
    return GuardedScope(eligible_columns=eligible, excluded=excluded, _mint_key=_MINT)
