import { useMemo, useState } from "react";
import { AlertTriangle, BarChart3, Check, ChevronRight, CircleGauge, Download, ExternalLink, FileText, Info, TrendingDown, X } from "lucide-react";

import { diagnosticReportUrlV2, downloadDiagnosticReportV2 } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatBinRows } from "@/features/test-lab/shared/binning/binLabelDisplay";
import { FindingStateBadge, IssueLifecycleActions } from "@/pages/testlab/FindingWorkflow";
import { findingWorkflowState, matchesFindingFilter } from "@/pages/testlab/findingWorkflowState";
import { formatProfileNumber, primaryTreePerformance, profilePercentiles, promotionRecommendation } from "./featureTargetWorkflow";

const fmt = (value, digits = 4) => value == null ? "—" : Number(value).toFixed(digits);
const pct = (value, digits = 2) => value == null ? "—" : `${(Number(value) * 100).toFixed(digits)}%`;
const readable = (value) => String(value || "—").replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());

function CandidateActions({ finding, onDisposition }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);
  const [promoting, setPromoting] = useState(false);
  if (!finding) return <span className="text-xs text-slate-400">No review candidate</span>;
  if (finding.review_state !== "open") return <Badge variant={finding.review_state === "confirmed" ? "success" : "secondary"}>{finding.review_state}</Badge>;
  const act = async (action, rationale, overwrite = false) => {
    setBusy(true); setError("");
    try { await onDisposition(finding.finding_id, action, rationale, overwrite); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };
  return <div className="flex flex-wrap items-center gap-2">
    {finding.existing_issue && !finding.existing_issue.same_finding ? (
      !confirmOverwrite ? <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirmOverwrite(true)}><ExternalLink /> Overwrite promoted issue</Button>
        : <><span className="text-xs text-amber-700">Replace evidence on {finding.existing_issue.issue_row_id}?</span><input value={reason} autoFocus placeholder="Overwrite rationale (required)" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-64 rounded-md border border-slate-200 px-2 text-xs" /><Button size="sm" variant="destructive" disabled={busy || !reason.trim()} onClick={() => act("confirm_issue", reason.trim(), true)}>Confirm overwrite</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirmOverwrite(false)}>Cancel</Button></>
    ) : !promoting ? <Button size="sm" variant="success" disabled={busy} onClick={() => setPromoting(true)}><ExternalLink /> Promote to issue…</Button>
      : <><input value={reason} autoFocus placeholder="Promotion rationale (required)" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-64 rounded-md border border-slate-200 px-2 text-xs" /><Button size="sm" variant="success" disabled={busy || !reason.trim()} onClick={() => act("confirm_issue", reason.trim())}>Confirm promotion</Button></>}
    {!dismissing ? <Button size="sm" variant="outline" disabled={busy} onClick={() => setDismissing(true)}>Dismiss</Button>
      : <><input value={reason} autoFocus placeholder="Reason required" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-44 rounded-md border border-slate-200 px-2 text-xs" /><Button size="sm" variant="outline" disabled={busy || !reason.trim()} onClick={() => act("dismiss", reason.trim())}>Confirm dismissal</Button></>}
    {error && <p className="w-full text-xs text-red-600">{error}</p>}
  </div>;
}

function FeaturePromotionOverride({ row, recommendation, onPromote }) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!onPromote) return null;
  const promote = async () => {
    setBusy(true); setError("");
    try { await onPromote(row.result_id, reason.trim() || undefined); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };
  return <div>
    {!editing && <Button size="sm" variant={recommendation === "recommended" ? "success" : "outline"} onClick={() => setEditing(true)}>{recommendation === "recommended" ? "Promote to issue…" : "Override and promote…"}</Button>}
    {editing && <div className="flex flex-wrap gap-2"><input autoFocus value={reason} placeholder="Promotion rationale (required)" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-64 flex-1 rounded-md border border-slate-200 bg-white px-2 text-xs" /><Button size="sm" disabled={busy || !reason.trim()} onClick={promote}>{busy ? "Promoting…" : "Confirm promotion"}</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing(false)}>Cancel</Button></div>}
    {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
  </div>;
}

function MetricTile({ icon: Icon, label, value, detail, active, onClick }) {
  return <button type="button" onClick={onClick} className={`min-h-24 rounded-lg border p-3 text-left transition ${active ? "border-amber-400 bg-amber-50" : "border-slate-200 bg-white hover:border-slate-300"}`}><span className="flex items-center gap-2 text-xs font-medium text-slate-500"><Icon /> {label}</span><strong className="mt-2 block text-2xl text-slate-950">{value}</strong><small className="text-[11px] text-slate-500">{detail}</small></button>;
}

function Fact({ label, value }) {
  return <div className="rounded-lg border border-slate-200 bg-white px-3 py-2"><span className="block text-[11px] text-slate-500">{label}</span><strong className="mt-0.5 block text-sm text-slate-900">{value}</strong></div>;
}

function VariableProfile({ metrics }) {
  const profile = metrics.data_profile;
  if (!profile) return <section className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-2"><span className="text-xs font-semibold text-slate-800">Variable profile</span><span className="ml-2 text-xs text-slate-500">Retained AAR profile unavailable.</span></section>;
  const numeric = metrics.roc_detail?.feature_type === "numeric" || metrics.binning_detail?.feature_type === "numeric";
  const percentiles = profilePercentiles(profile);
  const topValues = Object.entries(profile.top_k || {}).slice(0, 8);
  const facts = numeric ? [
    ["Rows", Number(profile.total_count || metrics.rows_evaluated || 0).toLocaleString()],
    ["Regular values", Number(profile.regular_value_count ?? profile.non_null_count ?? 0).toLocaleString()],
    ["Missing", Number(profile.effective_missing_count ?? profile.null_count ?? 0).toLocaleString()],
    ["Distinct", Number(profile.distinct_count || 0).toLocaleString()],
    ["Minimum", formatProfileNumber(profile.min)], ["Maximum", formatProfileNumber(profile.max)],
    ["Mean", formatProfileNumber(profile.mean)], ["Std. deviation", formatProfileNumber(profile.stddev)],
  ] : [
    ["Rows", Number(profile.total_count || metrics.rows_evaluated || 0).toLocaleString()],
    ["Non-null", Number(profile.non_null_count || 0).toLocaleString()],
    ["Missing", Number(profile.effective_missing_count ?? profile.null_count ?? 0).toLocaleString()],
    ["Distinct", Number(profile.distinct_count || 0).toLocaleString()],
  ];
  return <section className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5">
    <div className="flex flex-wrap items-center gap-2"><h4 className="mr-1 text-xs font-semibold uppercase tracking-wide text-slate-700">Variable profile</h4><Badge variant="outline">{readable(profile.classification || profile.data_type)}</Badge><span className="text-[11px] text-slate-400">AAR · {metrics.profile_artifact_id || "retained profile"}</span></div>
    <div className={`mt-2 grid gap-1.5 ${numeric ? "grid-cols-2 sm:grid-cols-4 xl:grid-cols-8" : "grid-cols-2 sm:grid-cols-4"}`}>{facts.map(([label, value]) => <Fact key={label} label={label} value={value} />)}</div>
    {numeric && percentiles.length > 0 && <div className="mt-2 grid grid-cols-[auto_1fr] items-center gap-2"><strong className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Percentiles</strong><div className="grid grid-cols-3 gap-1.5 sm:grid-cols-7">{percentiles.map(([label, value]) => <div key={label} className="rounded-md border border-indigo-100 bg-white px-2 py-1.5"><span className="block text-[10px] font-medium text-indigo-600">{label}</span><strong className="block text-xs text-slate-900">{formatProfileNumber(value)}</strong></div>)}</div></div>}
    {!numeric && topValues.length > 0 && <div className="mt-2 grid grid-cols-[auto_1fr] items-center gap-2"><strong className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Top values</strong><div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4 xl:grid-cols-8">{topValues.map(([label, count]) => <div key={label} className="min-w-0 rounded-md border border-indigo-100 bg-white px-2 py-1.5"><span className="block truncate text-[10px] font-medium text-indigo-600" title={label}>{label}</span><strong className="block text-xs text-slate-900">{Number(count).toLocaleString()}</strong></div>)}</div></div>}
  </section>;
}

function RocChart({ rows, auc }) {
  if (rows.length < 2) return <div className="flex min-h-72 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 p-6 text-center text-sm text-slate-500">A binary ROC curve cannot be reconstructed from this retained result.</div>;
  const width = 560, height = 310, left = 58, right = 24, top = 48, bottom = 48;
  const x = (value) => left + value * (width - left - right);
  const y = (value) => height - bottom - value * (height - top - bottom);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-2 shadow-sm"><svg viewBox={`0 0 ${width} ${height}`} className="block h-auto w-full min-w-[460px]" role="img" aria-label={`Primary tree ROC curve with AUC ${fmt(auc, 3)}`}>
    <rect x={left} y={top} width={width-left-right} height={height-top-bottom} fill="#f8fafc" rx="4" />
    {ticks.map((tick) => <g key={tick}><line x1={x(0)} y1={y(tick)} x2={x(1)} y2={y(tick)} stroke="#e2e8f0" /><line x1={x(tick)} y1={y(0)} x2={x(tick)} y2={y(1)} stroke="#e2e8f0" /><text x={left-9} y={y(tick)+4} textAnchor="end" fontSize="10" fill="#64748b">{tick.toFixed(2)}</text><text x={x(tick)} y={height-bottom+18} textAnchor="middle" fontSize="10" fill="#64748b">{tick.toFixed(2)}</text></g>)}
    <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="#94a3b8" strokeDasharray="6 5" />
    <polyline points={rows.map((row) => `${x(row.fpr)},${y(row.tpr)}`).join(" ")} fill="none" stroke="#4f46e5" strokeWidth="3" />
    {rows.map((row, index) => <circle key={index} cx={x(row.fpr)} cy={y(row.tpr)} r="4" fill="#e11d48" stroke="white" strokeWidth="1.5"><title>{index === 0 ? "Origin" : `Threshold ${fmt(row.threshold)} · TPR ${fmt(row.tpr, 3)} · FPR ${fmt(row.fpr, 3)}`}</title></circle>)}
    <text x={left} y="26" fontSize="14" fontWeight="600" fill="#0f172a">Primary tree ROC · AUC {fmt(auc, 3)}</text><text x={(left+width-right)/2} y={height-8} textAnchor="middle" fontSize="12" fill="#475569">False positive rate</text><text transform={`translate(15 ${(top+height-bottom)/2}) rotate(-90)`} textAnchor="middle" fontSize="12" fill="#475569">True positive rate</text>
  </svg></div>;
}

function BinProfileChart({ rows, targetType, iv }) {
  if (!rows.length) return <div className="flex min-h-72 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 p-6 text-sm text-slate-500">Coarse-bin evidence is unavailable.</div>;
  const width = 560, height = 310, left = 58, right = 58, top = 48, bottom = 78;
  const multinomialRate = (row) => Math.max(...Object.values(row.class_rates || {}).map(Number), 0);
  const values = rows.map((row) => Number(targetType === "continuous" ? row.target_mean
    : targetType === "multinomial" ? multinomialRate(row) : row.event_rate) || 0);
  const lineLabel = targetType === "continuous" ? "mean target"
    : targetType === "multinomial" ? "leading class rate" : "target rate";
  const maxShare = Math.max(...rows.map((row) => Number(row.population_share) || 0), 0.01);
  const rateTarget = targetType !== "continuous";
  const observedMin = Math.min(...values), observedMax = Math.max(...values);
  const observedSpan = observedMax-observedMin;
  const valuePadding = observedMin === observedMax ? Math.max(Math.abs(observedMin) * 0.1, 0.5) : observedSpan * 0.08;
  const ratePadding = Math.max(observedSpan * 0.12, Math.abs(observedMax) * 0.03, 0.005);
  const minValue = rateTarget ? Math.max(0, observedMin-ratePadding) : observedMin-valuePadding;
  const maxValue = rateTarget ? Math.min(1, observedMax+ratePadding) : observedMax+valuePadding;
  const slot = (width-left-right) / rows.length;
  const plotHeight = height-top-bottom;
  const lineY = (value) => top + (maxValue-value) / Math.max(maxValue-minValue, Number.EPSILON) * plotHeight;
  const rateDigits = maxValue-minValue < 0.02 ? 2 : maxValue-minValue < 0.2 ? 1 : 0;
  const rightTick = (value) => rateTarget ? pct(value, rateDigits) : formatProfileNumber(value);
  return <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-2 shadow-sm"><svg viewBox={`0 0 ${width} ${height}`} className="block h-auto w-full min-w-[460px]" role="img" aria-label="Coarse bin population and outcome profile">
    <rect x={left} y={top} width={width-left-right} height={plotHeight} fill="#f8fafc" rx="4" />
    <line x1={left} y1={top+plotHeight/2} x2={width-right} y2={top+plotHeight/2} stroke="#cbd5e1" strokeDasharray="4 4" />
    <line x1={left} y1={height-bottom} x2={width-right} y2={height-bottom} stroke="#94a3b8" />
    <text x={left-7} y={top+4} textAnchor="end" fontSize="9" fill="#6366f1">{pct(maxShare, 0)}</text><text x={left-7} y={height-bottom+3} textAnchor="end" fontSize="9" fill="#6366f1">0%</text>
    <text x={width-right+7} y={top+4} fontSize="9" fill="#e11d48">{rightTick(maxValue)}</text><text x={width-right+7} y={height-bottom+3} fontSize="9" fill="#e11d48">{rightTick(minValue)}</text>
    {rows.map((row, index) => { const center = left+slot*(index+0.5); const barHeight = Number(row.population_share || 0)/maxShare*plotHeight; return <g key={row.bin_id}><rect x={center-slot*0.25} y={height-bottom-barHeight} width={slot*0.5} height={barHeight} rx="3" fill={row.kind === "regular" ? "#c7d2fe" : "#bbf7d0"} /><text x={center} y={height-bottom+17} transform={`rotate(-35 ${center} ${height-bottom+17})`} textAnchor="end" fontSize={rows.length > 8 ? "8" : "9"} fill="#64748b">{row.label.length > 18 ? `${row.label.slice(0, 17)}…` : row.label}</text></g>; })}
    <polyline points={rows.map((row, index) => `${left+slot*(index+0.5)},${lineY(values[index])}`).join(" ")} fill="none" stroke="#e11d48" strokeWidth="3" />
    {rows.map((row, index) => <circle key={`point-${row.bin_id}`} cx={left+slot*(index+0.5)} cy={lineY(values[index])} r="5" fill="#e11d48" stroke="white" strokeWidth="2"><title>{`${row.label}: ${lineLabel} ${rateTarget ? pct(values[index]) : formatProfileNumber(values[index])}`}</title></circle>)}
    <text transform={`translate(13 ${top+plotHeight/2}) rotate(-90)`} textAnchor="middle" fontSize="10" fill="#6366f1">Population (%)</text><text transform={`translate(${width-11} ${top+plotHeight/2}) rotate(90)`} textAnchor="middle" fontSize="10" fill="#e11d48">{lineLabel}</text>
    <text x={left} y="26" fontSize="14" fontWeight="600" fill="#0f172a">Bin profile · IV {fmt(iv, 3)}</text>
    <g transform={`translate(${width-right-174} 18)`}>
      <rect x="0" y="0" width="10" height="10" rx="2" fill="#c7d2fe" /><text x="15" y="9" fontSize="9" fill="#475569">Population</text>
      <line x1="78" y1="5" x2="96" y2="5" stroke="#e11d48" strokeWidth="2.5" /><circle cx="87" cy="5" r="3.5" fill="#e11d48" stroke="white" strokeWidth="1" /><text x="102" y="9" fontSize="9" fill="#475569">{lineLabel}</text>
    </g>
  </svg></div>;
}

function EvidenceTable({ title, columns, empty, children }) {
  const hasRows = Array.isArray(children) ? children.length > 0 : Boolean(children);
  return <section className="overflow-hidden rounded-xl border border-slate-200 bg-white"><header className="border-b border-slate-200 bg-slate-50 px-4 py-3"><h4 className="text-sm font-semibold text-slate-800">{title}</h4></header><div className="max-h-80 overflow-y-auto"><table className="w-full table-fixed text-left text-xs"><thead className="sticky top-0 bg-white uppercase tracking-wide text-slate-500"><tr>{columns.map((column) => <th key={column} className="break-words px-2 py-2 align-bottom">{column}</th>)}</tr></thead><tbody className="[&_td]:break-words [&_td]:px-2 [&_td]:align-top">{hasRows ? children : <tr><td colSpan={columns.length} className="px-3 py-8 text-center text-slate-500">{empty}</td></tr>}</tbody></table></div></section>;
}

function PerformanceTable({ metrics, rows }) {
  if (metrics.target_type !== "binary") {
    const configurations = metrics.roc_detail?.configurations || [];
    return <EvidenceTable title="Primary separation summary" columns={["Configuration", "AUC / concordance", "Gini", "Risk", "Leaves"]} empty="Separation summary unavailable.">
      {configurations.map((row) => <tr key={row.configuration} className="border-t border-slate-100"><td className="px-3 py-2 font-medium">{readable(row.configuration)}</td><td className="px-3 py-2">{fmt(row.auc)}</td><td className="px-3 py-2">{fmt(row.gini)}</td><td className="px-3 py-2">{readable(row.risk_tier)}</td><td className="px-3 py-2">{row.leaves}</td></tr>)}
    </EvidenceTable>;
  }
  return <EvidenceTable title="Primary tree ROC and lift" columns={["Score threshold", "Band rows", "Cumulative population", "TPR", "FPR", "Event rate", "Cumulative lift"]} empty="ROC/lift rows unavailable.">
    {rows.slice(1).map((row) => <tr key={row.threshold} className="border-t border-slate-100"><td className="px-3 py-2 font-medium">{fmt(row.threshold)}</td><td className="px-3 py-2">{row.rows.toLocaleString()}</td><td className="px-3 py-2">{pct(row.population_share)}</td><td className="px-3 py-2">{pct(row.tpr)}</td><td className="px-3 py-2">{pct(row.fpr)}</td><td className="px-3 py-2">{pct(row.event_rate)}</td><td className="px-3 py-2">{fmt(row.cumulative_lift, 2)}×</td></tr>)}
  </EvidenceTable>;
}

function BinningTable({ rows, targetType }) {
  const continuous = targetType === "continuous";
  const multinomial = targetType === "multinomial";
  const columns = multinomial
    ? ["Bin", "Rows", "Population", "Leading class", "Leading rate", "Class rates", "Class IV"]
    : ["Bin", "Rows", "Population", continuous ? "Mean target" : "Events", continuous ? "Target std." : "Event rate", continuous ? "—" : "WOE", continuous ? "Variance evidence" : "IV contribution"];
  return <EvidenceTable title="Coarse binning table" columns={columns} empty="Coarse-bin rows unavailable.">
    {rows.map((row) => { const leading = Object.entries(row.class_rates || {}).sort((left, right) => Number(right[1])-Number(left[1]))[0]; return <tr key={row.bin_id} className="border-t border-slate-100"><td className="px-2 py-2 font-medium text-slate-900" title={`${row.label} · ${readable(row.kind)}`}>{row.label}</td><td className="px-2 py-2">{Number(row.rows || 0).toLocaleString()}</td><td className="px-2 py-2">{pct(row.population_share)}</td>{multinomial ? <><td className="px-2 py-2">{leading?.[0] ?? "—"}</td><td className="px-2 py-2">{pct(leading?.[1])}</td><td className="px-2 py-2">{Object.entries(row.class_rates || {}).map(([key, value]) => `${key}: ${pct(value)}`).join(" · ") || "—"}</td><td className="px-2 py-2">{Object.entries(row.class_iv || {}).map(([key, value]) => `${key}: ${fmt(value)}`).join(" · ") || "—"}</td></> : <><td className="px-2 py-2">{continuous ? fmt(row.target_mean) : Number(row.events || 0).toLocaleString()}</td><td className="px-2 py-2">{continuous ? fmt(row.target_std) : pct(row.event_rate)}</td><td className="px-2 py-2">{continuous ? "—" : fmt(row.woe)}</td><td className="px-2 py-2">{continuous ? "—" : fmt(row.iv)}</td></>}</tr>; })}
  </EvidenceTable>;
}

function IssueDecisionPanel({ row, onDisposition, onPromote, onCloseIssue }) {
  const metrics = row.metrics_json || {};
  const finding = (row.findings || [])[0];
  const issue = finding?.existing_issue;
  const recommendation = promotionRecommendation(metrics);
  const label = recommendation === "recommended" ? "Promotion recommended" : recommendation === "review" ? "Review and consider" : "Promotion not recommended";
  const reasons = metrics.candidate?.candidate_reasons || [];
  return <section className={`rounded-xl border px-3 py-2.5 ${finding ? "border-amber-200 bg-amber-50/60" : "border-slate-200 bg-slate-50"}`}>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2"><div className="min-w-48 flex-1"><strong className="block text-sm text-slate-900">Issue decision · {label}</strong><span className="mt-0.5 block text-xs leading-4 text-slate-600">{reasons.length ? reasons.map(readable).join(" · ") : "No automatic threshold triggered; an SME may override with rationale."}</span></div><FindingStateBadge finding={finding} fallback={<Badge variant="secondary">No finding</Badge>} /><div className="flex flex-wrap items-center gap-2">{issue ? <><IssueLifecycleActions finding={finding} onCloseIssue={onCloseIssue} />{finding.review_state === "open" && issue.status !== "Closed" && !issue.same_finding && <CandidateActions finding={finding} onDisposition={onDisposition} />}</> : finding?.review_state === "open" ? <CandidateActions finding={finding} onDisposition={onDisposition} /> : finding?.review_state === "confirmed" ? null : <FeaturePromotionOverride row={row} recommendation={recommendation} onPromote={onPromote} />}</div></div>
  </section>;
}

function FeatureEvidenceModal({ row, onClose, onDisposition, onPromote, onCloseIssue }) {
  const metrics = row.metrics_json || {};
  const roc = metrics.roc_detail || {};
  const binning = metrics.binning_detail || {};
  const rocRows = primaryTreePerformance(roc.leaves || []);
  const binRows = formatBinRows(binning.coarse_bins || metrics.coarse_bins || []);
  const binary = metrics.target_type === "binary";
  return <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/40 p-4" role="dialog" aria-modal="true" aria-label={`${metrics.feature} target-separation evidence`}><div className="mx-auto max-w-7xl rounded-xl bg-white p-5 shadow-xl">
    <header className="mb-4 grid items-start gap-3 lg:grid-cols-[minmax(15rem,2fr)_minmax(0,3fr)_auto]"><div className="py-1"><p className="text-xs font-medium uppercase tracking-wide text-slate-400">Single-feature target separation</p><h3 className="mt-1 text-lg font-semibold text-slate-950">{metrics.feature}</h3><p className="mt-1 text-xs text-slate-500">Target {metrics.target} · AUC {fmt(metrics.auc)} · Gini {fmt(metrics.gini)} · IV {fmt(metrics.iv)} · {readable(metrics.category)}</p></div><IssueDecisionPanel row={row} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} /><Button size="sm" variant="outline" onClick={onClose}><X className="h-4 w-4" /> Close</Button></header>
    <div className="grid gap-4"><VariableProfile metrics={metrics} />
      {metrics.errors?.map((message) => <div key={message} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900"><AlertTriangle className="mr-1 inline h-4 w-4" />{message}</div>)}
      <div className="grid gap-4 xl:grid-cols-2">{binary ? <RocChart rows={rocRows} auc={metrics.auc} /> : <div className="flex min-h-72 flex-col items-center justify-center rounded-xl border border-slate-200 bg-slate-50 p-6 text-center"><CircleGauge className="mb-2 h-7 w-7 text-indigo-600" /><strong className="text-sm text-slate-800">{metrics.target_type === "multinomial" ? "One-vs-rest separation summary" : "Concordance summary"}</strong><p className="mt-1 max-w-md text-xs text-slate-500">A conventional ROC curve is not shown for a {metrics.target_type} target. The retained configuration metrics are reported in the table below.</p></div>}<BinProfileChart rows={binRows} targetType={metrics.target_type} iv={metrics.iv} /></div>
      <div className="grid gap-4 xl:grid-cols-2"><PerformanceTable metrics={metrics} rows={rocRows} /><BinningTable rows={binRows} targetType={metrics.target_type} /></div>
      {metrics.localized_candidates?.length > 0 && <section className="rounded-xl border border-slate-200 bg-white p-4"><h4 className="text-sm font-semibold text-slate-800">Localized leakage evidence</h4><div className="mt-2 grid gap-2 sm:grid-cols-2">{metrics.localized_candidates.map((candidate, index) => <div key={index} className="rounded-lg border border-rose-100 bg-rose-50/60 p-3 text-xs text-slate-600"><strong className="text-slate-900">{candidate.rule}</strong><p className="mt-1">Target rate {pct(candidate.target_rate)} · baseline {pct(candidate.baseline_rate)} · lift {fmt(candidate.lift, 2)}× · population {pct(candidate.population_share)}</p></div>)}</div></section>}
      <p className="flex items-start gap-1 text-[11px] text-slate-500"><Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />This is a read-only Test Lab projection of governed AAR evidence. Decision-tree analysis, bin editing, and universal bin promotion remain available in RCA after issue promotion. Artifacts: {[metrics.profile_artifact_id, ...Object.values(metrics.artifact_ids || {})].filter(Boolean).join(" · ") || "unavailable"}.</p>
    </div>
  </div></div>;
}

export default function FeatureTargetResults({ results, onDisposition, onPromote, onCloseIssue, workflowFilter = "all", diagnosticFilter = "all", onDiagnosticFilter, workflowSummary, runId }) {
  const summary = results.find((row) => row.metrics_json?.result_kind === "run_summary");
  const features = results.filter((row) => row.metrics_json?.result_kind === "feature");
  const rollup = summary?.metrics_json?.rollup || {};
  const [selectedId, setSelectedId] = useState("");
  const [reportError, setReportError] = useState("");
  const visible = useMemo(() => features.filter((row) =>
    (diagnosticFilter === "all" || row.metrics_json?.category === diagnosticFilter)
    && matchesFindingFilter(row, workflowFilter)), [features, diagnosticFilter, workflowFilter]);
  const openCandidates = features.flatMap((row) => row.findings || []).filter((finding) => findingWorkflowState(finding) === "review_needed");
  const selectedRow = features.find((row) => row.result_id === selectedId);
  return <section className="grid gap-4" data-testid="feature-target-results">
    <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,1fr)]"><header className="rounded-xl border border-slate-200 bg-white p-4">
      <div><p className="text-xs font-medium uppercase text-slate-400">Single-feature target separation</p><h2 className="mt-1 text-lg font-semibold text-slate-950">Target: {summary?.metrics_json?.target?.target}</h2><p className="text-xs text-slate-500">Review AUC, Gini, IV/WOE, and localized leakage evidence in a concise variable-level view · {summary?.metrics_json?.status || "complete"}</p></div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5"><MetricTile icon={BarChart3} label="Assessed" value={rollup.features || 0} detail="All feature outcomes" active={diagnosticFilter === "all" && workflowFilter === "all"} onClick={() => onDiagnosticFilter("all")} /><MetricTile icon={AlertTriangle} label="Suspicious" value={rollup.categories?.suspicious || 0} detail="Highest separation" active={diagnosticFilter === "suspicious"} onClick={() => onDiagnosticFilter("suspicious")} /><MetricTile icon={CircleGauge} label="Strong" value={rollup.categories?.strong || 0} detail="Strong evidence" active={diagnosticFilter === "strong"} onClick={() => onDiagnosticFilter("strong")} /><MetricTile icon={CircleGauge} label="Medium" value={rollup.categories?.medium || 0} detail="Moderate evidence" active={diagnosticFilter === "medium"} onClick={() => onDiagnosticFilter("medium")} /><MetricTile icon={Check} label="Weak" value={rollup.categories?.weak || 0} detail="Limited separation" active={diagnosticFilter === "weak"} onClick={() => onDiagnosticFilter("weak")} /></div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs"><Badge className="border-transparent bg-amber-100 text-amber-900">{rollup.target_leakage || 0} leakage candidate(s)</Badge><Badge variant="secondary"><TrendingDown /> {rollup.poor_discrimination || 0} poor-discrimination candidate(s)</Badge><span className="ml-auto text-slate-500">{openCandidates.length} awaiting review</span></div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3"><Button size="sm" variant="outline" disabled={!runId} onClick={async () => { setReportError(""); try { const blob = await downloadDiagnosticReportV2(runId); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `feature-target-separation-${runId}.pdf`; link.click(); URL.revokeObjectURL(url); } catch (error) { setReportError(error.message); } }}><Download className="h-4 w-4" /> Download analysis report</Button><Button size="sm" variant="outline" disabled={!runId} onClick={() => window.open(diagnosticReportUrlV2(runId, "text"), "_blank")}><FileText className="h-4 w-4" /> View text report</Button>{reportError && <span className="text-xs text-red-600">{reportError}</span>}</div>
    </header>{workflowSummary}</div>
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"><header className="border-b border-slate-200 bg-slate-50 px-4 py-3"><h3 className="text-sm font-semibold text-slate-800">Variable-level results</h3><p className="mt-1 text-xs text-slate-500">Open a variable to review its retained profile, ROC/lift evidence, bin profile, and issue recommendation.</p></header><div className="max-h-[min(65vh,46rem)] overflow-auto"><table className="w-full min-w-[900px] text-left text-sm"><thead className="sticky top-0 z-10 border-b border-slate-200 bg-white text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-4 py-3">Variable</th><th className="px-4 py-3">Population</th><th className="px-4 py-3">AUC</th><th className="px-4 py-3">Gini</th><th className="px-4 py-3">IV</th><th className="px-4 py-3">Category</th><th className="px-4 py-3">Issue workflow</th><th className="px-4 py-3 text-right">Details</th></tr></thead><tbody>
      {visible.map((row) => { const metrics = row.metrics_json || {}; const finding = (row.findings || [])[0]; const specialRows = Object.values(metrics.roc_detail?.special_value_rows || {}).reduce((sum, count) => sum + count, 0); return <tr key={row.result_id} className="border-b border-slate-100 align-middle hover:bg-slate-50/80"><td className="px-4 py-3"><strong className="block text-slate-900">{metrics.feature}</strong><span className="text-xs text-slate-500">{readable(metrics.roc_detail?.feature_type || metrics.binning_detail?.feature_type || "unavailable")}</span></td><td className="px-4 py-3"><strong className="block text-slate-700">{Number(metrics.rows_evaluated || 0).toLocaleString()}</strong><span className="text-xs text-slate-500">{Number(metrics.missing_rows || 0).toLocaleString()} missing · {Number(specialRows).toLocaleString()} special</span></td><td className="px-4 py-3 font-medium">{fmt(metrics.auc)}</td><td className="px-4 py-3 font-medium">{fmt(metrics.gini)}</td><td className="px-4 py-3 font-medium">{fmt(metrics.iv)}</td><td className="px-4 py-3"><Badge variant={metrics.category === "suspicious" ? "warning" : "secondary"}>{metrics.category}</Badge></td><td className="px-4 py-3"><FindingStateBadge finding={finding} fallback={<span className="text-xs text-slate-400">No review required</span>} /></td><td className="px-4 py-3 text-right"><Button size="sm" variant="outline" onClick={() => setSelectedId(row.result_id)}>View evidence <ChevronRight className="h-4 w-4" /></Button></td></tr>; })}
      {!visible.length && <tr><td colSpan="8" className="px-4 py-10 text-center text-sm text-slate-500">No variables match the selected category and workflow status.</td></tr>}
    </tbody></table></div></div>
    {selectedRow && <FeatureEvidenceModal row={selectedRow} onClose={() => setSelectedId("")} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} />}
  </section>;
}
