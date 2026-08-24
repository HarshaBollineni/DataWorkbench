import { useEffect, useState } from "react";

import { getVersionDiffV2 } from "@/api/client";

function Value({ value }) {
  if (value === null || value === undefined) return <span>—</span>;
  return <span>{typeof value === "object" ? JSON.stringify(value) : String(value)}</span>;
}
function Tier1({ tier }) {
  return <section data-testid="version-diff-tier-1" className="rounded-md border border-slate-200 p-3">
    <h4 className="font-semibold text-slate-950">1. Schema</h4>
    {(tier.tables_added || []).map((table) => <p key={`add-${table}`} className="text-sm">Table added: {table}</p>)}
    {(tier.tables_removed || []).map((table) => <p key={`remove-${table}`} className="text-sm">Table removed: {table}</p>)}
    {Object.entries(tier.per_table || {}).map(([table, diff]) => <div key={table} className="mt-2 text-sm">
      <p className="font-medium">{table}</p>
      {(diff.added || []).map((column) => <p key={`a-${column}`}>Column added: {column}</p>)}
      {(diff.removed || []).map((column) => <p key={`r-${column}`}>Column removed: {column}</p>)}
      {(diff.likely_renamed || []).map((change) => <p key={`n-${change.from}-${change.to}`}>Likely renamed: {change.from} → {change.to} (confidence {change.confidence}; evidence: name similarity and type agreement)</p>)}
      {(diff.reordered || []).map((change) => <p key={`o-${change.column}`}>Reordered: {change.column} ({change.from_index} → {change.to_index})</p>)}
      {(diff.type_changes || []).map((change) => <p key={`t-${change.column}`}>{change.column}: {change.from} → {change.to}</p>)}
    </div>)}
    {tier.is_match && <p className="text-sm text-slate-500">No schema differences.</p>}
  </section>;
}

function Tier2({ tier }) {
  return <section data-testid="version-diff-tier-2" className="rounded-md border border-slate-200 p-3">
    <h4 className="font-semibold text-slate-950">2. Shape</h4>
    {[["Rows", tier.row_count], ["Columns", tier.column_count], ["File size", tier.file_size], ["Snapshots", tier.snapshot_count]].map(([label, value]) => <p key={label} className="text-sm">{label}: <Value value={value?.a} /> → <Value value={value?.b} /> (delta <Value value={value?.delta} />)</p>)}
    <p className="text-sm">Coverage: <Value value={tier.period_coverage?.a} /> → <Value value={tier.period_coverage?.b} /></p>
  </section>;
}

function Tier3({ tier }) {
  return <section data-testid="version-diff-tier-3" className="rounded-md border border-slate-200 p-3">
    <h4 className="font-semibold text-slate-950">3. Distribution</h4>
    {tier.comparison_basis && <p className="text-xs text-slate-600">{tier.comparison_basis}</p>}
    {!tier.available && <p className="mt-2 text-sm text-amber-800">{tier.unavailable_reason}</p>}
    {tier.available && Object.entries(tier.per_column || {}).map(([column, diff]) => <div key={column} className="mt-2 text-sm">
      <p className="font-medium">{column}</p>
      {diff.distribution_note && <p className="text-amber-800">{diff.distribution_note}</p>}
      <p>Null rate: <Value value={diff.null_rate?.a} /> → <Value value={diff.null_rate?.b} /> · distinct: <Value value={diff.distinct_count?.a} /> → <Value value={diff.distinct_count?.b} /></p>
      <p>Min/max: <Value value={diff.min?.a} /> / <Value value={diff.max?.a} /> → <Value value={diff.min?.b} /> / <Value value={diff.max?.b} /></p>
      {diff.mean && <p>Mean/stddev: <Value value={diff.mean?.a} /> / <Value value={diff.stddev?.a} /> → <Value value={diff.mean?.b} /> / <Value value={diff.stddev?.b} /></p>}
      <p>Top categories: <Value value={diff.top_k} /> · histogram shift (bin-wise absolute difference): <Value value={diff.histogram_shift} /></p>
    </div>)}
  </section>;
}

export default function VersionDiff({ assetId, versionA, versionB }) {
  const requestKey = `${assetId ?? ""}:${versionA ?? ""}:${versionB ?? ""}`;
  const [result, setResult] = useState({ key: "", diff: null, error: "" });
  useEffect(() => {
    if (!assetId || versionA === undefined || versionB === undefined) return undefined;
    let active = true;
    getVersionDiffV2(assetId, versionA, versionB)
      .then((diff) => {
        if (active) setResult({ key: requestKey, diff, error: "" });
      })
      .catch((err) => {
        if (active) setResult({ key: requestKey, diff: null, error: err.message });
      });
    return () => { active = false; };
  }, [assetId, requestKey, versionA, versionB]);
  if (result.key === requestKey && result.error) return <p className="text-sm text-red-700">{result.error}</p>;
  if (result.key !== requestKey || !result.diff) return <p className="text-sm text-slate-500">Loading version differences…</p>;
  const { diff } = result;
  return <div data-testid="version-diff" className="mt-3 space-y-3">
    <Tier1 tier={diff.tier1_schema} />
    <Tier2 tier={diff.tier2_shape} />
    <Tier3 tier={diff.tier3_distribution} />
    <p className="text-xs text-slate-500">Row-level comparison is unavailable in this release; no row-level action is offered by default.</p>
  </div>;
}
