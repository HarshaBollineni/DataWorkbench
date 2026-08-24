import { Fragment, useState } from "react";
import { AlertTriangle, BarChart3, Boxes, Check, ChevronRight, CircleGauge, Database, Eye, GitCompareArrows, Target, TrendingUp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatBinLabels, sortPsiBins } from "./binLabelDisplay";
import { FindingStateBadge, IssueLifecycleActions } from "./FindingWorkflow";
import { findingWorkflowState, matchesFindingFilter } from "./findingWorkflowState";

const number = (value, digits = 4) => value == null ? "—" : Number(value).toFixed(digits);
const count = (value) => value == null ? "—" : Number(value).toLocaleString();
const percent = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(2)}%`;

const categoryStyle = {
  investigate: "border-red-200 bg-red-50 text-red-800",
  watch: "border-amber-200 bg-amber-50 text-amber-800",
  stable: "border-emerald-200 bg-emerald-50 text-emerald-800",
};

function MetricTile({ icon: Icon, label, value, detail, active, onClick }) {
  return <button type="button" onClick={onClick}
    className={`min-h-24 border p-3 text-left ${active ? "border-dq-purple bg-dq-purple/5" : "border-slate-200 bg-white"}`}>
    <span className="flex items-center gap-2 text-xs font-medium text-slate-500"><Icon /> {label}</span>
    <strong className="mt-2 block text-2xl text-slate-950">{value}</strong>
    <small className="text-[11px] text-slate-500">{detail}</small>
  </button>;
}

function ClassificationBadge({ value }) {
  return <Badge variant="outline" className={categoryStyle[value] || ""}>{value || "unavailable"}</Badge>;
}

function FindingActions({ finding, onDisposition }) {
  const [busy, setBusy] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  if (!finding) return null;
  if (finding.review_state !== "open") return <Badge variant={finding.review_state === "confirmed" ? "success" : "secondary"}>{finding.review_state}</Badge>;
  const act = async (action, rationale) => {
    setBusy(true); setError("");
    try { await onDisposition(finding.finding_id, action, rationale); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };
  return <div className="flex flex-wrap items-center gap-2">
    {!promoting ? <Button size="sm" variant="success" disabled={busy} onClick={() => setPromoting(true)}>Promote to issue…</Button>
      : <><input value={reason} autoFocus placeholder="Promotion rationale (required)" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-64 rounded-md border border-slate-200 px-2 text-xs" /><Button size="sm" variant="success" disabled={busy || !reason.trim()} onClick={() => act("confirm_issue", reason.trim())}>Confirm promotion</Button></>}
    {!dismissing ? <Button size="sm" variant="outline" disabled={busy} onClick={() => setDismissing(true)}>Dismiss</Button>
      : <><input value={reason} autoFocus placeholder="Reason required" onChange={(event) => setReason(event.target.value)}
        className="h-8 min-w-44 rounded-md border border-slate-200 px-2 text-xs" />
      <Button size="sm" variant="outline" disabled={busy || !reason.trim()} onClick={() => act("dismiss", reason.trim())}>Confirm dismissal</Button></>}
    {error && <p className="w-full text-xs text-red-600">{error}</p>}
  </div>;
}

function PromotionOverride({ row, recommendation, onPromote }) {
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
  return <div className="mt-3">
    {!editing && <Button size="sm" variant={recommendation === "recommended" ? "success" : "outline"}
      onClick={() => setEditing(true)}>{recommendation === "recommended" ? "Promote to issue…" : "Override and promote…"}</Button>}
    {editing && <div className="flex flex-wrap gap-2"><input autoFocus value={reason} placeholder="Promotion rationale (required)" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-64 flex-1 rounded-md border border-slate-200 bg-white px-2 text-xs" /><Button size="sm" disabled={busy || !reason.trim()} onClick={promote}>{busy ? "Promoting…" : "Confirm promotion"}</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing(false)}>Cancel</Button></div>}
    {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
  </div>;
}

function PopulationBar({ baseline, current }) {
  const maximum = Math.max(Number(baseline) || 0, Number(current) || 0, 0.000001);
  return <div className="grid min-w-36 gap-1" aria-label={`Baseline ${percent(baseline)}, Current ${percent(current)}`}>
    <div className="flex items-center gap-2"><span className="w-4 text-[10px] text-slate-400">B</span><div className="h-1.5 flex-1 rounded bg-slate-100"><div className="h-full rounded bg-teal-600" style={{ width: `${(Number(baseline) / maximum) * 100}%` }} /></div></div>
    <div className="flex items-center gap-2"><span className="w-4 text-[10px] text-slate-400">C</span><div className="h-1.5 flex-1 rounded bg-slate-100"><div className="h-full rounded bg-orange-400" style={{ width: `${(Number(current) / maximum) * 100}%` }} /></div></div>
  </div>;
}

export function PsiPopulationProfile({ metric }) {
  const orderedBins = sortPsiBins(metric?.bins || []);
  const binLabels = formatBinLabels(orderedBins.map((bin) => bin.bin_label || bin.bin));
  if (!orderedBins.length) return <p className="text-xs text-slate-500">No retained PSI bin profile is available.</p>;
  return <div className="overflow-hidden rounded border border-slate-200 bg-white"><table className="w-full text-xs"><thead className="bg-slate-50 text-left uppercase text-slate-400"><tr><th className="p-2">Bin</th><th className="p-2 text-right">Baseline</th><th className="p-2 text-right">Current</th><th className="p-2">Population profile</th><th className="p-2 text-right">Contribution</th></tr></thead><tbody>
    {orderedBins.map((bin, index) => <tr key={bin.bin} className="border-t border-slate-100"><td className="max-w-72 p-2"><strong className="block font-medium text-slate-800">{binLabels[index]}</strong>{bin.bin_label && bin.bin_label !== bin.bin && <small className="text-[10px] text-slate-400">{bin.bin}</small>}</td><td className="p-2 text-right"><strong>{count(bin.baseline_count)}</strong><small className="ml-1 text-slate-400">{percent(bin.baseline_proportion)}</small></td><td className="p-2 text-right"><strong>{count(bin.current_count)}</strong><small className="ml-1 text-slate-400">{percent(bin.current_proportion)}</small></td><td className="p-2"><PopulationBar baseline={bin.baseline_proportion} current={bin.current_proportion} /></td><td className="p-2 text-right font-medium">{number(bin.contribution, 6)}</td></tr>)}
  </tbody></table></div>;
}

export function PsiJourneySummary({ manifest }) {
  if (!manifest) return null;
  const sameSnapshot = manifest.baseline?.snapshot?.snapshot_id === manifest.current?.snapshot?.snapshot_id;
  const definition = manifest.population_definition || {};
  const expression = definition.expression || {};
  const splitDetail = definition.method === "split_snapshot"
    ? `${definition.split_feature} ${expression.operator || ""} ${expression.value ?? ""}`.trim()
    : "Baseline snapshot → Current snapshot";
  const targetMode = manifest.target_choice?.mode;
  const targetDetail = targetMode === "saved_target"
    ? manifest.target_choice?.saved_target || manifest.governed_context?.target?.column || "Saved target"
    : "No target dependency";
  const frozen = Object.values(manifest.frozen_bins || {});
  const reused = frozen.filter((reference) => /universal|reuse/i.test(reference.reuse_reason || "")).length;
  const generated = frozen.length - reused;
  const binningDetail = `${reused} universal reused · ${generated} generated`;
  const steps = [
    { icon: Database, label: "Data", value: sameSnapshot ? "Single snapshot" : "Two snapshots", detail: manifest.table },
    { icon: GitCompareArrows, label: "Population", value: definition.method === "split_snapshot" ? "Split population" : "Compare snapshots", detail: splitDetail },
    { icon: Target, label: "Target", value: targetMode === "saved_target" ? "Target-aware" : "Target-free", detail: targetDetail },
    { icon: Boxes, label: "Binning", value: "Automatic route", detail: binningDetail },
  ];
  return <section className="rounded-lg border border-teal-200 bg-white px-4 py-3" data-testid="psi-journey-summary">
    <div className="mb-2 flex items-center justify-between gap-3"><p className="text-xs font-semibold uppercase tracking-wide text-teal-700">PSI journey used</p><span className="text-[11px] text-slate-400">Frozen run configuration</span></div>
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{steps.map(({ icon: Icon, label, value, detail }) => <div key={label} className="flex min-w-0 items-start gap-2 rounded-md bg-slate-50 px-3 py-2">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-teal-700" /><span className="min-w-0"><small className="block text-[10px] font-semibold uppercase text-slate-400">{label}</small><strong className="block truncate text-xs text-slate-800">{value}</strong><span className="block truncate text-[11px] text-slate-500" title={detail}>{detail || "—"}</span></span>
    </div>)}</div>
  </section>;
}

export default function PopulationStabilityResults({ results, onDisposition, onPromote, onCloseIssue, workflowFilter = "all", diagnosticFilter = "all", onDiagnosticFilter, workflowSummary }) {
  const features = results.filter((row) => row.metrics_json?.result_kind === "psi_feature");
  const summary = results.find((row) => row.metrics_json?.result_kind === "psi_run_summary");
  const [expanded, setExpanded] = useState("");
  const classifications = features.reduce((totals, row) => {
    const key = row.metrics_json?.classification;
    if (key) totals[key] = (totals[key] || 0) + 1;
    return totals;
  }, {});
  const failed = summary?.metrics_json?.rollup?.failed || 0;
  const visible = features.filter((row) =>
    (diagnosticFilter === "all" || row.metrics_json?.classification === diagnosticFilter)
    && matchesFindingFilter(row, workflowFilter));
  const openFindings = features.flatMap((row) => row.findings || []).filter((finding) => findingWorkflowState(finding) === "review_needed");
  const errors = summary?.metrics_json?.feature_errors || [];

  return <section className="grid gap-4" data-testid="psi-results">
    <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,1fr)]">
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div><p className="text-xs font-medium uppercase text-slate-400">Population Stability Index</p>
        <h2 className="mt-1 text-lg font-semibold text-slate-950">Baseline versus Current population</h2>
        <p className="text-xs text-slate-500">Frozen-bin PSI, population movement, and contextual drift evidence · {summary?.metrics_json?.status || "complete"}</p></div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        <MetricTile icon={BarChart3} label="Assessed" value={features.length} detail="All feature outcomes" active={diagnosticFilter === "all" && workflowFilter === "all"} onClick={() => onDiagnosticFilter("all")} />
        <MetricTile icon={AlertTriangle} label="Investigate" value={classifications.investigate || 0} detail="PSI at investigate threshold" active={diagnosticFilter === "investigate"} onClick={() => onDiagnosticFilter("investigate")} />
        <MetricTile icon={CircleGauge} label="Watch" value={classifications.watch || 0} detail="PSI at watch threshold" active={diagnosticFilter === "watch"} onClick={() => onDiagnosticFilter("watch")} />
        <MetricTile icon={Check} label="Stable" value={classifications.stable || 0} detail="Below watch threshold" active={diagnosticFilter === "stable"} onClick={() => onDiagnosticFilter("stable")} />
        <MetricTile icon={AlertTriangle} label="Failed" value={failed} detail="Could not be evaluated" active={false} onClick={() => onDiagnosticFilter("all")} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <Badge className="border-transparent bg-indigo-100 text-indigo-800"><Eye /> Contextual review — no automatic pass/fail</Badge>
        <Badge variant="secondary"><TrendingUp /> {(classifications.investigate || 0) + (classifications.watch || 0)} drift candidate(s)</Badge>
        <span className="ml-auto text-slate-500">{openFindings.length} awaiting review</span>
      </div>
      {errors.length > 0 && <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
        <strong>{errors.length} feature evaluation(s) failed.</strong> {errors.map((entry) => `${entry.feature}: ${entry.error}`).join(" · ")}
      </div>}
    </div>
    {workflowSummary}
    </div>

    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white"><div className="overflow-x-auto">
      <table className="w-full text-sm"><thead className="bg-slate-50 text-left text-xs uppercase text-slate-400"><tr>
        <th className="px-4 py-2">Feature</th><th className="px-3 py-2">Population</th><th className="px-3 py-2">PSI</th>
        <th className="px-3 py-2">Bins</th><th className="px-3 py-2">Classification</th><th className="px-3 py-2">Review</th><th className="w-10" />
      </tr></thead><tbody>
        {visible.map((row) => {
          const metric = row.metrics_json || {};
          const finding = (row.findings || [])[0];
          const isExpanded = expanded === row.result_id;
          const recommendation = metric.classification === "investigate" ? "recommended"
            : metric.classification === "watch" ? "review" : "not_recommended";
          const recommendationLabel = recommendation === "recommended" ? "promotion recommended"
            : recommendation === "review" ? "review and consider" : "promotion not recommended";
          return <Fragment key={row.result_id}><tr className={`border-t border-slate-100 align-top ${isExpanded ? "bg-emerald-50/30" : ""}`}>
            <td className="px-4 py-3"><strong className="text-slate-900">{metric.feature}</strong><small className="mt-1 block text-slate-500">Frozen governed bins</small></td>
            <td className="px-3 py-3 text-slate-600"><strong className="block text-slate-700">{count((metric.baseline_count || 0) + (metric.current_count || 0))}</strong><small className="block text-[11px]">{count(metric.baseline_count)} baseline · {count(metric.current_count)} current</small></td>
            <td className="px-3 py-3 font-medium text-slate-800">{number(metric.psi)}</td>
            <td className="px-3 py-3 text-slate-600">{metric.bins?.length || 0}</td>
            <td className="px-3 py-3"><ClassificationBadge value={metric.classification} /></td>
            <td className="px-3 py-3"><FindingStateBadge finding={finding} fallback={<Badge className={recommendation === "recommended" ? "border-transparent bg-red-100 text-red-800" : recommendation === "review" ? "border-transparent bg-amber-100 text-amber-800" : "border-transparent bg-slate-100 text-slate-600"}>{recommendationLabel}</Badge>} /></td>
            <td className="px-2 py-3"><button type="button" aria-label={`${isExpanded ? "Collapse" : "Expand"} ${metric.feature} details`} onClick={() => setExpanded(isExpanded ? "" : row.result_id)}><ChevronRight className={`transition-transform ${isExpanded ? "rotate-90" : ""}`} /></button></td>
          </tr>{isExpanded && <tr className="border-t border-slate-100 bg-slate-50/50"><td colSpan="7" className="p-4">
            <div className="grid gap-4">
              <div className="grid gap-2 sm:grid-cols-3"><div className="rounded border border-slate-200 bg-white p-3 text-xs"><span className="text-slate-500">Watch threshold</span><strong className="mt-1 block">{number(metric.thresholds?.watch, 2)}</strong></div><div className="rounded border border-slate-200 bg-white p-3 text-xs"><span className="text-slate-500">Investigate threshold</span><strong className="mt-1 block">{number(metric.thresholds?.investigate, 2)}</strong></div><div className="rounded border border-slate-200 bg-white p-3 text-xs"><span className="text-slate-500">Frozen-bin artifact</span><strong className="mt-1 block break-all">{metric.bin_artifact_id || "—"}</strong></div></div>
              <PsiPopulationProfile metric={metric} />
              <div className={`rounded-md border p-3 ${recommendation === "not_recommended" ? "border-slate-200 bg-slate-50" : "border-indigo-200 bg-indigo-50/40"}`}><p className="text-sm font-medium text-slate-900">Product recommendation: {recommendationLabel}.</p><p className="mt-1 text-xs text-slate-600">PSI {number(metric.psi)} is classified as {metric.classification}. The recommendation guides the decision; an SME may override it after reviewing bin contributions.</p><div className="mt-3"><FindingStateBadge finding={finding} /></div>{finding?.existing_issue ? <IssueLifecycleActions finding={finding} onCloseIssue={onCloseIssue} /> : finding?.review_state === "open" ? <div className="mt-3"><FindingActions finding={finding} onDisposition={onDisposition} /></div> : finding?.review_state === "confirmed" ? null : <PromotionOverride row={row} recommendation={recommendation} onPromote={onPromote} />}</div>
              <p className="text-[11px] text-slate-400">PSI artifact {metric.artifact_id || "—"} · {metric.reuse_outcome || "persisted"} · methodology {metric.methodology || summary?.metrics_json?.methodology || "—"}</p>
            </div>
          </td></tr>}</Fragment>;
        })}
        {!visible.length && <tr><td colSpan="7" className="p-6 text-center text-sm text-slate-500">No features match the selected classification and workflow status.</td></tr>}
      </tbody></table>
    </div></div>
  </section>;
}
