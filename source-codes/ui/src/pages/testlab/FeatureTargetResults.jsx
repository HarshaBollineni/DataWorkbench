import { Fragment, useMemo, useState } from "react";
import { AlertTriangle, BarChart3, Check, ChevronRight, CircleGauge, ExternalLink, TrendingDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { reviewDiagnosticBinningV2 } from "@/api/client";
import FeatureTargetEvidence from "./FeatureTargetEvidence";
import { FindingStateBadge, IssueLifecycleActions } from "./FindingWorkflow";
import { findingWorkflowState, matchesFindingFilter } from "./findingWorkflowState";

const fmt = (value) => value == null ? "—" : Number(value).toFixed(4);

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
  return <div className="mt-3">
    {!editing && <Button size="sm" variant={recommendation === "recommended" ? "success" : "outline"} onClick={() => setEditing(true)}>{recommendation === "recommended" ? "Promote to issue…" : "Override and promote…"}</Button>}
    {editing && <div className="flex flex-wrap gap-2"><input autoFocus value={reason} placeholder="Promotion rationale (required)" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-64 flex-1 rounded-md border border-slate-200 bg-white px-2 text-xs" /><Button size="sm" disabled={busy || !reason.trim()} onClick={promote}>{busy ? "Promoting…" : "Confirm promotion"}</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing(false)}>Cancel</Button></div>}
    {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
  </div>;
}

function MetricTile({ icon: Icon, label, value, detail, active, onClick }) {
  return <button type="button" onClick={onClick} className={`min-h-24 border p-3 text-left ${active ? "border-dq-purple bg-dq-purple/5" : "border-slate-200 bg-white"}`}><span className="flex items-center gap-2 text-xs font-medium text-slate-500"><Icon /> {label}</span><strong className="mt-2 block text-2xl text-slate-950">{value}</strong><small className="text-[11px] text-slate-500">{detail}</small></button>;
}

export default function FeatureTargetResults({ results, onDisposition, onPromote, onCloseIssue, workflowFilter = "all", diagnosticFilter = "all", onDiagnosticFilter, workflowSummary }) {
  const summary = results.find((row) => row.metrics_json?.result_kind === "run_summary");
  const features = results.filter((row) => row.metrics_json?.result_kind === "feature");
  const rollup = summary?.metrics_json?.rollup || {};
  const [expanded, setExpanded] = useState("");
  const [binningOverrides, setBinningOverrides] = useState({});
  const [universalBusy, setUniversalBusy] = useState(false);
  const [universalError, setUniversalError] = useState("");
  const [universalExclusions, setUniversalExclusions] = useState([]);
  const visible = useMemo(() => features.filter((row) =>
    (diagnosticFilter === "all" || row.metrics_json?.category === diagnosticFilter)
    && matchesFindingFilter(row, workflowFilter)), [features, diagnosticFilter, workflowFilter]);
  const openCandidates = features.flatMap((row) => row.findings || []).filter((finding) => findingWorkflowState(finding) === "review_needed");
  const promotable = features.flatMap((row) => {
    const original = row.metrics_json || {};
    const override = binningOverrides[row.result_id];
    const metric = override ? { ...original, binning_detail: override.binning,
      binning_governance: override.governance } : original;
    return metric.binning_detail && !metric.binning_governance?.universal
      ? [{ row, metric }] : [];
  });
  const selectedPromotable = promotable.filter(({ row }) => !universalExclusions.includes(row.result_id));
  const promoteRunBins = async () => {
    setUniversalBusy(true); setUniversalError("");
    const promoted = {};
    try {
      for (const { row, metric } of selectedPromotable) {
        const reviewed = await reviewDiagnosticBinningV2(row.result_id, {
          definition: metric.binning_detail.coarse_definition,
          scope: "universal",
          confirm_universal: true,
        });
        promoted[row.result_id] = {
          binning: { ...metric.binning_detail, coarse_definition: reviewed.definition,
            coarse_bins: reviewed.bins, coarse_metrics: reviewed.metrics,
            warnings: reviewed.warnings, coarse_groups: reviewed.coarse_groups },
          governance: reviewed.governance,
        };
      }
      setBinningOverrides((current) => ({ ...current, ...promoted }));
    } catch (requestError) {
      if (Object.keys(promoted).length) {
        setBinningOverrides((current) => ({ ...current, ...promoted }));
      }
      setUniversalError(`${requestError.message}. ${Object.keys(promoted).length} definition(s) were promoted before the failure.`);
    } finally { setUniversalBusy(false); }
  };
  return <section className="grid gap-4" data-testid="feature-target-results">
    <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,1fr)]">
    <div className="rounded-lg border border-slate-200 bg-white p-4"><div><p className="text-xs font-medium uppercase text-slate-400">Single-feature target separation</p><h2 className="mt-1 text-lg font-semibold text-slate-950">Target: {summary?.metrics_json?.target?.target}</h2><p className="text-xs text-slate-500">AUC, Gini, supervised IV/WOE, and localized leakage evidence · {summary?.metrics_json?.status || "complete"}</p></div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5"><MetricTile icon={BarChart3} label="Assessed" value={rollup.features || 0} detail="All feature outcomes" active={diagnosticFilter === "all" && workflowFilter === "all"} onClick={() => onDiagnosticFilter("all")} /><MetricTile icon={AlertTriangle} label="Suspicious" value={rollup.categories?.suspicious || 0} detail="Highest separation" active={diagnosticFilter === "suspicious"} onClick={() => onDiagnosticFilter("suspicious")} /><MetricTile icon={CircleGauge} label="Strong" value={rollup.categories?.strong || 0} detail="Strong evidence" active={diagnosticFilter === "strong"} onClick={() => onDiagnosticFilter("strong")} /><MetricTile icon={CircleGauge} label="Medium" value={rollup.categories?.medium || 0} detail="Moderate evidence" active={diagnosticFilter === "medium"} onClick={() => onDiagnosticFilter("medium")} /><MetricTile icon={Check} label="Weak" value={rollup.categories?.weak || 0} detail="Limited separation" active={diagnosticFilter === "weak"} onClick={() => onDiagnosticFilter("weak")} /></div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs"><Badge className="border-transparent bg-amber-100 text-amber-900">{rollup.target_leakage || 0} leakage candidate(s)</Badge><Badge variant="secondary"><TrendingDown /> {rollup.poor_discrimination || 0} poor-discrimination candidate(s)</Badge><span className="ml-auto text-slate-500">{openCandidates.length} awaiting review</span></div>
    </div>
    {workflowSummary}
    </div>
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
        <div><h3 className="text-sm font-semibold text-slate-900">Variable-level results</h3><p className="mt-1 text-xs text-slate-500">Select the completed IV definitions to promote for compatible Diagnostic 2 and target-aware two-snapshot PSI reuse.</p></div>
        {!!promotable.length && <div className="flex flex-wrap items-center gap-2" data-testid="iv-batch-promotion-controls"><label className="flex h-8 cursor-pointer items-center gap-2 rounded border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700"><input type="checkbox" checked={selectedPromotable.length === promotable.length} disabled={universalBusy} onChange={() => setUniversalExclusions(selectedPromotable.length === promotable.length ? promotable.map(({ row }) => row.result_id) : [])} />Select eligible bins ({promotable.length})</label><Button size="sm" disabled={universalBusy || !selectedPromotable.length} onClick={promoteRunBins}>{universalBusy ? "Making universal…" : `Make universal (${selectedPromotable.length})`}</Button><span className="text-[11px] text-slate-500">{promotable.length} eligible · {selectedPromotable.length} selected</span></div>}
        {universalError && <p className="w-full text-xs font-medium text-red-700"><AlertTriangle className="mr-1 inline h-4 w-4" />{universalError}</p>}
      </header>
      <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="bg-slate-50 text-left text-xs uppercase text-slate-400"><tr><th className="w-10 px-3 py-2" aria-label="Universal selection" /><th className="px-4 py-2">Feature</th><th className="px-3 py-2">Population</th><th className="px-3 py-2">AUC</th><th className="px-3 py-2">Gini</th><th className="px-3 py-2">IV</th><th className="px-3 py-2">Category</th><th className="px-3 py-2">Review</th><th className="w-10" /></tr></thead><tbody>
      {visible.map((row) => {
        const original = row.metrics_json || {};
        const override = binningOverrides[row.result_id];
        const revisedIv = override?.binning?.coarse_metrics?.find((entry) => ["information_value", "maximum_one_vs_rest_iv"].includes(entry.name))?.value;
        const metric = override ? { ...original, binning_detail: override.binning, binning_governance: override.governance, iv: revisedIv ?? original.iv } : original;
        const roc = metric.roc_detail, binning = metric.binning_detail;
        const specialRows = Object.values(roc?.special_value_rows || {}).reduce((sum, count) => sum + count, 0);
        const uniqueValues = binning?.binning_profile?.unique_regular_values ?? roc?.optimization_profile?.regular_unique_values;
        const finding = (row.findings || [])[0], isExpanded = expanded === row.result_id;
        const candidateType = metric.candidate?.candidate_type;
        const recommendation = candidateType === "target_leakage" ? "recommended"
          : candidateType ? "review" : "not_recommended";
        const recommendationLabel = recommendation === "recommended" ? "promote recommended"
          : recommendation === "review" ? "review and consider" : "promotion not recommended";
        return <Fragment key={row.result_id}><tr className={`border-t border-slate-100 align-top ${isExpanded ? "bg-emerald-50/30" : ""}`}>
          <td className="px-3 py-3">{binning && !metric.binning_governance?.universal ? <input type="checkbox" aria-label={`Select ${metric.feature} to make universal`} checked={!universalExclusions.includes(row.result_id)} disabled={universalBusy} onChange={() => setUniversalExclusions((current) => current.includes(row.result_id) ? current.filter((id) => id !== row.result_id) : [...current, row.result_id])} /> : metric.binning_governance?.universal ? <Check className="h-4 w-4 text-emerald-600" aria-label="Universal" /> : null}</td>
          <td className="px-4 py-3"><strong className="text-slate-900">{metric.feature}</strong><small className="mt-1 block text-slate-500">{roc?.feature_type || binning?.feature_type || "unavailable"}</small>{metric.errors?.length > 0 && <small className="mt-1 block font-medium text-amber-700">Partially available</small>}</td>
          <td className="px-3 py-3 text-slate-600"><strong className="block text-slate-700">{metric.rows_evaluated?.toLocaleString?.() || "—"}</strong><small className="block whitespace-normal text-[11px]">{roc ? `${roc.generic_missing_rows.toLocaleString()} missing · ${specialRows.toLocaleString()} special` : "Population from IV evidence"}{uniqueValues != null ? ` · ${Number(uniqueValues).toLocaleString()} unique` : ""}</small></td>
          <td className="px-3 py-3 font-medium text-slate-800">{fmt(metric.auc)}</td><td className="px-3 py-3 font-medium text-slate-800">{fmt(metric.gini)}</td><td className="px-3 py-3 font-medium text-slate-800">{fmt(metric.iv)}</td><td className="px-3 py-3"><Badge variant={metric.category === "suspicious" ? "warning" : "secondary"}>{metric.category}</Badge></td><td className="px-3 py-3"><FindingStateBadge finding={finding} fallback={<Badge className={recommendation === "recommended" ? "border-transparent bg-red-100 text-red-800" : recommendation === "review" ? "border-transparent bg-amber-100 text-amber-800" : "border-transparent bg-slate-100 text-slate-600"}>{recommendationLabel}</Badge>} /></td><td className="px-2 py-3"><button type="button" aria-label={`${isExpanded ? "Collapse" : "Expand"} ${metric.feature} details`} onClick={() => setExpanded(isExpanded ? "" : row.result_id)}><ChevronRight className={`transition-transform ${isExpanded ? "rotate-90" : ""}`} /></button></td>
        </tr>{isExpanded && <tr className="border-t border-slate-100 bg-slate-50/50"><td colSpan="9" className="p-4"><div className="grid gap-4">
          {metric.errors?.map((message) => <div key={message} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">{message}</div>)}
          <div className={`rounded-md border p-3 ${recommendation === "not_recommended" ? "border-slate-200 bg-slate-50" : "border-indigo-200 bg-indigo-50/40"}`}><p className="text-sm font-medium text-slate-900">Product recommendation: {recommendationLabel}.</p><p className="mt-1 text-xs text-slate-600">{metric.candidate?.candidate_reasons?.join(", ") || "The automated assessment did not identify this feature as a review candidate. An SME may still override the recommendation with a rationale."}</p><div className="mt-3"><FindingStateBadge finding={finding} /></div>{finding?.existing_issue ? <><IssueLifecycleActions finding={finding} onCloseIssue={onCloseIssue} />{finding.review_state === "open" && finding.existing_issue.status !== "Closed" && !finding.existing_issue.same_finding && <div className="mt-3"><CandidateActions finding={finding} onDisposition={onDisposition} /></div>}</> : finding?.review_state === "open" ? <div className="mt-3"><CandidateActions finding={finding} onDisposition={onDisposition} /></div> : finding?.review_state === "confirmed" ? null : <FeaturePromotionOverride row={row} recommendation={recommendation} onPromote={onPromote} />}</div>
          <FeatureTargetEvidence row={{ ...row, metrics_json: metric }} onChanged={(nextBinning, governance) => setBinningOverrides((current) => ({ ...current, [row.result_id]: { binning: nextBinning, governance } }))} />
          {metric.localized_candidates?.length > 0 && <div><h4 className="mb-2 text-xs font-semibold uppercase text-slate-500">Localized leakage evidence</h4>{metric.localized_candidates.map((candidate, index) => <div key={index} className="rounded-md border border-slate-200 bg-white p-3 text-xs text-slate-600"><strong className="text-slate-800">{candidate.rule}</strong><p className="mt-1">Target rate {fmt(candidate.target_rate)} · baseline {fmt(candidate.baseline_rate)} · lift {fmt(candidate.lift)} · population {fmt(candidate.population_share)}</p></div>)}</div>}
          <p className="text-[11px] text-slate-400">Artifacts: {Object.entries(metric.artifact_ids || {}).map(([type, id]) => `${type} ${id}`).join(" · ") || "No reusable artifact available"}</p>
        </div></td></tr>}</Fragment>;
      })}
      {!visible.length && <tr><td colSpan="9" className="p-6 text-center text-sm text-slate-500">No features match the selected category and workflow status.</td></tr>}
    </tbody></table></div></div>
  </section>;
}
