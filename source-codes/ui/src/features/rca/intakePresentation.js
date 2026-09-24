const present = (value) => value !== null && value !== undefined && value !== "";
const scalar = (value) => present(value) && ["string", "number"].includes(typeof value)
  && (typeof value !== "number" || Number.isFinite(value));
const strings = (value) => (Array.isArray(value) ? value : [value]).filter((item) => typeof item === "string" && item.trim());

// Adapt retained evidence only. Never infer a verdict from issue lifecycle status,
// guess a threshold comparator, or turn a profile statistic into a failure measure.
export function intakePresentation(issue = {}) {
  const source = issue.source_evidence || {};
  const metric = source.metrics || {};
  const finding = source.finding || {};
  const isPsi = Number(source.diagnostic_id) === 14 || metric.result_kind === "psi_feature";
  const isFeatureTarget = Number(source.diagnostic_id) === 2;
  const rawStatus = String(finding.outcome || source.verdict || finding.classification || metric.classification || source.decision_type || "").toLowerCase().replace(/[ -]+/g, "_");
  const unavailable = ["not_runnable", "not_applicable", "insufficient_data", "not_evaluated", "cannot_evaluate", "na", "error"].includes(rawStatus)
    || Boolean(source.na_reason || finding.na_reason);
  const status = unavailable ? "Could not evaluate"
    : ["fail", "failed", "failure", "breach"].includes(rawStatus) ? "Failed"
      : rawStatus === "investigate" || (isPsi && metric.classification === "investigate") ? "Investigation recommended"
      : ["watch", "warning", "warn", "contextual", "review"].includes(rawStatus) ? "Review required"
        : ["pass", "passed", "stable"].includes(rawStatus) ? "Passed"
          : "Outcome not specified";
  const facts = [];
  const add = (label, value) => { if (scalar(value) && facts.length < 3) facts.push([label, value]); };
  if (!unavailable) {
    if (isPsi) {
      add("Population stability index", metric.psi ?? issue.metric);
      add("Investigate boundary", metric.thresholds?.investigate);
    } else if (isFeatureTarget) {
      add("AUC", metric.auc ?? metric.roc_detail?.primary_auc ?? issue.metric);
      add("Information value", metric.iv);
    } else {
      add("Observed measure", finding.rate ?? issue.metric ?? metric.metric);
      add("Declared threshold", finding.tolerance);
    }
    add("Reported exceptions", finding.violation_count ?? issue.violation_count);
  }
  const limitations = [...new Set([
    ...strings(source.na_reason), ...strings(finding.na_reason),
    ...strings(metric.limitations), ...strings(metric.structured_result?.limitations),
    ...strings(metric.status_reason),
  ])];
  if (!issue.source_evidence) limitations.push("Detailed source evidence was not retained for this issue.");
  if (!facts.length && !unavailable) limitations.push("No supported summary measures are available; inspect the retained diagnostic evidence.");
  const profile = source.data_profile;
  if (profile && !profile.special_values_confirmed && (profile.declared_special_values?.length || profile.special_values?.length)) {
    limitations.push("Proposed special values are unconfirmed; they must not be treated as governed exclusions.");
  }
  const scope = [
    ["Dataset", issue.item_name], ["Table", issue.table_name || source.entity_or_table],
    ["Fields", (issue.columns || []).join(", ")],
    ["Rows evaluated", source.scope_counts?.rows_evaluated],
    ["Rows skipped", source.scope_counts?.rows_skipped],
    ["Baseline records", source.population_context?.preview?.baseline_count],
    ["Current records", source.population_context?.preview?.current_count],
    ["Excluded records", source.population_context?.preview?.excluded_count],
  ].filter(([, value]) => scalar(value));
  return {
    isPsi, isFeatureTarget, status, unavailable, facts, scope, limitations,
    summary: finding.rule_text || (unavailable
      ? "The diagnostic could not establish an evaluable result for this scope."
      : `Review the retained ${issue.test_name || "diagnostic"} result before investigating possible causes.`),
    causeNote: "This evidence describes the observed condition; it does not establish its cause.",
  };
}
