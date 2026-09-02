import { useState } from "react";
import { AlertTriangle, BarChart3, Boxes, Check, ChevronRight, CircleGauge, Database, Download, Eye, FileText, GitCompareArrows, Info, Target, TrendingUp, X } from "lucide-react";

import { diagnosticReportUrlV2, downloadDiagnosticReportV2 } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatBinLabels, sortPsiBins } from "@/features/test-lab/shared/binning/binLabelDisplay";
import { FindingStateBadge, IssueLifecycleActions } from "@/pages/testlab/FindingWorkflow";
import { findingWorkflowState, matchesFindingFilter } from "@/pages/testlab/findingWorkflowState";

const number = (value, digits = 4) => value == null ? "—" : Number(value).toFixed(digits);
const count = (value) => value == null ? "—" : Number(value).toLocaleString();
const percent = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(2)}%`;

const categoryStyle = {
  investigate: "border-red-200 bg-red-50 text-red-800",
  watch: "border-amber-200 bg-amber-50 text-amber-800",
  stable: "border-emerald-200 bg-emerald-50 text-emerald-800",
};

const profileNumber = (value) => value == null ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 4 });
const readable = (value) => String(value || "—").replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());

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

function ProfileFact({ label, value }) {
  return <div className="rounded-lg border border-slate-200 bg-white px-3 py-2"><span className="block text-[11px] text-slate-500">{label}</span><strong className="mt-0.5 block break-words text-sm text-slate-900">{value}</strong></div>;
}

function PsiFeatureProfile({ metric }) {
  const profile = metric.data_profile;
  if (!profile) return <section className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-2"><span className="text-xs font-semibold text-slate-800">Baseline feature profile</span><span className="ml-2 text-xs text-slate-500">Retained AAR profile unavailable.</span></section>;
  const numeric = ["numeric", "numerical", "integer", "float"].some((value) => String(profile.classification || profile.data_type || "").toLowerCase().includes(value));
  const regular = profile.regular_value_count ?? profile.non_null_count ?? 0;
  const missing = profile.effective_missing_count ?? profile.null_count ?? 0;
  const facts = [
    ["Rows", Number(profile.total_count || 0).toLocaleString()],
    ["Regular values", Number(regular).toLocaleString()],
    ["Missing", Number(missing).toLocaleString()],
    ["Distinct", Number(profile.distinct_count || 0).toLocaleString()],
    ...(numeric ? [["Minimum", profileNumber(profile.min)], ["Maximum", profileNumber(profile.max)], ["Mean", profileNumber(profile.mean)], ["Std. deviation", profileNumber(profile.stddev)]] : []),
  ];
  const percentiles = Object.entries(profile.percentiles || {}).slice(0, 7);
  const topValues = Object.entries(profile.top_k || {}).slice(0, 8);
  return <section className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5">
    <div className="flex flex-wrap items-center gap-2"><h4 className="mr-1 text-xs font-semibold uppercase tracking-wide text-slate-700">Baseline feature profile</h4><Badge variant="outline">{readable(profile.classification || profile.data_type)}</Badge><span className="text-[11px] text-slate-400">AAR · {metric.profile_artifact_id || "retained profile"}</span></div>
    <div className={`mt-2 grid gap-1.5 ${numeric ? "grid-cols-2 sm:grid-cols-4 xl:grid-cols-8" : "grid-cols-2 sm:grid-cols-4"}`}>{facts.map(([label, value]) => <ProfileFact key={label} label={label} value={value} />)}</div>
    {numeric && percentiles.length > 0 && <div className="mt-2 grid grid-cols-[auto_1fr] items-center gap-2"><strong className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Percentiles</strong><div className="grid grid-cols-3 gap-1.5 sm:grid-cols-7">{percentiles.map(([label, value]) => <ProfileFact key={label} label={label} value={profileNumber(value)} />)}</div></div>}
    {!numeric && topValues.length > 0 && <div className="mt-2 grid grid-cols-[auto_1fr] items-center gap-2"><strong className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">Top values</strong><div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4 xl:grid-cols-8">{topValues.map(([label, value]) => <ProfileFact key={label} label={label} value={Number(value).toLocaleString()} />)}</div></div>}
  </section>;
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

function PsiIssueDecision({ row, onDisposition, onPromote, onCloseIssue }) {
  const metric = row.metrics_json || {};
  const finding = (row.findings || [])[0];
  const issue = finding?.existing_issue;
  const recommendation = metric.classification === "investigate" ? "recommended"
    : metric.classification === "watch" ? "review" : "not_recommended";
  const label = recommendation === "recommended" ? "Promotion recommended"
    : recommendation === "review" ? "Review and consider" : "Promotion not recommended";
  return <section className={`rounded-xl border px-3 py-2.5 ${finding ? "border-amber-200 bg-amber-50/60" : "border-slate-200 bg-slate-50"}`}>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2"><div className="min-w-48 flex-1"><strong className="block text-sm text-slate-900">Issue decision · {label}</strong><span className="mt-0.5 block text-xs leading-4 text-slate-600">PSI {number(metric.psi)} is {metric.classification}. This is contextual evidence; an SME makes the promotion decision after reviewing the profile and bin contributions.</span></div><FindingStateBadge finding={finding} fallback={<Badge variant="secondary">No finding</Badge>} /><div className="flex flex-wrap items-center gap-2">{issue ? <IssueLifecycleActions finding={finding} onCloseIssue={onCloseIssue} /> : finding?.review_state === "open" ? <FindingActions finding={finding} onDisposition={onDisposition} /> : finding?.review_state === "confirmed" ? null : <PromotionOverride row={row} recommendation={recommendation} onPromote={onPromote} />}</div></div>
  </section>;
}

function PsiEvidenceModal({ row, onClose, onDisposition, onPromote, onCloseIssue }) {
  const metric = row.metrics_json || {};
  return <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/40 p-4" role="dialog" aria-modal="true" aria-label={`${metric.feature} population stability evidence`}><div className="mx-auto max-w-7xl rounded-xl bg-white p-5 shadow-xl">
    <header className="mb-4 grid items-start gap-3 lg:grid-cols-[minmax(15rem,2fr)_minmax(0,3fr)_auto]"><div className="py-1"><p className="text-xs font-medium uppercase tracking-wide text-slate-400">Population Stability Index</p><h3 className="mt-1 text-lg font-semibold text-slate-950">{metric.feature}</h3><p className="mt-1 text-xs text-slate-500">PSI {number(metric.psi)} · {readable(metric.classification)} · {count(metric.baseline_count)} Baseline · {count(metric.current_count)} Current</p></div><PsiIssueDecision row={row} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} /><Button size="sm" variant="outline" onClick={onClose}><X className="h-4 w-4" /> Close</Button></header>
    <div className="grid gap-4">
      <PsiFeatureProfile metric={metric} />
      <section className="grid gap-2 sm:grid-cols-3"><ProfileFact label="Watch threshold" value={number(metric.thresholds?.watch, 2)} /><ProfileFact label="Investigate threshold" value={number(metric.thresholds?.investigate, 2)} /><ProfileFact label="Frozen-bin artifact" value={metric.bin_artifact_id || "—"} /></section>
      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white"><header className="border-b border-slate-200 bg-slate-50 px-4 py-3"><h4 className="text-sm font-semibold text-slate-800">Baseline-to-Current bin contributions</h4><p className="mt-1 text-xs text-slate-500">Each contribution reconciles to the feature-level PSI above.</p></header><div className="max-h-[32rem] overflow-auto"><PsiPopulationProfile metric={metric} /></div></section>
      <p className="flex items-start gap-1 text-[11px] text-slate-500"><Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />This is a read-only Test Lab projection of governed AAR evidence. The feature profile describes the retained Baseline snapshot; the bin table compares Baseline with Current. Artifacts: {metric.profile_artifact_id || "profile unavailable"} · {metric.bin_artifact_id || "bins unavailable"} · {metric.artifact_id || "PSI unavailable"}.</p>
    </div>
  </div></div>;
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

export default function PopulationStabilityResults({ results, onDisposition, onPromote, onCloseIssue, workflowFilter = "all", diagnosticFilter = "all", onDiagnosticFilter, workflowSummary, runId }) {
  const features = results.filter((row) => row.metrics_json?.result_kind === "psi_feature");
  const summary = results.find((row) => row.metrics_json?.result_kind === "psi_run_summary");
  const [selectedId, setSelectedId] = useState("");
  const [reportError, setReportError] = useState("");
  const classifications = features.reduce((totals, row) => {
    const key = row.metrics_json?.classification;
    if (key) totals[key] = (totals[key] || 0) + 1;
    return totals;
  }, {});
  const failed = summary?.metrics_json?.rollup?.failed || 0;
  const visible = features.filter((row) =>
    (diagnosticFilter === "all" || row.metrics_json?.classification === diagnosticFilter)
    && matchesFindingFilter(row, workflowFilter));
  const selectedRow = features.find((row) => row.result_id === selectedId);
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
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3"><Button size="sm" variant="outline" disabled={!runId} onClick={async () => { setReportError(""); try { const blob = await downloadDiagnosticReportV2(runId); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `population-stability-${runId}.pdf`; link.click(); URL.revokeObjectURL(url); } catch (error) { setReportError(error.message); } }}><Download className="h-4 w-4" /> Download analysis report</Button><Button size="sm" variant="outline" disabled={!runId} onClick={() => window.open(diagnosticReportUrlV2(runId, "text"), "_blank")}><FileText className="h-4 w-4" /> View text report</Button>{reportError && <span className="text-xs text-red-600">{reportError}</span>}</div>
      {errors.length > 0 && <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
        <strong>{errors.length} feature evaluation(s) failed.</strong> {errors.map((entry) => `${entry.feature}: ${entry.error}`).join(" · ")}
      </div>}
    </div>
    {workflowSummary}
    </div>

    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white"><div className="overflow-x-auto">
      <table className="w-full text-sm"><thead className="bg-slate-50 text-left text-xs uppercase text-slate-400"><tr>
        <th className="px-4 py-2">Feature</th><th className="px-3 py-2">Population</th><th className="px-3 py-2">PSI</th>
        <th className="px-3 py-2">Bins</th><th className="px-3 py-2">Classification</th><th className="px-3 py-2">Review</th><th className="px-3 py-2 text-right">Details</th>
      </tr></thead><tbody>
        {visible.map((row) => {
          const metric = row.metrics_json || {};
          const finding = (row.findings || [])[0];
          const recommendation = metric.classification === "investigate" ? "recommended"
            : metric.classification === "watch" ? "review" : "not_recommended";
          const recommendationLabel = recommendation === "recommended" ? "promotion recommended"
            : recommendation === "review" ? "review and consider" : "promotion not recommended";
          return <tr key={row.result_id} className="border-t border-slate-100 align-middle hover:bg-slate-50/80">
            <td className="px-4 py-3"><strong className="text-slate-900">{metric.feature}</strong><small className="mt-1 block text-slate-500">Frozen governed bins</small></td>
            <td className="px-3 py-3 text-slate-600"><strong className="block text-slate-700">{count((metric.baseline_count || 0) + (metric.current_count || 0))}</strong><small className="block text-[11px]">{count(metric.baseline_count)} baseline · {count(metric.current_count)} current</small></td>
            <td className="px-3 py-3 font-medium text-slate-800">{number(metric.psi)}</td>
            <td className="px-3 py-3 text-slate-600">{metric.bins?.length || 0}</td>
            <td className="px-3 py-3"><ClassificationBadge value={metric.classification} /></td>
            <td className="px-3 py-3"><FindingStateBadge finding={finding} fallback={<Badge className={recommendation === "recommended" ? "border-transparent bg-red-100 text-red-800" : recommendation === "review" ? "border-transparent bg-amber-100 text-amber-800" : "border-transparent bg-slate-100 text-slate-600"}>{recommendationLabel}</Badge>} /></td>
            <td className="px-2 py-3 text-right"><Button size="sm" variant="outline" onClick={() => setSelectedId(row.result_id)}>View evidence <ChevronRight className="h-4 w-4" /></Button></td>
          </tr>;
        })}
        {!visible.length && <tr><td colSpan="7" className="p-6 text-center text-sm text-slate-500">No features match the selected classification and workflow status.</td></tr>}
      </tbody></table>
    </div></div>
    {selectedRow && <PsiEvidenceModal row={selectedRow} onClose={() => setSelectedId("")} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} />}
  </section>;
}
