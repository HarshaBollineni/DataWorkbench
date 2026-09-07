import { useMemo, useState } from "react";
import { ChevronRight, Download, FileText, Info } from "lucide-react";

import { diagnosticReportUrlV2, downloadDiagnosticReportV2 } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FindingActions, FindingStateBadge, IssueLifecycleActions, OverrideIssueAction } from "@/pages/testlab/FindingWorkflow";
import { matchesFindingFilter } from "@/pages/testlab/findingWorkflowState";
import { OBSERVED_COLUMNS, fmt, groupObserved } from "./directionalityWorkflow";

const WIDTH = 1000;
const HEIGHT = 410;
const PAD = { left: 74, right: 26, top: 52, bottom: 52 };

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

function segmentDisplayName(row, definition) {
  const compact = (value) => String(value || "selected").replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "selected";
  if (definition?.split_feature) return `seg_${compact(definition.split_feature)}`;
  return row?.segment || "seg_segment";
}

function segmentPredicateLabel(definition) {
  const feature = String(definition?.split_feature || "Segment");
  const expression = definition?.expression || {};
  const operator = expression.operator;
  if (operator === "in") {
    const values = expression.values || [];
    const numbers = values.map(Number);
    const consecutiveIntegers = values.length > 1 && numbers.every(Number.isInteger)
      && numbers.every((value, index) => index === 0 || value === numbers[index - 1] + 1);
    return consecutiveIntegers
      ? `${feature}: ${values[0]}–${values.at(-1)}`
      : `${feature} ∈ {${values.join(", ")}}`;
  }
  if (operator === "range") {
    const left = expression.lower_inclusive === false ? "<" : "≤";
    const right = expression.upper_inclusive === false ? "<" : "≤";
    return `${expression.lower} ${left} ${feature} ${right} ${expression.upper}`;
  }
  if (["<", "<=", "≤", "=", ">=", "≥", ">"].includes(operator)) {
    return `${feature} ${operator.replace("<=", "≤").replace(">=", "≥")} ${expression.value}`;
  }
  return `${feature}: selected predicate`;
}

function EvidenceChart({ evidence, parentEvidence, feature, reference, expectedDirection }) {
  const datasets = parentEvidence
    ? [{ key: "parent", label: "Parent", evidence: parentEvidence, dashed: false }, { key: "segment", label: "Segment", evidence, dashed: true }]
    : [{ key: "scope", label: "Selected scope", evidence, dashed: false }];
  const bins = datasets.flatMap((dataset) => dataset.evidence.binned?.bins || []);
  const sample = parentEvidence ? [] : evidence.chart_sample || [];
  const curves = datasets.flatMap((dataset) => dataset.evidence.regression?.curve || []);
  const binary = evidence.event_value != null;
  const binXValues = bins.map((row) => Number(row.feature_mean)).filter(Number.isFinite);
  const binXMin = binXValues.length ? Math.min(...binXValues) : null;
  const binXMax = binXValues.length ? Math.max(...binXValues) : null;
  const visibleCurve = binXValues.length > 1
    ? curves.filter((row) => Number(row.x) >= binXMin && Number(row.x) <= binXMax)
    : curves;
  const xValues = binXValues.length > 1 ? binXValues
    : [...curves.map((row) => row.x), ...sample.map((row) => row.x)];
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
  return <figure className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
    <div className="overflow-x-auto px-2 pt-1">
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="block h-auto w-full min-w-[680px]" role="img" aria-label={`${feature} directionality evidence against ${reference}`}>
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
        {datasets.map((dataset) => { const rows = dataset.evidence.regression?.curve || []; return rows.length > 1 && <path key={`regression-${dataset.key}`} d={line(rows)} fill="none" stroke="#4f46e5" strokeWidth="2.5" strokeDasharray={dataset.dashed ? "7 5" : undefined} />; })}
        {datasets.map((dataset) => { const rows = dataset.evidence.binned?.bins || []; return rows.length > 1 && <path key={`bins-${dataset.key}`} d={line(rows)} fill="none" stroke="#e11d48" strokeWidth="3" strokeDasharray={dataset.dashed ? "7 5" : undefined} />; })}
      </g>
      {datasets.flatMap((dataset) => (dataset.evidence.binned?.bins || []).map((row) => <circle key={`${dataset.key}-${row.bin_number}`} cx={x(row.feature_mean)} cy={y(row.reference_mean)} r={dataset.dashed ? "5" : "6"} fill={dataset.dashed ? "white" : "#e11d48"} stroke="#e11d48" strokeWidth="2"><title>{`${dataset.label} · Bin ${row.bin_number}: average ${fmt(row.reference_mean, 3)}, n=${Number(row.n_observations || 0).toLocaleString()}`}</title></circle>))}
      <text x={(PAD.left + WIDTH - PAD.right) / 2} y={HEIGHT - 10} textAnchor="middle" fontSize="13" fill="#475569">{feature}</text><text transform={`translate(18 ${(PAD.top + HEIGHT - PAD.bottom) / 2}) rotate(-90)`} textAnchor="middle" fontSize="13" fill="#475569">{binary ? `Average event rate · ${reference}` : `Average ${reference}`}</text>
      <text x={PAD.left} y={HEIGHT - PAD.bottom + 20} fontSize="11" fill="#64748b">{fmt(xMin, 2)}</text><text x={WIDTH - PAD.right} y={HEIGHT - PAD.bottom + 20} textAnchor="end" fontSize="11" fill="#64748b">{fmt(xMax, 2)}</text>
      <g transform="translate(72 25)"><line x1="0" y1="0" x2="25" y2="0" stroke="#e11d48" strokeWidth="3" /><text x="32" y="4" fontSize="11" fill="#334155">Bin average</text><line x1="126" y1="0" x2="151" y2="0" stroke="#4f46e5" strokeWidth="2.5" /><text x="158" y="4" fontSize="11" fill="#334155">Regression fit</text>{parentEvidence && <><line x1="269" y1="0" x2="296" y2="0" stroke="#475569" strokeWidth="2" /><text x="303" y="4" fontSize="11" fill="#334155">Parent</text><line x1="353" y1="0" x2="380" y2="0" stroke="#475569" strokeWidth="2" strokeDasharray="6 5" /><text x="387" y="4" fontSize="11" fill="#334155">Segment</text></>}{expectedLine && !parentEvidence && <><line x1="269" y1="0" x2="296" y2="0" stroke="#16a34a" strokeWidth="2.25" strokeDasharray="6 5" /><text x="303" y="4" fontSize="11" fill="#334155">Expected direction</text></>}</g>
    </svg>
    </div>
    <figcaption className="grid gap-0.5 border-t border-slate-100 px-3 py-2 text-[11px] leading-4 text-slate-500"><span>The y-axis is focused on the observed bin averages and fitted relationship so the directional pattern remains readable. The dashed green guide shows direction only, not an expected magnitude.</span><span className="flex gap-1"><Info className="mt-0.5 h-3 w-3 shrink-0" />The chart explains immutable empirical evidence. Any user disposition is recorded separately and does not rewrite the observed direction.</span></figcaption>
  </figure>;
}

function EvidenceStats({ evidence, parentEvidence }) {
  const scopes = parentEvidence ? [["Parent", parentEvidence], ["Segment", evidence]] : [[null, evidence]];
  const populationDetails = (row) => {
    const usable = Number(row.n_paired || 0);
    const excluded = Number(row.n_dropped || 0);
    const special = Number(row.n_special_value_dropped || 0);
    const missingOrNonFinite = Math.max(0, excluded - special);
    const input = Number.isFinite(Number(row.n_input)) ? Number(row.n_input) : usable + excluded;
    return `${input.toLocaleString()} input · ${excluded.toLocaleString()} excluded (${special.toLocaleString()} special; ${missingOrNonFinite.toLocaleString()} missing/non-finite)`;
  };
  const metrics = [
    ["Population", (row) => `${Number(row.n_paired || 0).toLocaleString()} usable`, populationDetails],
    ["Spearman", (row) => fmt(row.spearman?.value), (row) => `p=${fmt(row.spearman?.p_value)}`],
    ["Regression coefficient", (row) => fmt(row.regression?.value), (row) => `p=${fmt(row.regression?.p_value)}`],
    ["Pearson · display only", (row) => fmt(row.pearson?.value), (row) => `p=${fmt(row.pearson?.p_value)}`],
  ];
  return <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(([label, value, note]) => <div key={label} className="rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-sm"><small className="font-medium text-slate-500">{label}</small><div className={`mt-1 grid ${parentEvidence ? "grid-cols-2 divide-x divide-slate-200" : ""}`}>{scopes.map(([scope, row]) => <div key={scope || "scope"} className={scope === "Segment" ? "pl-2" : parentEvidence ? "pr-2" : ""}>{scope && <span className="block text-[10px] uppercase tracking-wide text-slate-400">{scope}</span>}<strong className="block text-sm text-slate-900">{value(row)}</strong><span className="block text-[10px] leading-4 text-slate-500">{note(row)}</span></div>)}</div></div>)}</div>;
}

function BinEvidenceTable({ bins = [], title = "Bin averages" }) {
  if (!bins.length) return null;
  return <div className="overflow-hidden rounded-lg border border-slate-200">
    <div className="flex items-center justify-between bg-rose-50/70 px-2.5 py-1"><h4 className="text-xs font-semibold text-slate-800">{title}</h4><span className="text-[10px] text-slate-500">Average · population</span></div>
    <table className="w-full text-left text-[11px] leading-4">
      <thead className="border-y border-slate-200 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500"><tr><th className="px-2 py-0.5">Bin</th><th className="px-2 py-0.5 text-right">Average</th><th className="px-2 py-0.5 text-right">Population</th><th className="px-2 py-0.5 text-right">Feature range</th></tr></thead>
      <tbody>{bins.map((row) => <tr key={row.bin_number} className="border-b border-slate-100 last:border-b-0"><td className="px-2 py-0.5 font-medium text-rose-700">{row.bin_number}</td><td className="px-2 py-0.5 text-right font-semibold text-slate-900">{fmt(row.reference_mean, 3)}</td><td className="px-2 py-0.5 text-right text-slate-600">{Number(row.n_observations || 0).toLocaleString()}</td><td className="px-2 py-0.5 text-right text-slate-500" title={`${fmt(row.lower_bound, 3)} to ${fmt(row.upper_bound, 3)}`}>{fmt(row.lower_bound, 2)}–{fmt(row.upper_bound, 2)}</td></tr>)}</tbody>
    </table>
  </div>;
}

function ResultDetail({ metrics }) {
  const analysisView = metrics.analysis_view || { mode: "overall_only" };
  const segmented = analysisView.mode === "segmented_rerun";
  const population = analysisView.segmentation?.population_counts || metrics.segment_preview;
  const segmentOptions = (metrics.segments || []).map((row) => ({ ...row, displayName: segmentDisplayName(row, metrics.segment_definition) }));
  const selectedSegment = segmented ? segmentOptions[0] : null;
  const selected = selectedSegment || { evidence: metrics.evidence, comparison: metrics.comparison };
  const evidence = selected.evidence;
  const comparingSegment = Boolean(selectedSegment);
  const parentEvidence = comparingSegment ? metrics.evidence : null;
  const segmentDetails = segmentPredicateLabel(metrics.segment_definition);
  const download = () => {
    const downloadPayload = comparingSegment
      ? { scope: "parent_vs_segment", parent: { evidence: metrics.evidence, comparison: metrics.comparison }, segment: selected }
      : selected;
    const blob = new Blob([JSON.stringify(downloadPayload, null, 2)], { type: "application/json" });
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob);
    link.download = `${metrics.feature}-directionality-${comparingSegment ? "comparison" : "evidence"}.json`; link.click(); URL.revokeObjectURL(link.href);
  };
  const scopeNote = comparingSegment
    ? `The parent uses the entire sample. The accepted segment contains ${Number(population?.baseline_count || evidence.n_paired || 0).toLocaleString()} rows; ${Number(population?.current_count || 0).toLocaleString()} complementary rows were retained as Not analysed.`
    : "This result uses the entire analysis sample; no segment split was applied.";
  return <div className="grid min-h-0 gap-3">
    <div className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2.5 text-sm"><div className="flex flex-wrap items-center gap-x-1.5 gap-y-1"><strong>Expected {selected.comparison.expected_reference_direction}</strong><span className="text-slate-500">vs</span><strong>Observed {selected.comparison.observed_direction}</strong><span className="text-slate-500">with</span><strong className="text-indigo-800">{metrics.reference.column}</strong><span className="text-slate-400">—</span><Badge>{selected.comparison.conclusion}</Badge>{comparingSegment && <span className="text-xs font-medium text-teal-700">· Segment result</span>}</div><p className="mt-1 text-xs leading-4 text-slate-600">{evidence.status_reason}</p></div>
    <EvidenceStats evidence={evidence} parentEvidence={parentEvidence} />
    <div className="grid gap-3 xl:grid-cols-[minmax(18rem,35fr)_minmax(0,65fr)] xl:items-start">
    <aside className="grid content-start gap-2" aria-label="Evidence scope and bin averages">
      <div className={`rounded-lg border px-3 py-2 text-xs leading-4 ${comparingSegment ? "border-teal-200 bg-teal-50 text-teal-900" : "border-slate-200 bg-slate-50 text-slate-700"}`}>
        <div className="flex flex-wrap items-end gap-2"><div className="font-medium"><span className="block text-[10px] uppercase tracking-wide opacity-70">Evidence scope</span><div className="mt-1 flex flex-wrap gap-1.5"><Badge variant="outline" className={comparingSegment ? "border-teal-300 bg-white text-teal-800" : "bg-white text-slate-700"}>{comparingSegment ? `Parent vs ${selectedSegment.displayName}` : "Entire sample"}</Badge>{comparingSegment && <Badge variant="outline" className="max-w-full whitespace-normal border-teal-300 bg-white text-left font-medium text-teal-800" title={segmentDetails}>{segmentDetails}</Badge>}</div></div><Button className="ml-auto" size="sm" variant="outline" onClick={download}><Download className="h-3.5 w-3.5" /> Download evidence</Button></div>
        <div className="mt-1.5 border-t border-current/10 pt-1.5"><strong>{comparingSegment ? "Comparative view. " : "Entire sample. "}</strong><span>{scopeNote}</span></div>
      </div>
      {comparingSegment && <BinEvidenceTable bins={metrics.evidence.binned?.bins} title="Parent · bin averages" />}
      <BinEvidenceTable bins={evidence.binned?.bins} title={comparingSegment ? "Segment · bin averages" : "Bin averages"} />
    </aside>
    <div className="min-w-0"><EvidenceChart evidence={evidence} parentEvidence={parentEvidence} feature={metrics.feature} reference={metrics.reference.column} expectedDirection={selected.comparison.expected_reference_direction} /></div>
    </div>
  </div>;
}

function IssueDecisionPanel({ row, onDisposition, onPromote, onCloseIssue }) {
  const finding = (row?.findings || [])[0];
  const issue = finding?.existing_issue;
  const openFinding = finding?.review_state === "open";
  return <div className={`rounded-lg border px-3 py-2 ${finding ? "border-amber-200 bg-amber-50/60" : "border-slate-200 bg-slate-50"}`}>
    <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h4 className="text-sm font-semibold text-slate-900">Issue decision</h4><FindingStateBadge finding={finding} fallback={<Badge variant="secondary">No finding</Badge>} /></div><p className="text-xs leading-4 text-slate-600">{finding ? "This contextual finding requires an SME decision. Promote or dismiss it with rationale." : "No contextual finding was generated. You can still override the outcome and raise an issue with rationale."}</p></div>
      <div className="lg:justify-self-end">{issue ? <IssueLifecycleActions finding={finding} onCloseIssue={onCloseIssue} /> : openFinding ? <FindingActions finding={finding} onDisposition={onDisposition} /> : <OverrideIssueAction resultId={row.result_id} onPromote={onPromote} />}</div>
    </div>
  </div>;
}

export default function DirectionalityResults({ results = [], onDisposition, onCloseIssue,
  onPromote, workflowFilter = "all", diagnosticFilter = "all", onDiagnosticFilter,
  workflowSummary, runId }) {
  const features = useMemo(() => results.filter(
    (row) => row.metrics_json?.result_kind === "directionality_feature",
  ), [results]);
  const summary = results.find((row) => row.metrics_json?.result_kind === "directionality_run_summary")?.metrics_json;
  const grouped = useMemo(() => groupObserved(features), [features]);
  const [expanded, setExpanded] = useState("");
  const [reportError, setReportError] = useState("");
  const activeGroup = diagnosticFilter === "all" ? "ALL" : diagnosticFilter;
  const groupVisible = activeGroup === "ALL" ? features : grouped[activeGroup] || [];
  const visible = workflowFilter === "all"
    ? groupVisible
    : features.filter((row) => matchesFindingFilter(row, workflowFilter));
  const selectedRow = features.find((row) => row.result_id === expanded);
  const selected = selectedRow?.metrics_json;
  const runView = features[0]?.metrics_json?.analysis_view || { mode: "overall_only" };
  const segmentedRun = runView.mode === "segmented_rerun";
  const runPopulation = runView.segmentation?.population_counts;
  return <section className="grid gap-4" data-testid="directionality-results">
    <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,1fr)]"><header className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="font-semibold text-slate-900">Directional consistency evidence</h2><p className="mt-1 text-sm text-slate-500">Review expected and observed directions in one compact list. Open a variable to inspect bin shape, correlations, regression evidence, and segment results.</p></div>
        {summary && <div className="flex flex-wrap gap-2 text-xs"><Badge>{summary.rollup.features_completed} completed</Badge><Badge variant="secondary">{summary.rollup.review_findings} contextual findings</Badge>{summary.rollup.features_failed > 0 && <Badge className="border-transparent bg-red-100 text-red-800">{summary.rollup.features_failed} unavailable</Badge>}</div>}
      </div>
      <div className={`mt-3 rounded-md border px-3 py-2 text-xs ${segmentedRun ? "border-teal-200 bg-teal-50 text-teal-900" : "border-slate-200 bg-slate-50 text-slate-700"}`}><strong>{segmentedRun ? "Segmented rerun" : "Entire sample run"}</strong><span className="ml-2">{segmentedRun ? `Overall evidence uses the entire sample. An additional result uses the accepted segment (${Number(runPopulation?.baseline_count || 0).toLocaleString()} rows); the complement (${Number(runPopulation?.current_count || 0).toLocaleString()} rows) was not analysed.` : "All displayed results use the full analysis sample; no segment split was applied."}</span></div>
      <div className="mt-4 flex flex-wrap gap-1.5" aria-label="Filter results by observed direction">
        {[["ALL", "All"], ...OBSERVED_COLUMNS].map(([key, label]) => {
          const count = key === "ALL" ? features.length : grouped[key].length;
          const active = activeGroup === key;
          return <button key={key} type="button" onClick={() => onDiagnosticFilter?.(key === "ALL" ? "all" : key)} className={`max-w-36 rounded-full border px-2.5 py-1 text-xs font-medium leading-tight whitespace-normal transition ${active ? "border-amber-400 bg-amber-400 text-slate-950" : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"}`}>{label} · {count}</button>;
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
    {selected && <div className="fixed inset-0 z-50 flex items-start justify-center overflow-hidden bg-slate-950/40 p-2 sm:p-3" role="dialog" aria-modal="true" aria-label={`${selected.feature} directionality evidence`}>
      <div className="flex max-h-[calc(100dvh-1rem)] w-full max-w-[96rem] flex-col overflow-hidden rounded-xl bg-white shadow-xl sm:max-h-[calc(100dvh-1.5rem)]">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-2 sm:px-4"><div className="mr-auto min-w-40"><h3 className="truncate font-semibold text-slate-900">{selected.feature}</h3><p className="truncate text-[11px] text-slate-500">Governed artifact · {selected.artifact_id}</p></div><div className="min-w-0 max-w-3xl flex-1"><IssueDecisionPanel row={selectedRow} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} /></div><Button size="sm" variant="outline" onClick={() => setExpanded("")}>Close</Button></div>
        <div className="min-h-0 overflow-y-auto px-3 py-2 sm:px-4 sm:pb-3"><ResultDetail key={selected.artifact_id} metrics={selected} /></div>
      </div>
    </div>}
  </section>;
}

export { EvidenceChart };
