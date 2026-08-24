"""Central app config — single source of truth for cross-cutting UI constants.

Feedback Round 4 (2026-06-29): AI-generated work must carry a consistent
disclaimer wherever it appears, stored centrally rather than copy-pasted into
components. This module is the one place those cross-cutting constants live; the
frontend reads them once via ``GET /api/config`` so backend and React never
drift. Add new app-wide knobs here, not scattered in pages.
"""
from __future__ import annotations

# Shared disclaimer shown beneath ANY AI-generated content (one source of truth).
AI_DISCLAIMER = (
    "AI-generated content may be inaccurate or incomplete. Please review and "
    "edit before relying on it."
)

# Character budget for the AI database-summary text box (four paragraphs: Newton ×2, Merton, manual).
# Allocations (targeting 50–90% utilisation): stats ~350, profiling ~600, usage ~400, manual ~100 = ~1 450 chars.
AI_SUMMARY_CHAR_LIMIT = 1500

# Attribution shown on AI surfaces so users know which agent + model produced it.
# R7: the AI Understanding surfaces use the neutral ``brand`` credit (the internal
# agent code-names Newton/Merton are no longer shown to the user there); the agent
# keys remain for other surfaces (e.g. the dictionary) and internal routing.
AI_ATTRIBUTION = {
    "brand": "Aegis Labs AI",
    "provider": "Azure OpenAI",
    "model": "gpt-4.1",
    "database_understanding_agent": "Newton",
    "credit_risk_domain_agent": "Merton",
    "dictionary_agent": "Newton",
}


def public_config() -> dict:
    """The config surface served to the frontend (GET /api/config)."""
    return {
        "ai_disclaimer": AI_DISCLAIMER,
        "ai_summary_char_limit": AI_SUMMARY_CHAR_LIMIT,
        "ai_attribution": AI_ATTRIBUTION,
    }
