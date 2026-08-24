"""FWK-18 — the DiagnosticEngine protocol: the extension seam.

testlab-redesign-0.4.0.md §5.1: every future workflow lands on this shape.
A workflow-pending diagnostic simply HAS NO ENGINE, and
``dq_diagnostics.register.require_executable`` answers for it with the
refusal message — so enabling a new diagnostic is one engine plus one
register flip, never a runner/manifest/UI change.

Slice 1 registers exactly one implementation (#4, cross-field). The
registry below is deliberately explicit and tiny: an engine is looked up by
its register key, and an unknown/unimplemented key raises rather than
silently returning a no-op.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

READINESS_STATES = ("ready", "not_applicable", "blocked")


@dataclass(frozen=True)
class Readiness:
    """The deterministic precondition verdict for one (item, diagnostic).

    ``status`` is one of :data:`READINESS_STATES`; ``reason`` is mandatory
    for everything except ``ready`` — an unexplained refusal is exactly the
    dishonesty FWK-14/15 exists to prevent.
    """

    status: str
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in READINESS_STATES:
            raise ValueError(f"readiness status must be one of {READINESS_STATES}, got {self.status!r}")
        if self.status != "ready" and not (self.reason or "").strip():
            raise ValueError(f"readiness status {self.status!r} requires a reason")

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "detail": self.detail}


@runtime_checkable
class DiagnosticEngine(Protocol):
    """What every diagnostic engine implements.

    ``execute`` is deterministic and read-only against the dataset, and
    emits progress through the injected ``emit`` callback rather than
    printing or streaming on its own (CFR-01/13/15/16).
    """

    diagnostic_id: int
    engine_version: str

    def readiness(self, item: dict[str, Any], ctx: dict[str, Any]) -> Readiness:
        ...

    def build_manifest(self, item: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        ...

    def execute(self, manifest: dict[str, Any], ctx: dict[str, Any],
                emit: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        ...


_ENGINES: dict[int, DiagnosticEngine] = {}


class EngineNotImplementedError(RuntimeError):
    """No engine is registered for a diagnostic id. Distinct from
    `register.WorkflowPendingError`: that one answers for a *registered*
    diagnostic whose workflow is not defined; this one is the internal
    lookup failure behind it."""


def register_engine(engine: DiagnosticEngine) -> DiagnosticEngine:
    _ENGINES[engine.diagnostic_id] = engine
    return engine


def get_engine(diagnostic_id: int) -> DiagnosticEngine:
    engine = _ENGINES.get(diagnostic_id)
    if engine is None:
        raise EngineNotImplementedError(f"no engine registered for diagnostic {diagnostic_id}")
    return engine


def registered_ids() -> list[int]:
    return sorted(_ENGINES)
