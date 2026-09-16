import { Fragment } from "react";

import { ARTIFACT_PAGE_SIZE } from "./constants";
import { artifactDisplayName, partitionArtifactPayload } from "./artifactPresentation";

function label(key) {
  return String(key).replaceAll("_", " ");
}

function formatValue(value) {
  if (typeof value !== "number") return String(value ?? "—");
  return Number.isInteger(value)
    ? value.toLocaleString()
    : value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function scalarEntries(payload) {
  return Object.entries(payload || {}).filter(([, value]) => (
    ["string", "number", "boolean"].includes(typeof value) || value == null
  ));
}

function Pager({ offset, total, count, loading, onPageChange }) {
  if (!total && !loading) return null;
  const start = total ? offset + 1 : 0;
  const end = Math.min(offset + count, total);
  return (
    <div className="flex items-center justify-between gap-3 border-t p-3 text-xs text-slate-500">
      <span>{loading ? "Loading…" : `Showing ${start}–${end} of ${total}`}</span>
      <div className="flex gap-2">
        <button type="button" disabled={loading || offset === 0}
          onClick={() => onPageChange(Math.max(0, offset - ARTIFACT_PAGE_SIZE))}
          className="rounded border px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40">Previous</button>
        <button type="button" disabled={loading || offset + ARTIFACT_PAGE_SIZE >= total}
          onClick={() => onPageChange(offset + ARTIFACT_PAGE_SIZE)}
          className="rounded border px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40">Next</button>
      </div>
    </div>
  );
}

function ArrayTable({ title, rows }) {
  if (!rows?.length || !rows.every((row) => row && typeof row === "object" && !Array.isArray(row))) return null;
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].slice(0, 8);
  return (
    <section className="mt-5">
      <h3 className="text-sm font-semibold text-slate-800">{label(title)}</h3>
      <div className="mt-2 overflow-x-auto rounded border">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-50 text-slate-500"><tr>{columns.map((column) => <th className="p-2" key={column}>{label(column)}</th>)}</tr></thead>
          <tbody>{rows.slice(0, 50).map((row, index) => <tr className="border-t" key={index}>{columns.map((column) => <td className="p-2" key={column}>{typeof row[column] === "object" ? JSON.stringify(row[column]) : formatValue(row[column])}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {rows.length > 50 && <p className="mt-1 text-xs text-slate-500">Showing the first 50 rows.</p>}
    </section>
  );
}

function ArtifactDetail({ artifact, payload, artifactTypes, onClose }) {
  if (!artifact) return null;
  if (!payload) return <section className="rounded-lg border bg-white p-5 text-sm text-slate-500">Verifying and loading immutable artifact details…</section>;
  const descriptor = artifactTypes[artifact.artifact_type];
  const { user, technical } = partitionArtifactPayload(payload);
  const arrays = Object.entries(user).filter(([, value]) => Array.isArray(value));
  const objects = Object.entries(user).filter(([, value]) => value && typeof value === "object" && !Array.isArray(value));
  const summaryMetrics = artifact.summary?.metrics || [];
  const summaryFields = Object.entries(artifact.summary?.fields || {}).filter(([, value]) => !Array.isArray(value));
  return (
    <section className="rounded-lg border bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <div><h2 className="font-semibold text-slate-900">{artifactDisplayName(artifact.artifact_type, artifactTypes)}</h2><p className="text-sm text-slate-500">{descriptor?.description || "Retained analytical evidence."}</p></div>
        <button type="button" className="text-sm text-dq-purple hover:underline" onClick={onClose}>Hide details</button>
      </div>
      {(summaryMetrics.length > 0 || summaryFields.length > 0) && <section className="mt-4"><h3 className="text-sm font-semibold text-slate-800">Summary</h3><div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{summaryMetrics.map((metric) => <div className="rounded border bg-teal-50 p-3" key={metric.metric_key || metric.label}><div className="text-xs text-slate-500">{metric.label || label(metric.metric_key)}</div><div className="mt-1 text-sm font-semibold text-slate-900">{formatValue(metric.value)}{metric.unit === "percent" ? "%" : ""}</div></div>)}{summaryFields.map(([key, value]) => <div className="rounded border bg-slate-50 p-3" key={key}><div className="text-xs text-slate-500">{label(key)}</div><div className="mt-1 break-words text-sm font-semibold text-slate-900">{formatValue(value)}</div></div>)}</div></section>}
      <h3 className="mt-5 text-sm font-semibold text-slate-800">Evidence details</h3>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {scalarEntries(user).map(([key, value]) => <div className="rounded border bg-slate-50 p-3" key={key}><div className="text-xs uppercase text-slate-500">{label(key)}</div><div className="mt-1 break-words text-sm font-semibold text-slate-900">{formatValue(value)}</div></div>)}
      </div>
      {objects.filter(([key]) => !["top_k", "truncation"].includes(key)).map(([key, value]) => <div className="mt-5" key={key}><h3 className="text-sm font-semibold text-slate-800">{label(key)}</h3><dl className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{scalarEntries(value).map(([childKey, childValue]) => <div className="rounded border p-2" key={childKey}><dt className="text-xs text-slate-500">{label(childKey)}</dt><dd className="text-sm font-medium">{formatValue(childValue)}</dd></div>)}</dl></div>)}
      {payload.top_k && <ArrayTable title="Top values" rows={Object.entries(payload.top_k).map(([value, count]) => ({ value, count }))} />}
      {arrays.filter(([key]) => key !== "top_k").map(([key, value]) => <ArrayTable key={key} title={key} rows={value} />)}
      <details className="mt-5 rounded border border-slate-200 bg-slate-50 p-3">
        <summary className="cursor-pointer text-sm font-semibold text-slate-700">Technical audit details</summary>
        <p className="mt-2 text-xs text-slate-500">Immutable identifiers and system metadata used for traceability and verification.</p>
        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2"><div><dt className="text-slate-500">Artifact ID</dt><dd className="break-all font-mono">{artifact.artifact_id}</dd></div><div><dt className="text-slate-500">Snapshot ID</dt><dd className="break-all font-mono">{artifact.snapshot_id}</dd></div><div><dt className="text-slate-500">Scope</dt><dd>{artifact.scope}</dd></div><div><dt className="text-slate-500">Status</dt><dd>{artifact.status}</dd></div></dl>
        {Object.keys(technical).length > 0 && <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-900 p-3 text-xs text-slate-100">{JSON.stringify(technical, null, 2)}</pre>}
      </details>
    </section>
  );
}

export function RepositoryOverview({ overview }) {
  if (!overview) return <div className="rounded-lg border bg-white p-5 text-sm text-slate-500">Loading overview…</div>;
  return <><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{Object.entries(overview).filter(([, value]) => typeof value === "number").map(([key, value]) => <div key={key} className="rounded-lg border bg-white p-4"><div className="text-xl font-semibold">{value}</div><div className="text-xs text-slate-500">{label(key)}</div></div>)}</div><p className="mt-3 text-xs text-slate-500">Repository totals include retained technical audit records. Evidence &amp; Results hides those records by default.</p>{overview.backfill_status === "pending" && <p className="mt-3 text-sm text-slate-500">Preparing retained Data Sourcing evidence…</p>}</>;
}

export function SavedSchema({ rows, loading, page, onPageChange }) {
  return <section className="rounded-lg border bg-white"><div className="border-b p-4"><h2 className="font-semibold text-slate-900">Final saved data schema</h2><p className="mt-1 text-sm text-slate-500">Column definitions retained with the active snapshot’s immutable column-profile evidence.</p><p className="mt-2 text-xs text-slate-500"><strong>Data type</strong> is the physical storage type; <strong>Detected type</strong> is the inferred logical type. <strong>Missing rate</strong> is the share of rows with null values.</p></div><div className="max-h-[min(60vh,42rem)] overflow-auto overscroll-contain"><table className="w-full min-w-[1050px] text-left text-sm"><thead className="sticky top-0 z-10 border-b bg-slate-50 text-xs text-slate-500 shadow-[0_1px_0_0_rgb(226_232_240)]"><tr><th className="p-3">Column name</th><th>Data type</th><th>Role</th><th>Detected type</th><th>Description</th><th>Distinct values</th><th>Missing rate</th><th>Special values</th></tr></thead><tbody>{rows.map((row) => { const fields = row.summary?.fields || {}; const metrics = Object.fromEntries((row.summary?.metrics || []).map((metric) => [metric.metric_key, metric.value])); const specialValues = fields.special_values || []; const specialCount = fields.special_value_count ?? specialValues.length; return <tr className="border-b align-top" key={row.artifact_id}><td className="p-3 font-medium">{row.feature || "—"}</td><td>{fields.data_type || "—"}</td><td>{fields.role || "—"}</td><td>{fields.classification || "—"}</td><td className="max-w-sm whitespace-normal">{fields.description || "—"}</td><td>{metrics.distinct_count == null ? "—" : formatValue(metrics.distinct_count)}</td><td>{metrics.null_share == null ? "—" : `${formatValue(metrics.null_share)}${Number(metrics.null_share) <= 1 ? "" : "%"}`}</td><td>{specialCount ? <span>{specialCount} · {specialValues.join(", ")}</span> : "—"}</td></tr>; })}{!rows.length && <tr><td colSpan="8" className="p-6 text-center text-slate-500">{loading ? "Loading saved schema…" : "No saved column schema is available for this snapshot."}</td></tr>}</tbody></table></div><Pager {...page} count={rows.length} loading={loading} onPageChange={onPageChange} /></section>;
}

export function ArtifactList({ rows, loading, featureQuery, onFeatureQueryChange, showTechnicalAudit, onShowTechnicalAuditChange, artifactTypes, onOpen, onDetails, onDownload, detail, detailPayload, onCloseDetails, page, onPageChange }) {
  return <div className="rounded-lg border bg-white">
    <div className="border-b p-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div><h2 className="font-semibold text-slate-900">Evidence and results</h2><p className="mt-1 max-w-3xl text-sm text-slate-500">Profiles, metrics, diagnostic findings, reports, and RCA evidence retained for this snapshot.</p></div>
        <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={showTechnicalAudit} onChange={(event) => onShowTechnicalAuditChange(event.target.checked)} />Show technical audit records</label>
      </div>
      {showTechnicalAudit && <p className="mt-2 text-xs text-amber-700">Technical audit records include system structure assertions, governance references, and immutable identifiers.</p>}
      <label className="mt-4 block text-xs font-medium text-slate-600" htmlFor="artifact-feature-search">Search by feature</label>
      <input id="artifact-feature-search" value={featureQuery} onChange={(event) => onFeatureQueryChange(event.target.value)} placeholder="e.g. bureau_score" className="mt-1 h-9 w-full max-w-sm rounded-md border border-slate-200 px-3 text-sm" />
    </div>
    <div className="max-h-[min(60vh,42rem)] overflow-auto overscroll-contain"><table className="w-full min-w-[1050px] text-left text-sm"><thead className="sticky top-0 z-10 border-b bg-slate-50 text-xs text-slate-500 shadow-[0_1px_0_0_rgb(226_232_240)]"><tr><th className="p-3">Evidence type</th><th>Feature</th><th>Scope</th><th>Primary metric</th><th>Snapshot</th><th>Status</th><th /></tr></thead><tbody>{rows.map((row) => { const metric = row.summary?.metrics?.[0]; const expanded = detail?.artifact_id === row.artifact_id; const binary = row.payload_media_type && row.payload_media_type !== "application/json"; return <Fragment key={row.artifact_id}><tr className="border-b"><td className="p-3 font-medium"><span>{artifactDisplayName(row.artifact_type, artifactTypes)}</span>{artifactTypes[row.artifact_type]?.user_facing === false && <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-slate-500">Technical</span>}</td><td>{row.feature || row.identity?.table || "—"}</td><td>{label(row.scope)}</td><td>{metric ? `${metric.label || label(metric.metric_key)}: ${formatValue(metric.value)}` : "—"}</td><td>{row.snapshot_id}</td><td>{row.status}</td><td className="whitespace-nowrap"><button type="button" className="mr-3 text-dq-purple hover:underline" onClick={() => expanded ? onCloseDetails() : onDetails(row)}>{expanded ? "Hide details" : "Details"}</button>{binary && <button type="button" className="mr-3 text-dq-purple hover:underline" onClick={() => onDownload(row)}>Download</button>}<button type="button" className="text-dq-purple hover:underline" onClick={() => onOpen(row)}>Lineage</button></td></tr>{expanded && <tr className="border-b bg-slate-50"><td colSpan="7" className="p-4"><ArtifactDetail artifact={detail} payload={detailPayload} artifactTypes={artifactTypes} onClose={onCloseDetails} /></td></tr>}</Fragment>; })}{!rows.length && <tr><td colSpan="7" className="p-6 text-center text-slate-500">{loading ? "Loading evidence…" : "No matching evidence or results are available."}</td></tr>}</tbody></table></div>
    <Pager {...page} count={rows.length} loading={loading} onPageChange={onPageChange} />
  </div>;
}

export function RetainedRuns({ rows }) {
  return <div className="rounded-lg border bg-white p-5"><h2 className="font-semibold">Retained analytical runs</h2>{rows.length ? <ul className="mt-3 space-y-3">{rows.map((entry) => <li key={entry.run.run_id} className="rounded border p-3 text-sm"><div className="font-medium">{entry.run.capability_id} · {entry.run.status}</div><div className="text-slate-500">{entry.run.run_id} · {entry.run.finished_at || entry.run.created_at}</div></li>)}</ul> : <p className="mt-2 text-sm text-slate-500">No retained analytical runs for this snapshot.</p>}</div>;
}

export function ArtifactLineage({ selected, lineage, artifactTypes, onOpen }) {
  if (!selected) return <div className="rounded-lg border bg-white p-5 text-sm text-slate-500">Choose a record from Evidence &amp; Results to inspect its bounded lineage and impact.</div>;
  if (!lineage) return <div className="rounded-lg border bg-white p-5 text-sm text-slate-500">Loading lineage…</div>;
  return <div className="grid gap-4"><div className="rounded-lg border bg-white p-4"><p className="font-semibold">{artifactDisplayName(selected.artifact_type, artifactTypes)}</p><p className="text-sm text-slate-500">{label(selected.scope)} · {selected.status}</p><p className="mt-1 break-all font-mono text-xs text-slate-400">{selected.artifact_id}</p></div>{[["Source evidence", lineage.ancestors], ["Dependent evidence", lineage.dependants]].map(([title, values]) => <section key={title} className="rounded-lg border bg-white p-4"><h2 className="font-semibold">{title}</h2>{values?.length ? <ul className="mt-2 space-y-2">{values.map((node) => <li key={node.artifact_id}><button type="button" onClick={() => onOpen(node)} className="text-left text-sm text-dq-purple hover:underline"><span className="font-medium">{artifactDisplayName(node.artifact_type, artifactTypes)}</span> · {node.feature || "snapshot"}<span className="block break-all font-mono text-xs text-slate-400">{node.artifact_id}</span></button></li>)}</ul> : <p className="mt-2 text-sm text-slate-500">None.</p>}</section>)}</div>;
}
