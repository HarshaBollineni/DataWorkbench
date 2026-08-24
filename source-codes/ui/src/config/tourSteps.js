// Plan 3 / D9.6 — guided product tour steps. Generic {target, content} contract
// (mapped to the spotlight lib in ProductTour) so the tour engine can be swapped
// without touching the copy.
export const tourSteps = [
  {
    target: ".tour-sidebar",
    content:
      "Welcome to AegisDQ. This is your command center for navigating the 4-step Agentic assessment workflow.",
  },
  {
    target: ".tour-data-sourcing",
    content:
      "Start by connecting your database. Our DB-Understanding Agent will instantly ingest and summarize your schema.",
  },
  {
    target: ".tour-ai-tests",
    content:
      "Our autonomous agents have screened your variables and discovered relationships to recommend these specific stability and distribution checks.",
  },
  {
    target: ".tour-health-score",
    content:
      "This score is a deterministic, rule-weighted aggregation of passed tests. AI does not calculate this math; it only ranks criticality.",
  },
  {
    target: ".tour-code-workbench",
    content:
      "A secure, sandboxed Python environment where SMEs can review AI-generated root cause analysis and edit mitigation code.",
  },
];
