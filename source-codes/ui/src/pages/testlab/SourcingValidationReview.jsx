import { AlertTriangle, CheckCircle2, ChevronDown } from "lucide-react";

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

function RuleTable({ rules }) {
  if (!rules.length) return <p className="border-t border-slate-200 bg-white p-4 text-sm text-slate-500">No rules in this group.</p>;
  return <div className="max-h-72 overflow-auto border-t border-slate-200"><table className="w-full min-w-[760px] text-sm"><thead className="sticky top-0 bg-slate-100 text-left text-xs uppercase text-slate-500"><tr><th className="px-3 py-2">Rule</th><th className="px-3 py-2">Column</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Definition</th></tr></thead><tbody>{rules.map((rule) => <tr key={rule.id} className="border-t border-slate-100 bg-white"><td className="px-3 py-2 font-medium">{rule.rule}</td><td className="px-3 py-2">{rule.column}</td><td className="px-3 py-2 text-slate-600">dictionary + profile</td><td className="px-3 py-2 capitalize">{rule.status}</td><td className="px-3 py-2 text-slate-600">{rule.definition}</td></tr>)}</tbody></table></div>;
}

function RuleGroup({ label, description, rules, tone, guidance }) {
  return <section className={`overflow-hidden rounded-lg border ${tone}`}><div className="flex items-center justify-between gap-3 p-4"><span><strong className="block text-slate-900">{label}</strong><small className="text-slate-500">{description}</small></span><b className="rounded-full bg-white px-3 py-1 text-sm">{rules.length}</b></div>{guidance && !!rules.length && <div className="border-t border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><strong>What needs review?</strong><p className="mt-1">Confirm that each listed sentinel represents missing or exceptional data, and review inferred or constant-column decisions before promotion.</p></div>}<RuleTable rules={rules} /></section>;
}

export default function SourcingValidationReview({ ingest, inventory = [], summaries = [] }) {
  const warnings = ingest?.warnings || [];
  const schemaMessages = ingest?.schema_check?.is_match === false ? (ingest.schema_check.messages || []) : [];
  const discrepancies = inventory.flatMap((row) => (row.discrepancies || []).map((message) => `${row.column_name}: ${message}`));
  const provisional = inventory.filter((row) => row.provisional).map((row) => row.column_name);
  const missingReviews = inventory.filter((row) => (row.missing_value_codes || []).length && !row.missing_codes_confirmed).map((row) => row.column_name);
  const constants = inventory.filter((row) => Number(row.distinct_count || 0) === 1).map((row) => `${row.column_name}: one observed level; Ignore recommended unless it is a protected role`);
  const checks = [
    ["Schema reconciliation", schemaMessages],
    ["Profiling warnings", warnings.map((warning) => `${warning.column || "Dataset"}: ${warning.message}`)],
    ["Column discrepancies", discrepancies],
    ["Provisional definitions", provisional],
    ["Special / missing value confirmation", missingReviews],
    ["Constant-column review", constants],
  ];
  const findings = checks.filter(([, values]) => values.length);
  const clear = checks.filter(([, values]) => !values.length);
  const findingCount = findings.reduce((total, [, values]) => total + values.length, 0);
  const warningColumns = new Set(warnings.map((warning) => warning.column).filter(Boolean));
  const rules = inventory.flatMap((row) => {
    const status = warningColumns.has(row.column_name) ? "warning" : row.provisional || Number(row.distinct_count || 0) === 1 || ((row.missing_value_codes || []).length && !row.missing_codes_confirmed) ? "review" : "passed";
    return [
      { id: `${row.table_name}.${row.column_name}.type`, column: row.column_name, rule: "Observed type", status, definition: row.inferred_type || row.classification || "unknown" },
      { id: `${row.table_name}.${row.column_name}.population`, column: row.column_name, rule: "Population profile", status: "passed", definition: `${Number(row.null_count || 0).toLocaleString()} null · ${Number(row.distinct_count || 0).toLocaleString()} distinct` },
    ];
  });
  const byStatus = (status) => rules.filter((rule) => rule.status === status);
  const rowCount = summaries.reduce((total, row) => total + Number(row.rows || 0), 0);

  return <div>
    <div className="flex justify-end">{findingCount ? <span className="inline-flex items-center gap-2 rounded-full bg-amber-100 px-3 py-2 text-sm font-semibold text-amber-800"><AlertTriangle className="h-4 w-4" />{findingCount} findings</span> : <span className="inline-flex items-center gap-2 rounded-full bg-emerald-100 px-3 py-2 text-sm font-semibold text-emerald-800"><CheckCircle2 className="h-4 w-4" />All checks clear</span>}</div>

    <details className={`group mt-4 overflow-hidden rounded-lg border ${findingCount ? "border-amber-300 bg-amber-50/30" : "border-emerald-200 bg-emerald-50/30"}`}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-4"><span><strong className="block text-slate-900">Reconciliation findings</strong><small className="text-slate-500">{findingCount ? `${findingCount} findings across ${findings.length} checks` : "No dictionary or schema discrepancies detected"}</small></span><span className="flex items-center gap-2 text-xs"><span className="rounded-full border bg-white px-3 py-1"><b>{findingCount}</b> findings</span><span className="rounded-full border bg-white px-3 py-1"><b>{clear.length}</b> clear checks</span><ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" /></span></summary>
      <div className="grid gap-3 border-t p-4 md:grid-cols-2">{checks.map(([label, values]) => <section key={label} className="rounded-md border border-slate-200 bg-white p-3"><div className="flex items-center justify-between"><strong className="text-sm text-slate-800">{label}</strong><span className={`text-xs font-semibold ${values.length ? "text-amber-700" : "text-emerald-700"}`}>{values.length || "Clear"}</span></div>{values.length ? <ul className="mt-2 list-disc pl-4 text-xs text-slate-600">{values.map((value) => <li key={value}>{value}</li>)}</ul> : <p className="mt-1 text-xs text-slate-500">No findings</p>}</section>)}</div>
    </details>

    <details className="group mt-4 overflow-hidden rounded-lg border border-emerald-200 bg-emerald-50/20">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-4"><span><strong className="block text-slate-900">Generated validation rules</strong><small className="text-slate-500">{rules.length} deterministic checks across {inventory.length} columns</small></span><span className="flex items-center gap-2 text-xs"><span className="rounded-full border bg-white px-3 py-1"><b>{byStatus("warning").length}</b> warnings</span><span className="rounded-full border bg-white px-3 py-1"><b>{byStatus("review").length}</b> review</span><span className="rounded-full border bg-white px-3 py-1"><b>{byStatus("passed").length}</b> passed</span><ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" /></span></summary>
      <div className="space-y-4 border-t border-emerald-200 p-4">
        <RuleGroup label="Warnings" description="Checks with observed discrepancies that need attention." rules={byStatus("warning")} tone="border-red-200 bg-red-50/30" />
        <RuleGroup label="Review" description="Preset decisions that require explicit review or confirmation." rules={byStatus("review")} tone="border-amber-200 bg-amber-50/30" guidance />
        <RuleGroup label="Passed" description="Checks completed without an observed discrepancy." rules={byStatus("passed")} tone="border-emerald-200 bg-emerald-50/30" />
      </div>
    </details>

    <details className="group mt-4 overflow-hidden rounded-lg border border-slate-200">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-4"><span><strong className="block text-slate-900">Observed dataset profile</strong><small className="text-slate-500">Expand to review observed types, levels, summaries, and samples</small></span><span className="flex items-center gap-3"><b className="text-sm text-slate-700">{rowCount.toLocaleString()} rows · {inventory.length.toLocaleString()} columns</b><ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" /></span></summary>
      <div className="max-h-96 overflow-auto border-t"><table className="w-full min-w-[700px] text-sm"><thead className="sticky top-0 bg-slate-100 text-left text-xs uppercase text-slate-500"><tr><th className="px-3 py-2">Column</th><th className="px-3 py-2">Observed type</th><th className="px-3 py-2">Levels</th><th className="px-3 py-2">Summary / unique sample</th></tr></thead><tbody>{inventory.map((row) => <tr key={`${row.table_name}.${row.column_name}`} className="border-t border-slate-100"><td className="px-3 py-2 font-medium">{row.column_name}</td><td className="px-3 py-2">{row.inferred_type || row.classification}</td><td className="px-3 py-2">{Number(row.distinct_count || 0).toLocaleString()}</td><td className="max-w-lg px-3 py-2 text-xs text-slate-600">{profileEvidence(row)}</td></tr>)}</tbody></table></div>
    </details>
  </div>;
}
