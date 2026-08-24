export const EDITABLE_DIAGNOSTIC_FIELDS = [
  "name", "what_it_computes", "metric", "threshold_rule_default",
];
export const EDITABLE_CONFIGURATION_FIELDS = [
  "default_reporting_grain", "default_continuity_floor",
  "continuity_floor_label", "continuity_floor_help",
];
export const EDITABLE_RULE_FIELDS = ["title", "user_help", "severity", "next_step"];
export const REPORTING_GRAINS = ["monthly", "quarterly", "semiannual", "annual"];
export const SEVERITIES = ["CRITICAL", "MATERIAL"];

export function validateDiagnosticPackageEditor(value) {
  const errors = {};
  const required = (path, fieldValue, label) => {
    if (!String(fieldValue ?? "").trim()) errors[path] = `${label} is required.`;
  };
  required("change_summary", value?.change_summary, "Change summary");
  required("methodology", value?.methodology, "Methodology explanation");
  for (const field of EDITABLE_DIAGNOSTIC_FIELDS) {
    required(`diagnostic.${field}`, value?.diagnostic?.[field], field.replaceAll("_", " "));
  }
  const config = value?.configuration || {};
  if (!REPORTING_GRAINS.includes(config.default_reporting_grain)) {
    errors["configuration.default_reporting_grain"] = "Select a supported reporting grain.";
  }
  const floor = Number(config.default_continuity_floor);
  if (!Number.isFinite(floor) || floor < 0 || floor > 1) {
    errors["configuration.default_continuity_floor"] = "Enter a number from 0 through 1.";
  }
  required("configuration.continuity_floor_label", config.continuity_floor_label,
    "Coverage-floor label");
  required("configuration.continuity_floor_help", config.continuity_floor_help,
    "Coverage-floor guidance");
  if (!Array.isArray(value?.rules) || value.rules.length !== 6) {
    errors.rules = "The package must contain the six supported rules.";
  }
  for (const rule of value?.rules || []) {
    const prefix = `rules.${rule.rule_id}`;
    required(`${prefix}.title`, rule.title, "Rule title");
    required(`${prefix}.user_help`, rule.user_help, "User guidance");
    required(`${prefix}.next_step`, rule.next_step, "Recommended next step");
    if (!SEVERITIES.includes(rule.severity)) {
      errors[`${prefix}.severity`] = "Severity must be CRITICAL or MATERIAL.";
    }
  }
  return errors;
}

export function diagnosticPackageChanges(baseline, value) {
  if (!baseline || !value) return [];
  const changes = [];
  const compare = (path, before, after) => {
    if (before !== after) changes.push({ path, before, after });
  };
  compare("methodology", baseline.methodology, value.methodology);
  for (const field of EDITABLE_DIAGNOSTIC_FIELDS) {
    compare(`diagnostic.${field}`, baseline.diagnostic?.[field], value.diagnostic?.[field]);
  }
  for (const field of EDITABLE_CONFIGURATION_FIELDS) {
    compare(`configuration.${field}`, baseline.configuration?.[field], value.configuration?.[field]);
  }
  for (const rule of value.rules || []) {
    const original = (baseline.rules || []).find((item) => item.rule_id === rule.rule_id) || {};
    for (const field of EDITABLE_RULE_FIELDS) {
      compare(`rules.${rule.rule_id}.${field}`, original[field], rule[field]);
    }
  }
  return changes;
}
