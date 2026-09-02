"""Plan 8 / Aspect (b) — system-owned control-plane config for agent roles.

This is the single source of truth mapping each agent ``agent_key`` to its
``{model, house, temp_bucket, effort}``. The Python control plane owns these
knobs; users NEVER pick model/temperature/effort (they express domain intent in
the USER-OWNED slots of the skills .md — see Plan 8 Aspect a).

Model constraint (Plan 8): the only Azure deployment provisioned today is
``gpt-4.1``. Every role is therefore assigned ``gpt-4.1`` / house ``openai``.
The cross-house design (Opus orchestrator, Sonnet builder, Gemini profiler) is
the documented FUTURE intent — see the ``# FUTURE:`` note on each row. Swapping a
role to another model when it becomes available is a one-line edit here; no call
site changes. The multi-endpoint router that house-based routing needs is parked
for a follow-up plan (``llm.py`` stays single-endpoint, with the seam prepared).

Temperature policy (3 buckets, not micro-tuned): effort is the primary lever.
  deterministic -> 0.0   (profiler/ingestion, orchestration, scoring/arbitration)
  low_variance  -> 0.2   (builder, challengers — governed judgment)
  diverse       -> 0.6   (genuine idea diversity — rarely needed here)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai._helper_log import logger

# Phase 2 / PLT-08 note: this pass only adds type hints, docstrings, and a
# debug log line in :func:`resolve` (through ``ai/_helper_log.py``'s shared
# logger) — no behavioural change. This module is LIVE (``ai/effort.py``
# resolves every LLM-backed role's model/temperature/effort through it).

# 3-bucket temperature policy. The role picks a bucket; the bucket picks a temp.
TEMP_BUCKETS: dict[str, float] = {
    "deterministic": 0.0,
    "low_variance": 0.2,
    "diverse": 0.6,
}

# Effort tiers mirror ai/effort.py::TIERS plus "none" for no-LLM roles.
_VALID_EFFORT = {"none", "low", "medium", "high"}

# Default model/house while only gpt-4.1 is provisioned on Azure.
_DEFAULT_MODEL = "gpt-4.1"
_DEFAULT_HOUSE = "openai"


@dataclass(frozen=True)
class RoleCfg:
    """System-owned config for one agent role."""
    model: str
    house: str
    temp_bucket: str
    effort: str
    future_model: str = ""  # documented cross-house intent (not provisioned today)

    @property
    def temperature(self) -> float:
        return TEMP_BUCKETS[self.temp_bucket]


def _cfg(temp_bucket: str, effort: str, future_model: str = "") -> RoleCfg:
    """Build one role's :class:`RoleCfg` on today's single provisioned model.
    Raises ``ValueError`` for an unknown ``temp_bucket``/``effort`` name."""
    if temp_bucket not in TEMP_BUCKETS:
        raise ValueError(f"Unknown temp bucket: {temp_bucket!r}")
    if effort not in _VALID_EFFORT:
        raise ValueError(f"Unknown effort: {effort!r}")
    return RoleCfg(
        model=_DEFAULT_MODEL, house=_DEFAULT_HOUSE,
        temp_bucket=temp_bucket, effort=effort, future_model=future_model,
    )


# Role -> config. Keys mirror ai/skills.py::AGENTS agent_key values.
# Collapsed to gpt-4.1 today; ``future_model`` records the brainstorm's
# cross-house target so the eventual swap is obvious and auditable.
ROLE_CONFIG: dict[str, RoleCfg] = {
    # Orchestrator (referee): executive logic, no code. Deterministic + high effort.
    "gauss":    _cfg("deterministic", "high", future_model="claude-opus-4-8"),
    # Profiler / ingestion: pure metadata ingestion. Cost+context, not diversity.
    # (Fisher retired — Newton's DiscoveryState owns profiling; no role config needed.)
    "newton":   _cfg("deterministic", "high", future_model="gpt-4.1-mini"),
    # Credit-risk domain expert (regulatory + non-regulatory): owns the
    # 'potential usages' lens of the DB summary. Domain judgment -> low variance,
    # high effort; deep domain reasoning is the eventual cross-house target.
    "merton":   _cfg("low_variance", "high", future_model="claude-opus-4-8"),
    # Builder (test synthesis lives in Gauss loop): reliable stats/py, LOW temp.
    # (Gauss row above is the builder/orchestrator; challengers below.)
    # Local challenger (within-table): cheap independent lens. low-variance.
    "fermat":   _cfg("low_variance", "high", future_model="gpt-4.1-mini"),
    # Global challenger (cross-table): holds disparate architectures. low-variance.
    "euler":    _cfg("low_variance", "high", future_model="claude-sonnet-4-6"),
    # Relationship discovery feeds the challengers; governed judgment.
    "poincare": _cfg("low_variance", "high", future_model="gpt-4.1-mini"),
    # Cross-table ERD / join inference (Data Sourcing). The deterministic evidence
    # harness is the authoritative gate — Codd only annotates/confirms it — so a
    # SINGLE medium pass is right; 'high' (3 drafts + merge = 4 chained calls) made
    # "Generate with AI" run for minutes for no quality gain (Feedback R6 RCA).
    "codd":     _cfg("low_variance", "medium", future_model="claude-sonnet-4-6"),
    # Library screening + deep search.
    "hypatia":  _cfg("low_variance", "medium"),
    # Criticality ranking — judgment, low variance.
    "pascal":   _cfg("low_variance", "medium"),
    # RCA reasoning + effective challenge — hard cognition, low temp + high effort.
    "feynman":  _cfg("deterministic", "high", future_model="claude-opus-4-8"),
    "noether":  _cfg("low_variance", "high", future_model="gpt-4.1-mini"),
    # Test Lab HITL feedback adjudicator (the "shuttle"): weighs human feedback as
    # evidence and either accommodates or compassionately pushes back. Judgment +
    # empathy -> low variance, high effort; deep reasoning is the cross-house target.
    "bayes":    _cfg("low_variance", "high", future_model="claude-opus-4-8"),
    # Cross-field role mapping verification is an explicitly enabled,
    # pre-freeze provenance check.  It never evaluates a rule or chooses a
    # verdict, so use the deterministic bucket and a single bounded pass.
    "role_mapping_verifier": _cfg("deterministic", "low", future_model="gpt-4.1-mini"),
    "directionality_semantic_adjudicator": _cfg("deterministic", "low", future_model="gpt-4.1-mini"),
    # Deterministic / no-LLM roles.
    "laplace":  _cfg("deterministic", "none"),
    "context_memory_broker": _cfg("deterministic", "none"),
}

# Default applied when an unknown agent_key is resolved (defensive; logged by callers).
_FALLBACK = _cfg("low_variance", "medium")


def get_role_cfg(agent_key: str) -> RoleCfg:
    """Return the system-owned config for a role, or a safe fallback."""
    return ROLE_CONFIG.get((agent_key or "").lower(), _FALLBACK)


def resolve(agent_key: str) -> dict[str, Any]:
    """Flatten a role's config to the knobs call sites need.

    Returns ``{model, temperature, effort, house}``. ``llm.py`` consumes
    ``model``; ``effort.py`` consumes ``temperature`` + ``effort``.
    """
    cfg = get_role_cfg(agent_key)
    logger.debug(
        "helper=control_plane.resolve context_id=%s outcome=ok model=%s effort=%s",
        agent_key or "-", cfg.model, cfg.effort,
    )
    return {
        "model": cfg.model,
        "temperature": cfg.temperature,
        "effort": cfg.effort,
        "house": cfg.house,
    }
