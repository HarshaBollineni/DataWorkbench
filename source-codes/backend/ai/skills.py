"""Plan 3 / F6 + P7 — agent registry + skills-.md convention.

Each agent's system prompt is stored in backend/skills/<agent_key>.md. On first
use the agent's built-in default is written there; thereafter the (editable) .md
is the source of truth, so an edit in the Agentic Skills console changes the
prompt the agent loads on its next run.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# Baked-in defaults always ship at backend/skills (agent prompts + the demo DBs'
# generated understanding artifacts). In production SKILLS_DIR is pointed at a
# persistent volume (Azure Files, e.g. /data/skills) via the SKILLS_DIR env var
# so agent-prompt edits AND generated DB-understanding artifacts survive a
# container restart. On first boot the empty volume is seeded from the baked-in
# defaults; existing files are never overwritten, so user edits are preserved.
_DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR") or _DEFAULT_SKILLS_DIR)
SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _seed_skills_dir() -> None:
    """Copy baked-in prompts + artifacts into an external SKILLS_DIR on first
    boot. Only .md/.json files not already present are copied, so a restart
    never clobbers edits made through the Agentic Skills console."""
    if SKILLS_DIR.resolve() == _DEFAULT_SKILLS_DIR.resolve():
        return  # local dev: SKILLS_DIR is the baked-in dir itself
    if not _DEFAULT_SKILLS_DIR.is_dir():
        return
    for src in _DEFAULT_SKILLS_DIR.iterdir():
        if src.is_file() and src.suffix in (".md", ".json"):
            dst = SKILLS_DIR / src.name
            if not dst.exists():
                shutil.copy2(src, dst)


_seed_skills_dir()

# F6 topology with fancy call-names. parent_key=None -> top-level/standalone.
# `seq` (Plan 6) is the explicit render/execution order so the Gauss sub-agents
# appear in their true pipeline sequence (Poincaré -> Fermat -> Euler
# -> Hypatia) rather than alphabetically.
# `descriptor` (Feedback R4.1) is a short, human-facing label shown in the UI as
# "Agent <call_name> (<descriptor>)" everywhere agents are named (Agent Console,
# Agentic Skills, AI attributions). One source of truth lives here.
AGENTS: list[dict] = [
    {"agent_key": "gauss", "call_name": "Gauss", "role": "New Test Manager (Orchestrator)",
     "descriptor": "test-manager", "parent_key": None, "effort_tier": "high", "seq": 1},
    {"agent_key": "poincare", "call_name": "Poincaré", "role": "Relationship Discovery",
     "descriptor": "relationship-discovery", "parent_key": "gauss", "effort_tier": "high", "seq": 3},
    {"agent_key": "fermat", "call_name": "Fermat", "role": "Relationship Validation (within table)",
     "descriptor": "relationship-validator", "parent_key": "gauss", "effort_tier": "high", "seq": 4},
    {"agent_key": "euler", "call_name": "Euler", "role": "Global Consistency (beyond table/db)",
     "descriptor": "consistency-checker", "parent_key": "gauss", "effort_tier": "high", "seq": 5},
    {"agent_key": "hypatia", "call_name": "Hypatia", "role": "Test Screening (library + deep search)",
     "descriptor": "test-screener", "parent_key": "gauss", "effort_tier": "medium", "seq": 6},
    {"agent_key": "pascal", "call_name": "Pascal", "role": "Criticality (Importance ranking)",
     "descriptor": "criticality-ranker", "parent_key": None, "effort_tier": "medium", "seq": 7},
    {"agent_key": "feynman", "call_name": "Feynman", "role": "Root Cause Analysis",
     "descriptor": "root-cause-analyst", "parent_key": None, "effort_tier": "high", "seq": 8},
    {"agent_key": "noether", "call_name": "Noether", "role": "RCA Checker (effective challenge)",
     "descriptor": "RCA-challenger", "parent_key": "feynman", "effort_tier": "high", "seq": 9},
    {"agent_key": "newton", "call_name": "Newton", "role": "Database Understanding",
     "descriptor": "data-profiler", "parent_key": None, "effort_tier": "high", "seq": 10},
    {"agent_key": "merton", "call_name": "Merton",
     "role": "Credit-Risk Domain Expert (potential usages)",
     "descriptor": "credit-SME", "parent_key": None, "effort_tier": "high", "seq": 11},
    {"agent_key": "codd", "call_name": "Codd",
     "role": "Relationship Architect (cross-table ERD / join inference)",
     "descriptor": "relationship-architect", "parent_key": None, "effort_tier": "high", "seq": 12},
    {"agent_key": "laplace", "call_name": "Laplace", "role": "Health Score (deterministic, no AI math)",
     "descriptor": "health-scorer", "parent_key": None, "effort_tier": "none", "seq": 13},
    {"agent_key": "context_memory_broker", "call_name": "Context Memory Broker",
     "role": "Object context memory retrieval and usage policy",
     "descriptor": "memory-broker", "parent_key": None, "effort_tier": "none", "seq": 14},
    {"agent_key": "bayes", "call_name": "Bayes",
     "role": "Test Feedback Adjudicator (HITL Shuttle)",
     "descriptor": "feedback-adjudicator", "parent_key": "gauss", "effort_tier": "high", "seq": 15},
]


_DESCRIPTIONS: dict[str, str] = {
    "gauss": (
        "Orchestrates the full test-authoring workflow from field selection to validated Python code.\n"
        "Coordinates sub-agents through discovery and validation cycles, converging when consensus is reached or the effort budget is exhausted.\n"
        "• Inputs: table, field selection, logical database\n"
        "• Sub-agents: Newton → Poincaré → Fermat + Euler → Hypatia\n"
        "• Output: runnable DQ test (Python) with a business narrative"
    ),
    "newton": (
        "Two-phase database profiler. Phase 1 is deterministic: pandas column statistics, null rates, cardinality, FK heuristics, and cross-table join candidates — saved as a DiscoveryState JSON.\n"
        "Phase 2 is an LLM narrative summarising findings and recommending test focus areas.\n"
        "• Persists to: skills/db_discovery_{db}.json\n"
        "• Downstream consumers: Poincaré, Fermat, Euler"
    ),
    "poincare": (
        "Discovers candidate DQ relationships: primary keys, foreign-key joins, referential integrity rules, and cross-table associations.\n"
        "Uses Newton's DiscoveryState when available (richer cross-table profile); otherwise falls back to single-table screening.\n"
        "• Output: ranked relationship candidates passed to Fermat and Euler for validation"
    ),
    "fermat": (
        "Validates each Poincaré candidate from a within-table perspective.\n"
        "Checks column constraints, domain ranges, conditional logic, and cardinality rules against the data.\n"
        "• Issues accept/reject verdicts with detailed reasoning\n"
        "• Verdicts feed Gauss's convergence check alongside Euler"
    ),
    "euler": (
        "Provides global cross-table consistency checking for each relationship candidate.\n"
        "Reviews referential integrity, join cardinality balance, and temporal alignment across tables.\n"
        "• Issues accept/reject verdicts complementing Fermat's within-table challenge\n"
        "• Both validators must accept a candidate for consensus"
    ),
    "hypatia": (
        "Searches the existing test library for tests already applicable to the selected fields before Gauss authors a new one.\n"
        "Performs a deep search when no direct match is found.\n"
        "• Prevents test duplication\n"
        "• Recommended library tests are attached to the authored test for traceability"
    ),
    "feynman": (
        "Orchestrates the Root Cause Analysis workflow for failed DQ tests.\n"
        "Iteratively analyses test output, row-level data, and column statistics using sandboxed Python tools.\n"
        "• Produces a structured RCA report with evidence and remediation suggestions\n"
        "• Noether acts as effective challenge on Feynman's conclusions"
    ),
    "noether": (
        "Reviews Feynman's RCA reasoning chain for logical gaps, unsupported inferences, or insufficient evidence.\n"
        "Issues a structured verdict: accepts, refines, or rejects the proposed root cause.\n"
        "• Prevents poorly-evidenced conclusions from reaching the user"
    ),
    "pascal": (
        "Ranks DQ tests by business criticality and operational impact, incorporating domain context and admin-defined priorities.\n"
        "• Output: criticality score and tier — critical / high / medium / low\n"
        "• Used to prioritise remediation effort and test scheduling"
    ),
    "merton": (
        "Credit-risk domain expert mapping database columns to regulatory and business concepts (LGD, PD, EAD, Basel metrics).\n"
        "Highlights domain-specific anomalies and risk-sensitive fields.\n"
        "• Guides test selection and result interpretation for credit portfolios\n"
        "• Activated when an ingested database is tagged with the 'credit' domain"
    ),
    "codd": (
        "Infers entity-relationship structure from schema and data evidence beyond simple column-name heuristics.\n"
        "Proposes cross-table joins, entity mappings, and ERD representations.\n"
        "• Output consumed by Poincaré and downstream test authoring agents\n"
        "• Named for E.F. Codd, father of the relational model"
    ),
    "laplace": (
        "Deterministic health scorer — no LLM.\n"
        "Aggregates pass/fail/skip test results into a weighted health score and computes trend deltas against the previous run baseline.\n"
        "• Pure arithmetic: reproducible and auditable\n"
        "• Score appears on the DQ Dashboard and per-table health tiles"
    ),
    "context_memory_broker": (
        "Retrieves stored object-level context for tables and columns and enforces the memory usage policy.\n"
        "Determines what context is safe to surface versus must be re-verified before use.\n"
        "• Bridges session-level context to agent prompts\n"
        "• Prevents stale facts from being presented as current"
    ),
    "bayes": (
        "Adjudicates human feedback on a designed test in a back-and-forth 'shuttle'. Weighs each "
        "comment on its merits and either accommodates it (revising the test + dossier as a new "
        "version) or pushes back with a compassionate, reasoned explanation.\n"
        "• Inputs: current test state (code, params, dossier) + the human's feedback note\n"
        "• Output: a verdict (accommodate / push_back) + an empathetic message + any proposed changes\n"
        "• Every accepted change is versioned so the human can undo / redo"
    ),
}


def agent_description(agent_key: str) -> str:
    return _DESCRIPTIONS.get(agent_key, "")


# agent_key -> (module path, attribute holding the default system prompt)
_DEFAULT_SOURCES = {
    "poincare": ("ai.agents.relationship_discovery", "_SYSTEM"),
    "fermat": ("ai.agents.relationship_validation", "_SYSTEM"),
    "euler": ("ai.agents.global_consistency", "_SYSTEM"),
    "noether": ("ai.rca_checker", "_SYSTEM"),
    "newton": ("ai.db_understanding", "_SYSTEM"),
    "merton": ("ai.credit_risk_domain", "_SYSTEM"),
    "codd": ("ai.agents.relationship_join", "_SYSTEM"),
    "feynman": ("ai.prompts", "RCA_SYSTEM"),
    "bayes": ("ai.prompts", "BAYES_SYSTEM"),
}


# Plan 8 (Aspect a): the canonical agent prompts are governed by the FORM
# contract (ai/skill_form.py) — validated strictly at load, SYSTEM block locked
# on save. Generated DB-understanding summaries and session variants are NOT
# governed.
GOVERNED_KEYS = {a["agent_key"] for a in AGENTS}


def _role_of(agent_key: str) -> str:
    for a in AGENTS:
        if a["agent_key"] == agent_key:
            return a["role"]
    return agent_key


def md_path(agent_key: str) -> Path:
    return SKILLS_DIR / f"{agent_key}.md"


def default_prompt(agent_key: str) -> str:
    """Best-effort recovery of an agent's built-in default system prompt."""
    src = _DEFAULT_SOURCES.get(agent_key)
    if not src:
        return ""
    try:
        mod = __import__(src[0], fromlist=[src[1]])
        return getattr(mod, src[1], "")
    except ImportError:
        return ""


def get_system_prompt(agent_key: str, default: str) -> str:
    """Return the agent's system prompt from its .md, seeding it on first use.

    Governed keys (the canonical agents) are validated STRICTLY against the FORM
    contract: a malformed existing file raises so the violation surfaces loudly
    rather than feeding a broken prompt to the model. On first use the built-in
    default is wrapped into a valid FORM before being written + returned."""
    from . import skill_form

    p = md_path(agent_key)
    governed = agent_key in GOVERNED_KEYS
    if p.exists():
        text = p.read_text(encoding="utf-8").strip()
        if text:
            if governed:
                skill_form.validate(text)  # raises SkillFormError if malformed
            return text
    if governed:
        text = skill_form.wrap_default(agent_key, _role_of(agent_key), default)
    else:
        text = default
    p.write_text(text, encoding="utf-8")
    return text


def save_prompt(agent_key: str, text: str) -> None:
    """Persist an edited prompt. For governed agents the SYSTEM-OWNED block is
    read-only: the edit must keep it byte-for-byte; only USER-OWNED text changes."""
    from . import skill_form

    p = md_path(agent_key)
    if agent_key in GOVERNED_KEYS and p.exists():
        current = p.read_text(encoding="utf-8")
        # Raises SkillFormError if the edit alters the locked SYSTEM block or is
        # malformed; returns the safely-merged document otherwise.
        text = skill_form.merge_user_edit(current, text)
    p.write_text(text, encoding="utf-8")


def architecture_map() -> dict:
    """Return the .py/.md coexistence map rendered in Agentic Skills."""
    nodes = [
        {"id": "gauss_py", "label": "Gauss", "kind": "py",
         "status": "locked", "role": "New Test Manager orchestrator"},
        {"id": "poincare_md", "label": "Poincare", "kind": "md",
         "status": "editable", "role": "Relationship discovery prompt",
         "parent": "gauss_py", "agent_key": "poincare"},
        {"id": "fermat_md", "label": "Fermat", "kind": "md",
         "status": "editable", "role": "Within-table challenge prompt",
         "parent": "gauss_py", "agent_key": "fermat"},
        {"id": "euler_md", "label": "Euler", "kind": "md",
         "status": "editable", "role": "Global consistency prompt",
         "parent": "gauss_py", "agent_key": "euler"},
        {"id": "hypatia_md", "label": "Hypatia", "kind": "md",
         "status": "editable", "role": "Library screening prompt",
         "parent": "gauss_py", "agent_key": "hypatia"},
        {"id": "gauss_md", "label": "Gauss synthesis prompt", "kind": "md",
         "status": "editable", "role": "Final test authoring prompt",
         "parent": "gauss_py", "agent_key": "gauss"},
        {"id": "feynman_py", "label": "Feynman RCA loop", "kind": "py",
         "status": "locked", "role": "RCA orchestration, tool calls, HITL state"},
        {"id": "feynman_md", "label": "Feynman prompt", "kind": "md",
         "status": "editable", "role": "RCA reasoning prompt",
         "parent": "feynman_py", "agent_key": "feynman"},
        {"id": "memory_md", "label": "Context Memory Broker", "kind": "md",
         "status": "editable", "role": "Object-memory usage policy",
         "parent": "feynman_py", "agent_key": "context_memory_broker"},
        {"id": "helper_py", "label": "RCA Helper Registry", "kind": "py",
         "status": "locked", "role": "Vetted helper methods and quarantine validation",
         "parent": "feynman_py"},
        {"id": "sandbox_py", "label": "Code Sandbox", "kind": "py",
         "status": "locked", "role": "AST/import guarded pandas execution",
         "parent": "feynman_py"},
        {"id": "noether_py", "label": "Noether checker call", "kind": "py",
         "status": "locked", "role": "RCA effective-challenge execution",
         "parent": "feynman_py"},
        {"id": "noether_md", "label": "Noether prompt", "kind": "md",
         "status": "editable", "role": "Effective challenge prompt",
         "parent": "noether_py", "agent_key": "noether"},
        {"id": "tickets_py", "label": "Issue Management", "kind": "py",
         "status": "locked", "role": "Ticket APIs and RCA linkage",
         "parent": "feynman_py"},
    ]
    return {
        "legend": {
            "py": "Locked Python executor/orchestrator. Enforces contracts and side effects.",
            "md": "Editable markdown prompt/policy. Describes behavior consumed by Python call sites.",
        },
        "nodes": nodes,
        "edges": [{"from": n["parent"], "to": n["id"]} for n in nodes if n.get("parent")],
    }
