import { useMemo, useState } from "react";
import { ChevronRight, Download, FileText, Info } from "lucide-react";

import { diagnosticReportUrlV2, downloadDiagnosticReportV2 } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FindingActions, FindingStateBadge, IssueLifecycleActions, OverrideIssueAction } from "@/pages/testlab/FindingWorkflow";
import { matchesFindingFilter } from "@/pages/testlab/findingWorkflowState";
import { OBSERVED_COLUMNS, fmt, groupObserved } from "./directionalityWorkflow";

const WIDTH = 900;
const HEIGHT = 380;
const PAD = { left: 70, right: 30, top: 58, bottom: 54 };

function extent(values, fallback = [0, 1]) {
  const finite = values.map(Number).filter(Number.isFinite);
  if (!finite.length) return fallback;
  const min = Math.min(...finite), max = Math.max(...finite);
  if (min === max) return [min - 0.5, max + 0.5];
  const margin = (max - min) * 0.05;
  return [min - margin, max + margin];
}

function focusedYExtent(values, binary) {
  const finite = values.map(Number).filter(Number.isFinite);
  if (!finite.length) return binary ? [0, 1] : [0, 1];
  let min = Math.min(...finite), max = Math.max(...finite);
  const scale = Math.max(Math.abs(min), Math.abs(max));
  const minimumSpan = binary ? 0.02 : (scale ? scale * 0.04 : 1);
  if (max - min < minimumSpan) {
    const midpoint = (min + max) / 2;
    min = midpoint - minimumSpan / 2;
    max = midpoint + minimumSpan / 2;
  }
  const margin = (max - min) * 0.14;
  min -= margin;
  max += margin;
  return binary ? [Math.max(0, min), Math.min(1, max)] : [min, max];
}

function chartTicks(min, max, count = 5) {
  return Array.from({ length: count }, (_, index) => min + ((max - min) * index) / (count - 1));
}

function readableEnum(value) {
  return String(value || "-").replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}

function EvidenceChart({ evidence, feature, reference, expectedDirection }) {
  const bins = evidence.binned?.bins || [];
  const sample = evidence.chart_sample || [];
  const curve = evidence.regression?.curve || [];
  const binary = evidence.event_value != null;
  const binXValues = bins.map((row) => Number(row.feature_mean)).filter(Number.isFinite);
  const binXMin = binXValues.length ? Math.min(...binXValues) : null;
  const binXMax = binXValues.length ? Math.max(...binXValues) : null;
  const visibleCurve = binXValues.length > 1
    ? curve.filter((row) => Number(row.x) >= binXMin && Number(row.x) <= binXMax)
    : curve;
  const xValues = binXValues.length > 1 ? binXValues
    : [...curve.map((row) => row.x), ...sample.map((row) => row.x)];
  const primaryYValues = [...bins.map((row) => row.reference_mean), ...visibleCurve.map((row) => row.y)];
  const yValues = primaryYValues.length ? primaryYValues : sample.map((row) => row.y);
  const [xMin, xMax] = extent(xValues);
  const [yMin, yMax] = focusedYExtent(yValues, binary);
  const x = (value) => PAD.left + ((value - xMin) / (xMax - xMin || 1)) * (WIDTH - PAD.left - PAD.right);
  const y = (value) => HEIGHT - PAD.bottom - ((value - yMin) / (yMax - yMin || 1)) * (HEIGHT - PAD.top - PAD.bottom);
  const line = (values) => values.map((row, index) => `${index ? "L" : "M"}${x(row.x ?? row.feature_mean)},${y(row.y ?? row.reference_mean)}`).join(" ");
  const expectedLine = expectedDirection === "INCREASING"
    ? { x1: xMin, y1: yMin + (yMax - yMin) * 0.15, x2: xMax, y2: yMax - (yMax - yMin) * 0.15 }
    : expectedDirection === "DECREASING"
      ? { x1: xMin, y1: yMax - (yMax - yMin) * 0.15, x2: xMax, y2: yMin + (yMax - yMin) * 0.15 }
      : null;
  const yTicks = chartTicks(yMin, yMax);
  const yTickDigits = yMax - yMin < 0.01 ? 4 : yMax - yMin < 0.1 ? 3 : 2;
  return <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-2 shadow-sm">
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="block h-auto w-full min-w-[760px]" role="img" aria-label={`${feature} directionality evidence against ${reference}`}>
      <defs>
        <clipPath id="directionality-plot-area"><rect x={PAD.left} y={PAD.top} width={WIDTH - PAD.left - PAD.right} height={HEIGHT - PAD.top - PAD.bottom} /></clipPath>
        <marker id="expected-direction-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="#16a34a" /></marker>
      </defs>
      <rect x={PAD.left} y={PAD.top} width={WIDTH - PAD.left - PAD.right} height={HEIGHT - PAD.top - PAD.bottom} fill="#f8fafc" rx="4" />
      {yTicks.map((tick) => <g key={tick}><line x1={PAD.left} y1={y(tick)} x2={WIDTH - PAD.right} y2={y(tick)} stroke="#e2e8f0" /><text x={PAD.left - 10} y={y(tick) + 4} textAnchor="end" fontSize="11" fill="#64748b">{fmt(tick, yTickDigits)}</text></g>)}
      <line x1={PAD.left} y1={HEIGHT - PAD.bottom} x2={WIDTH - PAD.right} y2={HEIGHT - PAD.bottom} stroke="#94a3b8" /><line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={HEIGHT - PAD.bottom} stroke="#94a3b8" />
      <g clipPath="url(#directionality-plot-area)">
        {expectedLine && <line x1={x(expectedLine.x1)} y1={y(expectedLine.y1)} x2={x(expectedLine.x2)} y2={y(expectedLine.y2)} stroke="#16a34a" strokeWidth="2.25" strokeDasharray="7 6" opacity="0.8" markerEnd="url(#expected-direction-arrow)" />}
        {!binary && sample.map((row, index) => <circle key={index} cx={x(row.x)} cy={y(row.y)} r="2" fill="#94a3b8" opacity="0.26" />)}
        {curve.length > 1 && <path d={line(curve)} fill="none" stroke="#4f46e5" strokeWidth="2.5" />}
        {bins.length > 1 && <path d={line(bins)} fill="none" stroke="#e11d48" strokeWidth="3" />}
      </g>
      {bins.map((row) => <circle key={row.bin_number} cx={x(row.feature_mean)} cy={y(row.reference_mean)} r="6" fill="#e11d48" stroke="white" strokeWidth="2"><title>{`Bin ${row.bin_number}: average ${fmt(row.reference_mean, 3)}, n=${Number(row.n_observations || 0).toLocaleString()}`}</title></circle>)}
      <text x={(PAD.left + WIDTH - PAD.right) / 2} y={HEIGHT - 10} textAnchor="middle" fontSize="13" fill="#475569">{feature}</text><text transform={`translate(18 ${(PAD.top + HEIGHT - PAD.bottom) / 2}) rotate(-90)`} textAnchor="middle" fontSize="13" fill="#475569">{binary ? `Average event rate · ${reference}` : `Average ${reference}`}</text>
      <text x={PAD.left} y={HEIGHT - PAD.bottom + 20} fontSize="11" fill="#64748b">{fmt(xMin, 2)}</text><text x={WIDTH - PAD.right} y={HEIGHT - PAD.bottom + 20} textAnchor="end" fontSize="11" fill="#64748b">{fmt(xMax, 2)}</text>
      <g transform="translate(72 25)"><line x1="0" y1="0" x2="25" y2="0" stroke="#e11d48" strokeWidth="3" /><text x="32" y="4" fontSize="11" fill="#334155">Bin average</text><line x1="126" y1="0" x2="151" y2="0" stroke="#4f46e5" strokeWidth="2.5" /><text x="158" y="4" fontSize="11" fill="#334155">Regression fit</text>{expectedLine && <><line x1="269" y1="0" x2="296" y2="0" stroke="#16a34a" strokeWidth="2.25" strokeDasharray="6 5" /><text x="303" y="4" fontSize="11" fill="#334155">Expected direction</text></>}</g>
    </svg>
    <p className="px-2 pb-1 text-xs text-slate-500">The y-axis is focused on the observed bin averages and fitted relationship so the directional pattern remains readable. The dashed green guide shows direction only, not an expected magnitude.</p>
    {!!bins.length && <div className="grid gap-2 border-t border-slate-100 px-2 py-3 sm:grid-cols-3 lg:grid-cols-5">{bins.map((row) => <div key={row.bin_number} className="rounded-lg border border-rose-100 bg-rose-50/60 px-3 py-2"><div className="flex items-center justify-between gap-2"><span className="text-xs font-medium text-rose-700">Bin {row.bin_number}</span><span className="text-[11px] text-slate-500">n={Number(row.n_observations || 0).toLocaleString()}</span></div><strong className="mt-1 block text-sm text-slate-900">Average {fmt(row.reference_mean, 3)}</strong><span className="block truncate text-[11px] text-slate-500" title={`${fmt(row.lower_bound, 3)} to ${fmt(row.upper_bound, 3)}`}>{fmt(row.lower_bound, 3)} to {fmt(row.upper_bound, 3)}</span></div>)}</div>}
  </div>;
}

function EvidenceStats({ evidence }) {
  return <div className="grid gap-2 sm:grid-cols-4"><div className="rounded border border-slate-200 p-2"><small className="text-slate-500">Spearman</small><strong className="block">{fmt(evidence.spearman?.value)} <span className="text-xs font-normal text-slate-500">p={fmt(evidence.spearman?.p_value)}</span></strong></div><div className="rounded border border-slate-200 p-2"><small className="text-slate-500">Regression coefficient</small><strong className="block">{fmt(evidence.regression?.value)} <span className="text-xs font-normal text-slate-500">p={fmt(evidence.regression?.p_value)}</span></strong></div><div className="rounded border border-slate-200 p-2"><small className="text-slate-500">Pearson · display only</small><strong className="block">{fmt(evidence.pearson?.value)} <span className="text-xs font-normal text-slate-500">p={fmt(evidence.pearson?.p_value)}</span></strong></div><div className="rounded border border-slate-200 p-2"><small className="text-slate-500">Population</small><strong className="block">{Number(evidence.n_paired || 0).toLocaleString()}</strong><span className="text-xs text-slate-500">{Number(evidence.n_dropped || 0).toLocaleString()} dropped · {Number(evidence.n_special_value_dropped || 0).toLocaleString()} special</span></div></div>;
}

function ResultDetail({ metrics }) {
  const [segment, setSegment] = useState("__overall__");
  const selected = segment === "__overall__" ? { evidence: metrics.evidence, comparison: metrics.comparison }
    : metrics.segments.find((row) => row.segment === segment) || { evidence: metrics.evidence, comparison: metrics.comparison };
  const evidence = selected.evidence;
  const download = () => {
    const blob = new Blob([JSON.stringify(selected, null, 2)], { type: "application/json" });
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob);
    link.download = `${metrics.feature}-directionality-evidence.json`; link.click(); URL.revokeObjectURL(link.href);
  };
  return <div className="grid gap-3"><div className="flex flex-wrap items-center gap-2"><label className="text-xs font-medium text-slate-600">Evidence scope <select value={segment} onChange={(event) => setSegment(event.target.value)} className="ml-2 rounded border border-slate-300 px-2 py-1"><option value="__overall__">Overall</option>{metrics.segments.map((row) => <option key={row.segment} value={row.segment}>{row.segment}</option>)}</select></label><Button size="sm" variant="outline" onClick={download}><Download className="h-3.5 w-3.5" /> Download evidence</Button></div><div className="rounded border border-indigo-200 bg-indigo-50 p-3 text-sm"><strong>Expected {selected.comparison.expected_reference_direction}</strong><span className="mx-2">vs</span><strong>Observed {selected.comparison.observed_direction}</strong><Badge className="ml-2">{selected.comparison.conclusion}</Badge><p className="mt-1 text-xs text-slate-600">{evidence.status_reason}</p></div><EvidenceStats evidence={evidence} /><EvidenceChart evidence={evidence} feature={metrics.feature} reference={metrics.reference.column} expectedDirection={selected.comparison.expected_reference_direction} /><p className="flex gap-1 text-xs text-slate-500"><Info className="h-3.5 w-3.5 shrink-0" />The chart explains immutable empirical evidence. Any user disposition is recorded separately and does not rewrite the observed direction.</p></div>;
}

function IssueDecisionPanel({ row, onDisposition, onPromote, onCloseIssue }) {
  const finding = (row?.findings || [])[0];
  const issue = finding?.existing_issue;
  const openFinding = finding?.review_state === "open";
  return <div className={`mb-4 rounded-lg border p-4 ${finding ? "border-amber-200 bg-amber-50/60" : "border-slate-200 bg-slate-50"}`}>
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h4 className="text-sm font-semibold text-slate-900">Issue decision</h4><p className="mt-1 text-xs text-slate-600">{finding ? "This contextual finding requires an SME decision. Promote or dismiss it with rationale." : "No contextual finding was generated. You can still override the outcome and raise an issue with rationale."}</p></div><FindingStateBadge finding={finding} fallback={<Badge variant="secondary">No finding</Badge>} /></div>
    <div className="mt-3">{issue ? <IssueLifecycleActions finding={finding} onCloseIssue={onCloseIssue} /> : openFinding ? <FindingActions finding={finding} onDisposition={onDisposition} /> : <OverrideIssueAction resultId={row.result_id} onPromote={onPromote} />}</div>
  </div>;
}

export default function DirectionalityResults({ results = [], onDisposition, onCloseIssue,
  onPromote, workflowFilter = "all", onWorkflowFilter, workflowSummary, runId }) {
  const features = useMemo(() => results.filter(
    (row) => row.metrics_json?.result_kind === "directionality_feature",
  ), [results]);
  const summary = results.find((row) => row.metrics_json?.result_kind === "directionality_run_summary")?.metrics_json;
  const grouped = useMemo(() => groupObserved(features), [features]);
  const [activeGroup, setActiveGroup] = useState("ALL");
  const [expanded, setExpanded] = useState("");
  const [reportError, setReportError] = useState("");
  const groupVisible = activeGroup === "ALL" ? features : grouped[activeGroup] || [];
  const visible = groupVisible.filter((row) => matchesFindingFilter(row, workflowFilter));
  const selectedRow = features.find((row) => row.result_id === expanded);
  const selected = selectedRow?.metrics_json;
  return <section className="grid gap-4" data-testid="directionality-results">
    <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,1fr)]"><header className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="font-semibold text-slate-900">Directional consistency evidence</h2><p className="mt-1 text-sm text-slate-500">Review expected and observed directions in one compact list. Open a variable to inspect bin shape, correlations, regression evidence, and segment results.</p></div>
        {summary && <div className="flex flex-wrap gap-2 text-xs"><Badge>{summary.rollup.features_completed} completed</Badge><Badge variant="secondary">{summary.rollup.review_findings} contextual findings</Badge>{summary.rollup.features_failed > 0 && <Badge className="border-transparent bg-red-100 text-red-800">{summary.rollup.features_failed} unavailable</Badge>}</div>}
      </div>
      <div className="mt-4 flex flex-wrap gap-1.5" aria-label="Filter results by observed direction">
        {[["ALL", "All"], ...OBSERVED_COLUMNS].map(([key, label]) => {
          const count = key === "ALL" ? features.length : grouped[key].length;
          const active = activeGroup === key;
          return <button key={key} type="button" onClick={() => { setActiveGroup(key); if (key === "ALL") onWorkflowFilter?.("all"); }} className={`max-w-36 rounded-full border px-2.5 py-1 text-xs font-medium leading-tight whitespace-normal transition ${active ? "border-amber-400 bg-amber-400 text-slate-950" : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"}`}>{label} · {count}</button>;
        })}
      </div>
      <p className="mt-2 text-xs text-slate-500">A contextual finding flags evidence that needs SME judgment. It is not an issue unless a user explicitly promotes it with a rationale.</p>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3"><Button size="sm" variant="outline" disabled={!runId} onClick={async () => { setReportError(""); try { const blob = await downloadDiagnosticReportV2(runId); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `directionality-${runId}.pdf`; link.click(); URL.revokeObjectURL(url); } catch (error) { setReportError(error.message); } }}><Download className="h-4 w-4" /> Download analysis report</Button><Button size="sm" variant="outline" disabled={!runId} onClick={() => window.open(diagnosticReportUrlV2(runId, "text"), "_blank")}><FileText className="h-4 w-4" /> View text report</Button>{reportError && <span className="text-xs text-red-600">{reportError}</span>}</div>
    </header>{workflowSummary}</div>
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-3"><div><h3 className="text-sm font-semibold text-slate-800">{activeGroup === "ALL" ? "All analysed variables" : readableEnum(activeGroup)}</h3><p className="text-xs text-slate-500">{visible.length} variable{visible.length === 1 ? "" : "s"} · immutable evidence retained in the Analytics Artifact Repository</p></div></div>
      <div className="max-h-[min(65vh,46rem)] overflow-auto">
        <table className="w-full min-w-[980px] text-left text-sm">
          <thead className="sticky top-0 z-10 border-b border-slate-200 bg-white text-xs uppercase tracking-wide text-slate-500 shadow-[0_1px_0_0_rgb(226_232_240)]"><tr><th className="px-4 py-3">Variable</th><th className="px-4 py-3">Expected direction</th><th className="px-4 py-3">Observed direction</th><th className="px-4 py-3">Evidence</th><th className="px-4 py-3">Outcome</th><th className="px-4 py-3">Issue workflow</th><th className="px-4 py-3 text-right">Details</th></tr></thead>
          <tbody>{visible.map((row) => { const metrics = row.metrics_json; const comparison = metrics.comparison; const agreement = comparison.conclusion === "AGREEMENT"; const finding = (row.findings || [])[0]; return <tr key={row.result_id} className="border-b border-slate-100 align-middle hover:bg-slate-50/80"><td className="px-4 py-3"><strong className="block text-slate-900">{metrics.feature}</strong><span className="text-xs text-slate-500">{metrics.canonical_feature ? readableEnum(metrics.canonical_feature) : "User-classified variable"}</span></td><td className="px-4 py-3 text-slate-700">{readableEnum(comparison.expected_reference_direction)}</td><td className="px-4 py-3"><Badge variant="secondary">{readableEnum(comparison.observed_direction)}</Badge></td><td className="px-4 py-3"><span className="font-medium text-slate-800">{readableEnum(metrics.evidence.evidence_strength)}</span><span className="block text-xs text-slate-500">2–3 source consensus</span></td><td className="px-4 py-3"><span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${agreement ? "bg-emerald-100 text-emerald-800" : comparison.review_recommended ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700"}`}>{readableEnum(comparison.conclusion)}</span></td><td className="px-4 py-3"><FindingStateBadge finding={finding} fallback={<span className="text-xs text-slate-400">No review required</span>} /></td><td className="px-4 py-3 text-right"><Button size="sm" variant="outline" title={`Artifact ${metrics.artifact_id}`} onClick={() => setExpanded(row.result_id)}>View evidence <ChevronRight className="h-4 w-4" /></Button></td></tr>; })}{!visible.length && <tr><td colSpan="7" className="px-4 py-10 text-center text-sm text-slate-500">No variables match the selected evidence and issue-workflow filters.</td></tr>}</tbody>
        </table>
      </div>
    </div>
    {selected && <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/40 p-4" role="dialog" aria-modal="true" aria-label={`${selected.feature} directionality evidence`}><div className="mx-auto max-w-6xl rounded-xl bg-white p-5 shadow-xl"><div className="mb-3 flex items-center justify-between"><div><h3 className="font-semibold">{selected.feature}</h3><p className="text-xs text-slate-500">Governed artifact · {selected.artifact_id}</p></div><Button size="sm" variant="outline" onClick={() => setExpanded("")}>Close</Button></div><IssueDecisionPanel row={selectedRow} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} /><ResultDetail metrics={selected} /></div></div>}
  </section>;
}

export { EvidenceChart };
