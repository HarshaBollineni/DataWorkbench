import { formatDisplayNumber } from "../../lib/numberFormat.js";

export const highConfidenceHeaderMapping = (inspection) => Object.fromEntries(
  (inspection?.fields || [])
    .filter((field) => field.confidence === "high" && field.source_column)
    .map((field) => [field.name, field.source_column]),
);

const normalizeSearchText = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

export function inferTaxonomyValue(options, corpus, aliases = []) {
  const normalized = normalizeSearchText(corpus);
  return options.find((option) => {
    const candidates = [option.label, option.key, ...(aliases.filter(([key]) => key === option.key || key === option.label).flatMap(([, values]) => values))];
    return candidates.some((candidate) => candidate && normalized.includes(normalizeSearchText(candidate)));
  })?.label || "";
}

export function periodDate(value, end = false, quarterLike = false) {
  const text = String(value || "").trim();
  const quarter = text.match(/(\d{4})\D*Q([1-4])/i);
  if (quarter) {
    const year = Number(quarter[1]);
    const quarterNumber = Number(quarter[2]);
    const date = end
      ? new Date(Date.UTC(year, quarterNumber * 3, 0))
      : new Date(Date.UTC(year, (quarterNumber - 1) * 3, 1));
    return date.toISOString().slice(0, 10);
  }
  if (/^\d{4}$/.test(text)) return `${text}-${end ? "12-31" : "01-01"}`;
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return "";
  if (quarterLike && end) date.setUTCMonth(Math.floor(date.getUTCMonth() / 3) * 3 + 3, 0);
  return date.toISOString().slice(0, 10);
}

export function targetProfileFacts(row) {
  if (!row) return [];
  const profile = row.profile_json || {};
  const type = String(row.inferred_type || row.classification || "").toLowerCase();
  const levels = Number(row.distinct_count ?? profile.unique_count ?? profile.cardinality ?? 0);
  const valueCounts = Object.entries(profile.top_values || profile.top_k || {});
  const total = Number(profile.regular_value_count ?? profile.non_null_count ?? valueCounts.reduce((sum, [, count]) => sum + Number(count || 0), 0));
  const topValues = valueCounts.slice(0, 3).map(([value, count]) => `${value} (${Number(count).toLocaleString()})`).join(", ");
  const hasZeroOneBounds = profile.min != null && profile.max != null
    && Number(profile.min) === 0 && Number(profile.max) === 1;
  const numericZeroOneCounts = hasZeroOneBounds && Number.isFinite(Number(profile.zero_count))
    && Number.isFinite(Number(profile.finite_value_count))
    ? { "0": Number(profile.zero_count), "1": Number(profile.finite_value_count) - Number(profile.zero_count) }
    : null;
  const numeric = ["numerical", "numeric", "number", "ordinal", "continuous"].includes(type);
  if (numeric && levels > 2) {
    const range = profile.min != null && profile.max != null
      ? `${formatDisplayNumber(profile.min)} → ${formatDisplayNumber(profile.max)}` : "Not retained";
    const p5 = profile.percentiles?.p05 ?? profile.percentiles?.p5;
    const p95 = profile.percentiles?.p95;
    const span = p5 != null && p95 != null
      ? `${formatDisplayNumber(p5)} → ${formatDisplayNumber(p95)}` : "Not retained";
    return [["Range", range], ["P5–P95", span], ["Mean", formatDisplayNumber(profile.mean, { fallback: "Not retained" })]];
  }
  if (levels === 2) {
    const counts = valueCounts.length ? Object.fromEntries(valueCounts) : (numericZeroOneCounts || {});
    if ("0" in counts && "1" in counts) {
      const ones = Number(counts["1"]);
      return [["0 count", Number(counts["0"]).toLocaleString()],
        ["1 count", ones.toLocaleString()], ["1 rate", total ? `${(100 * ones / total).toFixed(1)}%` : "Not retained"]];
    }
    return [["Class counts", topValues || "Not retained"], ["Classes", "2"]];
  }
  return [["Levels", Number(levels || 0).toLocaleString()], ["Class counts", topValues || "Not retained"]];
}

export function temporalProfileEvidence(row) {
  const profile = row?.profile_json || {};
  const regular = Number(profile.regular_value_count || 0);
  const bounds = profile.period_bounds;
  if (regular > 0 && profile.period_format_evidence_available === true
      && Number(profile.period_format_checked_regular_count) === regular
      && Number(profile.period_format_failure_count || 0) === 0
      && bounds?.start_date && bounds?.end_date) {
    return { kind: "period", startDate: bounds.start_date, endDate: bounds.end_date };
  }
  if (regular > 0 && profile.date_parse_failure_count != null
      && Number(profile.date_parse_failure_count) === 0
      && profile.min != null && profile.max != null) {
    return { kind: "date", startDate: periodDate(profile.min), endDate: periodDate(profile.max, true) };
  }
  const role = String(row?.role || row?.dictionary_role || "").trim().toLowerCase();
  const values = Object.entries(profile.top_values || profile.top_k || {});
  const distinct = Number(row?.distinct_count ?? profile.cardinality ?? profile.unique_count ?? 0);
  const yearMatches = values.map(([value]) => value.match(/^(\d{4})(?:\.0+)?$/));
  const completeYearSet = role === "period" && Number(row?.role_reviewed) === 1
    && regular > 0 && distinct > 0 && values.length === distinct
    && values.reduce((sum, [, count]) => sum + Number(count || 0), 0) === regular
    && yearMatches.every(Boolean);
  if (completeYearSet) {
    const years = yearMatches.map((match) => Number(match[1]));
    return {
      kind: "period",
      startDate: `${Math.min(...years)}-01-01`,
      endDate: `${Math.max(...years)}-12-31`,
    };
  }
  return null;
}
