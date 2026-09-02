import { AlertTriangle, CheckCircle2, ChevronDown } from "lucide-react";

const INFORMATIONAL_WARNING_CODES = new Set(["type_conflict"]);
const BOOLEAN_VALUES = new Set(["true", "false", "0", "1", "yes", "no", "y", "n", "t", "f"]);

const formatValue = (value) => {
  if (value == null) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
  return String(value);
};

const profileEvidence = (row) => {
  const profile = row.profile_json || {};
  const topValues = profile.top_values || profile.top_k;
  if (topValues && Object.keys(topValues).length) return Object.entries(topValues).slice(0, 5).map(([value, count]) => `${value} (${Number(count).toLocaleString()})`).join(", ");
  if (profile.min != null || profile.max != null) return `${formatValue(profile.min)} to ${formatValue(profile.max)}`;
  return (row.sample_values || profile.sample_values || []).slice(0, 5).map(formatValue).join(", ") || "—";
};

const isFalseBinaryParseWarning = (warning, inventory) => {
  if (warning.code !== "column_parse_failure_rate") return false;
  const row = inventory.find((candidate) => candidate.column_name === warning.column);
  if (row?.inferred_type !== "binary") return false;
  const values = row?.profile_json?.top_values || row?.profile_json?.top_k || {};
  const observed = Object.keys(values).map((value) => value.trim().toLowerCase());
  return observed.length > 0 && observed.every((value) => !BOOLEAN_VALUES.has(value));
};

export default function SourcingValidationReview({ ingest, inventory = [], summaries = [] }) {
  const warnings = ingest?.warnings || [];
  const schemaMessages = ingest?.schema_check?.is_match === false ? (ingest.schema_check.messages || []) : [];
  const actionableWarnings = warnings.filter((warning) => (
    !INFORMATIONAL_WARNING_CODES.has(warning.code) && !isFalseBinaryParseWarning(warning, inventory)
  ));
  const autoDetected = inventory.filter((row) => row.provisional).map((row) => row.column_name);
  const missingReviews = inventory.filter((row) => (row.missing_value_codes || []).length && !row.missing_codes_confirmed).map((row) => row.column_name);
  const constants = inventory.filter((row) => Number(row.distinct_count || 0) === 1).map((row) => `${row.column_name}: one observed level; Ignore recommended unless it is a protected role`);
  const checks = [
    ["Schema reconciliation", schemaMessages],
    ["Data quality warnings", actionableWarnings.map((warning) => `${warning.column || "Dataset"}: ${warning.message}`)],
    ["Special / missing value confirmation", missingReviews],
    ["Constant-column review", constants],
  ];
  const findings = checks.filter(([, values]) => values.length);
  const clear = checks.filter(([, values]) => !values.length);
  const findingCount = findings.reduce((total, [, values]) => total + values.length, 0);
  const rowCount = summaries.reduce((total, row) => total + Number(row.rows || 0), 0);

  return <div>
    <div className="flex justify-end">{findingCount ? <span className="inline-flex items-center gap-2 rounded-full bg-amber-100 px-3 py-2 text-sm font-semibold text-amber-800"><AlertTriangle className="h-4 w-4" />{findingCount} review {findingCount === 1 ? "item" : "items"}</span> : <span className="inline-flex items-center gap-2 rounded-full bg-emerald-100 px-3 py-2 text-sm font-semibold text-emerald-800"><CheckCircle2 className="h-4 w-4" />No review items</span>}</div>

    <details className={`group mt-4 overflow-hidden rounded-lg border ${findingCount ? "border-amber-300 bg-amber-50/30" : "border-emerald-200 bg-emerald-50/30"}`}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-4"><span><strong className="block text-slate-900">Items requiring review</strong><small className="text-slate-500">{findingCount ? `${findingCount} items across ${findings.length} checks` : "No actionable data-quality or schema issues detected"}</small></span><span className="flex items-center gap-2 text-xs"><span className="rounded-full border bg-white px-3 py-1"><b>{findingCount}</b> review</span><span className="rounded-full border bg-white px-3 py-1"><b>{clear.length}</b> clear</span><ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" /></span></summary>
      <div className="grid gap-3 border-t p-4 md:grid-cols-2">{checks.map(([label, values]) => <section key={label} className="rounded-md border border-slate-200 bg-white p-3"><div className="flex items-center justify-between"><strong className="text-sm text-slate-800">{label}</strong><span className={`text-xs font-semibold ${values.length ? "text-amber-700" : "text-emerald-700"}`}>{values.length || "Clear"}</span></div>{values.length ? <ul className="mt-2 list-disc pl-4 text-xs text-slate-600">{values.map((value) => <li key={value}>{value}</li>)}</ul> : <p className="mt-1 text-xs text-slate-500">No review needed</p>}</section>)}</div>
    </details>

    {autoDetected.length > 0 && <details className="group mt-4 overflow-hidden rounded-lg border border-slate-200 bg-slate-50/30">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-4"><span><strong className="block text-slate-900">Auto-detected definitions</strong><small className="text-slate-500">Informational only; confirm final formats and roles in Step 3</small></span><span className="flex items-center gap-3 text-xs text-slate-600"><b>{autoDetected.length}</b> columns<ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" /></span></summary>
      <div className="border-t border-slate-200 bg-white p-4 text-xs text-slate-600">{autoDetected.join(", ")}</div>
    </details>}

    <details className="group mt-4 overflow-hidden rounded-lg border border-slate-200">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-4"><span><strong className="block text-slate-900">Observed dataset profile</strong><small className="text-slate-500">Expand to review detected patterns, levels, summaries, and samples</small></span><span className="flex items-center gap-3"><b className="text-sm text-slate-700">{rowCount.toLocaleString()} rows · {inventory.length.toLocaleString()} columns</b><ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" /></span></summary>
      <div className="max-h-96 overflow-auto border-t"><table className="w-full min-w-[700px] text-sm"><thead className="sticky top-0 bg-slate-100 text-left text-xs uppercase text-slate-500"><tr><th className="px-3 py-2">Column</th><th className="px-3 py-2">Detected pattern</th><th className="px-3 py-2">Levels</th><th className="px-3 py-2">Summary / unique sample</th></tr></thead><tbody>{inventory.map((row) => <tr key={`${row.table_name}.${row.column_name}`} className="border-t border-slate-100"><td className="px-3 py-2 font-medium">{row.column_name}</td><td className="px-3 py-2">{row.inferred_type || row.classification}</td><td className="px-3 py-2">{Number(row.distinct_count || 0).toLocaleString()}</td><td className="max-w-lg px-3 py-2 text-xs text-slate-600">{profileEvidence(row)}</td></tr>)}</tbody></table></div>
    </details>
  </div>;
}
