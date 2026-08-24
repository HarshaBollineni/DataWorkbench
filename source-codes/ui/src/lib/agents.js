// Global agent display convention (Feedback R4.1): every agent is shown as
// "Agent <Name> (<descriptor>)" wherever agents are named in the UI. Descriptors
// mirror the backend roster (ai/skills.py AGENTS) — this map is the frontend
// fallback so non-roster surfaces (e.g. the live Agent Console, which only knows
// the call_name) render consistently.
// RET-04 (0.5.0) — no external caller anywhere in ui/src; used only by
// agentLabel() below (which *is* imported externally). Stopped being
// exported rather than deleted.
const AGENT_DESCRIPTORS = {
  Gauss: "test-manager",
  Fisher: "variable-screener",
  "Poincaré": "relationship-discovery",
  Poincare: "relationship-discovery",
  Fermat: "relationship-validator",
  Euler: "consistency-checker",
  Hypatia: "test-screener",
  Pascal: "criticality-ranker",
  Feynman: "root-cause-analyst",
  Noether: "RCA-challenger",
  Newton: "data-profiler",
  Merton: "credit-SME",
  Codd: "relationship-architect",
  Laplace: "health-scorer",
  "Context Memory Broker": "memory-broker",
}

// Galileo v2 agents stream generic snake_case keys on SSE (routers/v2.py).
// Display names mirror the backend roster (ai/v2/agents_roster.py AGENTS).
// RET-04 (0.5.0) — same shape as AGENT_DESCRIPTORS above: no external
// caller, used only by agentLabel() below. Stopped being exported.
const V2_AGENT_NAMES = {
  data_profiling: "Data Profiler",
  test_planning: "Test Planner",
  snippet_generation: "Snippet Author",
  snippet_execution: "Sandbox Executor",
  recommendation: "Recommendation Agent",
  cross_validation: "Cross-Validator",
  rca: "RCA Agent",
  report: "Report Builder",
  // Phase 6 (0.4.0) — the cross-field diagnostic's SSE `agent` field
  // (dq_diagnostics/runner_cross_field.py).
  cross_field_engine: "Cross-Field Engine",
}

/** Format an agent as "Agent <Name> (<descriptor>)". v2 SSE keys resolve to
 *  their roster display names; otherwise falls back to the static descriptor
 *  map, omitting the parens if none is known. */
export function agentLabel(name, descriptor) {
  if (!name) return ""
  if (V2_AGENT_NAMES[name]) return `Agent ${V2_AGENT_NAMES[name]}`
  const d = descriptor || AGENT_DESCRIPTORS[name]
  return d ? `Agent ${name} (${d})` : `Agent ${name}`
}
