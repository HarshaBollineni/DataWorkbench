// Compatibility only for manifests created before governed KB versioning.
// New runs receive these fields from manifest.kb.rules.
export const LEGACY_ROW_COMPLETENESS_RULE_HELP = {
  "T2D6-01": "Checks that every row can be assigned to both a facility and a valid reporting period.",
  "T2D6-02": "Checks that no complete reporting period is absent between the first and last periods seen in the table.",
  "T2D6-03": "Checks how many required facility rows were received in each reporting period.",
  "T2D6-04": "Finds facility-period keys that occur more than once and distinguishes exact from conflicting records.",
  "T2D6-05": "Checks for missing periods inside each facility's own first-to-last observed reporting span.",
  "T2D6-06": "Optionally checks required facility rows within each stable segment and reporting period.",
};

export const LEGACY_FLOOR_RULES = new Set(["T2D6-03", "T2D6-05", "T2D6-06"]);
// Deprecated exports retained for older consumers and frozen-run playback.
export const ROW_COMPLETENESS_RULE_HELP = LEGACY_ROW_COMPLETENESS_RULE_HELP;
export const FLOOR_RULES = LEGACY_FLOOR_RULES;

export function rowCompletenessReviewCount(findings = []) {
  return findings.filter((finding) => finding?.outcome === "VIOLATION"
    && finding?.review_state === "open" && !finding?.existing_issue).length;
}

export function rowCompletenessValidation(manifest) {
  const issues = [];
  const roles = manifest?.roles || {};
  if (!manifest?.table) issues.push("Select a table.");
  if (!roles.facility_id?.column) issues.push("Select a facility identifier.");
  if (!roles.period?.column) issues.push("Select a reporting-period column.");
  const columns = [roles.facility_id?.column, roles.period?.column, roles.segment?.column].filter(Boolean);
  if (new Set(columns).size !== columns.length) issues.push("Each semantic role must use a different column.");
  const grain = manifest?.configuration?.reporting_grain?.value;
  if (!["monthly", "quarterly", "semiannual", "annual"].includes(grain)) {
    issues.push("Select a supported reporting grain.");
  }
  const floor = Number(manifest?.configuration?.continuity_floor?.value);
  if (!Number.isFinite(floor) || floor < 0 || floor > 1) {
    issues.push("Continuity floor must be between 0% and 100%.");
  }
  return issues;
}

export function roleSourceLabel(binding) {
  if (!binding) return "Not selected";
  return {
    governed_metadata: "Governed metadata",
    deterministic: "Deterministic suggestion",
    manual: "Manual selection",
    llm_reviewed: "LLM-reviewed selection",
  }[binding.source] || binding.source || "Unknown source";
}

export function tableOptionSummary(option) {
  const signals = [];
  if (option.facility_candidate) signals.push("facility candidate");
  if (option.period_candidate) signals.push("period candidate");
  if (option.segment_candidate) signals.push("optional segment");
  return signals.join(" · ") || "manual role selection required";
}
