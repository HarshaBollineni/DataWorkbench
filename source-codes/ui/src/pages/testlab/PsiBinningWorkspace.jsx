import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, Check, ChevronDown, ChevronRight, GitCompareArrows } from "lucide-react";

import {
  approvePsiBinsBatchV2, createManualPsiBinDraftV2, createNumericPsiBinOverrideV2, createPsiBinDraftV2, freezePsiBinDraftV2,
  getPsiBinReviewV2, getPsiCandidateValuesV2, previewPsiBinsV2, promotePsiIvBinsV2, psiBinDraftStreamUrlV2, revisePsiBinsV2,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatBinLabels } from "./binLabelDisplay";
import { BinningWorkspace } from "./FeatureTargetEvidence";
import { binningClassification, hasBinningResult, reviewBinCounts } from "./psiWorkflow";

const statusLabel = {
  ready: "Approved and frozen",
  confirmation_required: "Exact reviewed match",
  review_required: "Review required",
  decision_required: "Review or adjust bins",
  generation_available: "New draft required",
  manual_required: "Manual grouping required",
};

const pct = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(2)}%`;

function DefinitionSummary({ payload }) {
  if (!payload) return null;
  const definition = payload.definition || payload;
  const boundaries = definition.numeric_splits || payload.boundaries || [];
  const groups = definition.categorical_groups || (payload.groups || []).map((group) => group.values);
  const specialCount = payload.special_value_bins?.length || Object.keys(definition.special_values || {}).length;
  const missingBin = payload.missing_bin ?? definition.missing_bin ?? true;
  const guarded = definition.out_of_range_guard_bins || payload.underflow_guard || payload.overflow_guard;
  return <div className="grid gap-2 text-xs sm:grid-cols-4">
    <div className="rounded border border-slate-200 p-2"><span className="text-slate-500">Definition</span><strong className="mt-1 block">{boundaries.length ? `${boundaries.length + 1} numeric bins` : `${groups.length} categorical groups`}</strong></div>
    <div className="rounded border border-slate-200 p-2"><span className="text-slate-500">Missing values</span><strong className="mt-1 block">{missingBin ? "Separate bin" : "Not defined"}</strong></div>
    <div className="rounded border border-slate-200 p-2"><span className="text-slate-500">Special values</span><strong className="mt-1 block">{specialCount ? `${specialCount} protected bin${specialCount === 1 ? "" : "s"}` : "None declared"}</strong></div>
    <div className="rounded border border-slate-200 p-2"><span className="text-slate-500">Future values</span><strong className="mt-1 block">{guarded ? "Underflow / overflow guarded" : `Unseen: ${payload.unseen_category_policy || definition.unseen_category_policy || "separate bin"}`}</strong></div>
  </div>;
}

function CoverageSummary({ applicability }) {
  if (!applicability) return null;
  const coverage = applicability.coverage || {};
  const complete = coverage.complete ?? applicability.reconciled;
  const observed = coverage.observed_distinct_count;
  const covered = coverage.covered_distinct_count;
  const exactValues = coverage.exact_value_set;
  const valueMessage = exactValues === true
    ? `The library and Baseline contain the same ${observed} observed values.`
    : observed != null && covered != null ? `${covered} of ${observed} observed Baseline values are covered.` : null;
  return <div className={`mt-3 rounded border p-3 text-xs ${complete ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
    <strong>{complete ? "Coverage verified automatically" : "Coverage exceptions require review"}</strong>
    <span className="ml-2">{Number(applicability.evaluated_rows || 0).toLocaleString()} Baseline rows assigned to bins.</span>
    {valueMessage && <span className="mt-1 block">{valueMessage}</span>}
    {applicability.fine_foundation_policy === "baseline_exact_values_under_50" && <span className="mt-1 block">Fine bins were rebuilt automatically as one bin per observed Baseline value; the repository grouping is used only as the coarse-bin proposal.</span>}
    {applicability.fine_foundation_policy === "diagnostic_2_iv_target_aware" && <span className="mt-1 block">Diagnostic 2 IV fine bins are retained as the governed foundation because this PSI run uses a target. A previous matched run contributes only the coarse-bin proposal.</span>}
    {!!coverage.unused_library_value_count && <span className="mt-1 block">{coverage.unused_library_value_count} library value(s) are not present in this Baseline; they remain available for future Current values.</span>}
    {applicability.warnings?.length > 0 && <ul className="mt-2 list-disc pl-4">{applicability.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
  </div>;
}

function BinTable({ title, rows }) {
  if (!rows?.length) return null;
  const labels = formatBinLabels(rows.map((row) => row.label));
  return <section className="mt-3">
    <h4 className="mb-1 text-xs font-semibold text-slate-700">{title} · {rows.length}</h4>
    <div className="max-h-64 overflow-auto rounded border border-slate-200">
      <table className="w-full text-left text-xs"><thead className="sticky top-0 bg-slate-50 text-slate-500"><tr><th className="p-2">Bin</th><th className="p-2">Kind</th><th className="p-2 text-right">Baseline rows</th><th className="p-2 text-right">Population</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={row.bin_id || row.label} className="border-t border-slate-100"><td className="p-2 font-medium text-slate-900">{labels[index]}</td><td className="p-2 text-slate-500">{row.kind}</td><td className="p-2 text-right">{Number(row.rows || 0).toLocaleString()}</td><td className="p-2 text-right">{pct(row.population_share)}</td></tr>)}</tbody>
      </table>
    </div>
  </section>;
}

function ProgressiveDraftResults({ previews }) {
  const rows = Object.values(previews);
  if (!rows.length) return null;
  return <section className="overflow-hidden rounded-lg border border-slate-200 bg-white" data-testid="psi-progressive-drafts">
    <header className="border-b border-slate-200 bg-slate-50 px-4 py-3"><h3 className="text-sm font-semibold text-slate-900">Completed variable definitions · {rows.length}</h3><p className="mt-1 text-xs text-slate-500">Preliminary and read-only until preparation finishes. Bin editing, approval, freezing and promotion are unavailable.</p></header>
    <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-slate-500"><tr><th className="px-4 py-2">Variable</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Type</th><th className="px-3 py-2 text-right">Fine bins</th><th className="px-3 py-2 text-right">Coarse bins</th><th className="px-3 py-2 text-right">Metric</th><th className="px-3 py-2">Method</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.feature} className="border-t border-slate-100"><td className="px-4 py-2 font-medium text-slate-900">{row.feature}</td><td className={`px-3 py-2 ${row.status === "failed" ? "text-red-700" : "text-emerald-700"}`}>{row.status}{row.error ? `: ${row.error}` : ""}</td><td className="px-3 py-2">{row.kind || "—"}</td><td className="px-3 py-2 text-right">{row.fine_bin_count ?? "—"}</td><td className="px-3 py-2 text-right">{row.coarse_bin_count ?? "—"}</td><td className="px-3 py-2 text-right font-mono">{row.metric_value == null ? "—" : Number(row.metric_value).toFixed(4)}</td><td className="px-3 py-2">{row.methodology || "—"}</td></tr>)}</tbody>
    </table></div>
  </section>;
}

function BatchApprovalControls({ eligible, selected, approved, pending, busy, onToggleAll, onApprove }) {
  const allSelected = eligible > 0 && selected === eligible;
  const readiness = eligible > 0
    ? `${eligible} ready for approval${pending ? ` · ${pending} still require preparation or review` : ""}`
    : pending > 0 ? `${pending} still require preparation or review` : `${approved} already approved and frozen`;
  return <div className="flex flex-wrap items-center gap-2" data-testid="psi-batch-approval-controls">
    <label className={`flex h-8 items-center gap-2 rounded border border-slate-200 px-3 text-xs font-medium ${eligible ? "cursor-pointer bg-white text-slate-700" : "cursor-not-allowed bg-slate-50 text-slate-400"}`}>
      <input type="checkbox" checked={allSelected} disabled={!eligible || busy} onChange={onToggleAll} />
      Select all ready ({eligible})
    </label>
    <Button size="sm" disabled={!selected || busy} onClick={onApprove}>{busy ? "Approving…" : `Approve selected (${selected})`}</Button>
    <span className="text-[11px] text-slate-500">{readiness}</span>
  </div>;
}

function psiBinningWorkspace(review) {
  const applicability = review?.applicability;
  // Fine-bin artifacts are foundations, not editable coarse proposals.
  if (review?.artifact?.artifact_type === "fine_bins") return null;
  const fineDefinition = applicability?.fine_definition || review?.fine_payload?.definition;
  const coarseDefinition = applicability?.coarse_definition || review?.payload?.definition;
  const fineBins = applicability?.fine_bins || review?.fine_payload?.bins || [];
  const coarseBins = applicability?.coarse_bins || review?.payload?.bins || [];
  if (!fineDefinition || !coarseDefinition || !fineBins.length || !coarseBins.length) return null;
  const metrics = applicability?.coarse_metrics || review?.payload?.review_metrics || [];
  const iv = metrics.find((metric) => ["information_value", "maximum_one_vs_rest_iv"].includes(metric.name))?.value;
  return {
    initial: { feature: review.feature, fine_definition: fineDefinition, coarse_definition: coarseDefinition,
      automatic_coarse_definition: coarseDefinition, fine_bins: fineBins, coarse_bins: coarseBins,
      fine_metrics: applicability?.fine_metrics || [], coarse_metrics: metrics,
      coarse_groups: applicability?.coarse_groups || fineBins.filter((row) => row.kind === "regular").map((row) => [row.bin_id]),
      warnings: applicability?.warnings || [] },
    governance: { local: { iv } },
  };
}

export default function PsiBinningWorkspace({ manifest, busy, patch, reload, acceptManifest, onBack }) {
  const [expanded, setExpanded] = useState("");
  const [reviews, setReviews] = useState({});
  const [loading, setLoading] = useState("");
  const [manual, setManual] = useState({});
  const [categoricalEdits, setCategoricalEdits] = useState({});
  const [numericOverrides, setNumericOverrides] = useState({});
  const [edits, setEdits] = useState({});
  const [batchProgress, setBatchProgress] = useState(null);
  const [batchPreviews, setBatchPreviews] = useState({});
  const [batchError, setBatchError] = useState("");
  const [selectedApprovals, setSelectedApprovals] = useState([]);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [promoted, setPromoted] = useState({});
  const byName = useMemo(() => Object.fromEntries((manifest.feature_metadata || []).map((row) => [row.column, row])), [manifest.feature_metadata]);
  const rows = (manifest.selected_features || []).map((name) => byName[name]).filter(Boolean);
  const frozenCount = rows.filter((row) => row.bin_route?.readiness === "ready").length;
  const allBinsReady = rows.length > 0 && frozenCount === rows.length;
  const resultCount = rows.filter((row) => hasBinningResult(row.bin_route)).length;
  const livePreparedCount = batchProgress
    ? Math.max(resultCount, Number(batchProgress.current || 0) - Number(batchProgress.failed || 0))
    : resultCount;
  const automaticDraftRows = rows.filter((row) => [
    "generate_target_aware", "generate_target_free", "generate_constant_guard",
    "use_universal_iv",
  ].includes(row.bin_route?.workflow));
  const automaticDraftKey = automaticDraftRows.map((row) => row.column).join("\u0000");
  const approvalEligible = rows.filter((row) => ["confirm_frozen_reuse", "review_draft"].includes(row.bin_route?.workflow));
  const eligibleApprovalNames = new Set(approvalEligible.map((row) => row.column));
  const activeSelectedApprovals = selectedApprovals.filter((feature) => eligibleApprovalNames.has(feature));
  const pendingApprovalCount = Math.max(0, rows.length - frozenCount - approvalEligible.length);

  useEffect(() => {
    if (!automaticDraftKey) return undefined;
    let disposed = false;
    let stream;
    let connectionTimeout;
    // Deferring the connection lets React Strict Mode dispose its probe effect
    // before any server-side batch is started. The real effect then owns the stream.
    const startConnection = window.setTimeout(() => {
      if (disposed) return;
      setBatchError("");
      setBatchPreviews({});
      setBatchProgress({ current: 0, total: automaticDraftRows.length,
        feature: "Connecting to the preparation service…", etaSeconds: null });
      stream = new EventSource(psiBinDraftStreamUrlV2(manifest.run_id));
      connectionTimeout = window.setTimeout(() => {
        if (disposed) return;
        stream.close();
        setBatchError("Draft preparation did not connect to the server within 8 seconds. No Baseline work was started; reopen this workspace to retry.");
        setBatchProgress(null);
      }, 8000);
      stream.onmessage = async (message) => {
        if (disposed) return;
        window.clearTimeout(connectionTimeout);
        let event;
        try { event = JSON.parse(message.data); }
        catch { event = { phase: "error", message: "The generation service returned an invalid progress event." }; }
        if (event.phase === "queued") {
          setBatchProgress((current) => ({ current: current?.current || 0,
            total: current?.total || automaticDraftRows.length,
            feature: event.thought || "Waiting in the diagnostic queue…",
            etaSeconds: null, failed: current?.failed || 0,
            optimizationProfile: current?.optimizationProfile,
          }));
        }
        if (event.phase === "heartbeat" && (event.resources?.diagnostic_jobs_waiting || event.resources?.optimizer_waiting)) {
          setBatchProgress((current) => current ? ({ ...current,
            feature: event.thought || "Waiting for shared optimizer capacity…",
            etaSeconds: null,
          }) : current);
        }
        if (event.phase === "start" || event.phase === "progress") {
          setBatchProgress((current) => ({ current: event.done, total: event.total,
            feature: event.feature || event.thought, etaSeconds: event.eta_seconds,
            failed: event.failed ?? current?.failed ?? 0,
            optimizationProfile: event.optimization_profile || current?.optimizationProfile }));
          if (event.preview?.feature) setBatchPreviews((current) => ({ ...current,
            [event.preview.feature]: event.preview,
          }));
        }
        if (event.phase === "error" || event.phase === "done") {
          if (event.phase === "error") setBatchError(event.message || "Draft generation failed.");
          if (event.phase === "done" && event.failed) setBatchError(`${event.failed} variable${event.failed === 1 ? "" : "s"} could not be generated.`);
          stream.close();
          if (event.phase === "done") setBatchProgress((current) => ({ ...current,
            current: event.done, total: event.total, failed: event.failed || 0,
            feature: "Refreshing saved definitions…", etaSeconds: null,
            optimizationProfile: event.optimization_profile || current?.optimizationProfile }));
          else setBatchProgress(null);
          try { await reload(); } catch (problem) { if (!disposed) setBatchError(problem.message); }
          finally { if (!disposed) setBatchProgress(null); }
        }
      };
      stream.onerror = async () => {
        if (disposed) return;
        window.clearTimeout(connectionTimeout);
        setBatchError("The live progress connection stopped. Completed coarse drafts were saved; reopening this workspace will resume the remainder.");
        stream.close();
        setBatchProgress(null);
        try { await reload(); } catch (problem) { if (!disposed) setBatchError(problem.message); }
      };
    }, 0);
    return () => {
      disposed = true;
      window.clearTimeout(startConnection);
      window.clearTimeout(connectionTimeout);
      stream?.close();
    };
  }, [automaticDraftKey, automaticDraftRows.length, manifest.run_id, reload]);

  const loadReview = async (feature, artifactId) => {
    setExpanded(feature); setLoading(feature);
    try {
      const result = await getPsiBinReviewV2(manifest.run_id, feature, artifactId);
      setReviews((current) => ({ ...current, [feature]: result }));
      const definition = result.payload?.definition || result.fine_payload?.definition;
      if (definition) setEdits((current) => ({ ...current, [feature]: definition }));
      const coarseGroups = result.payload?.groups
        || (definition?.categorical_groups || []).map((values, index) => ({ label: `Group ${index + 1}`, values }));
      if (coarseGroups.length) {
        setCategoricalEdits((current) => ({
          ...current,
          [feature]: coarseGroups.map((group, index) => `${group.label || `Group ${index + 1}`}: ${(group.values || []).join(", ")}`).join("\n"),
        }));
      }
    } finally { setLoading(""); }
  };
  const generate = async (feature, generateNew = false) => {
    setLoading(feature);
    try {
      const result = await createPsiBinDraftV2(manifest.run_id, feature, generateNew);
      await reload();
      await loadReview(feature, result.artifact.artifact_id);
    } finally { setLoading(""); }
  };
  const freezeDraft = async (feature, artifactId) => {
    setLoading(feature);
    try {
      const result = await freezePsiBinDraftV2(manifest.run_id, feature, artifactId);
      acceptManifest(result.manifest);
    }
    finally { setLoading(""); }
  };
  const approveReuse = async (feature, artifact, mismatch = false) => {
    setLoading(feature);
    try {
      await patch({ kind: "bin_assignment", feature, value: { artifact_id: artifact.artifact_id, confirm_mismatch: mismatch } });
    } finally { setLoading(""); }
  };
  const adoptAppliedProposal = async (feature, review) => {
    setLoading(feature);
    try {
      const revised = await revisePsiBinsV2(manifest.run_id, feature, {
        artifact_id: review.artifact.artifact_id,
        definition: review.applicability.coarse_definition,
      });
      const frozen = await freezePsiBinDraftV2(manifest.run_id, feature, revised.artifact.artifact_id);
      acceptManifest(frozen.manifest);
    } finally { setLoading(""); }
  };
  const approveSelected = async () => {
    if (!activeSelectedApprovals.length) return;
    setApprovalBusy(true); setBatchError("");
    try {
      const result = await approvePsiBinsBatchV2(manifest.run_id, activeSelectedApprovals);
      setSelectedApprovals([]);
      acceptManifest(result.manifest);
    } catch (error) {
      setBatchError(error.message);
    } finally { setApprovalBusy(false); }
  };
  const toggleApproval = (feature) => setSelectedApprovals((current) => current.includes(feature)
    ? current.filter((name) => name !== feature) : [...current, feature]);
  const toggleAllApprovals = () => setSelectedApprovals(
    activeSelectedApprovals.length === approvalEligible.length ? [] : approvalEligible.map((row) => row.column),
  );
  const loadManual = async (feature) => {
    const result = await getPsiCandidateValuesV2(manifest.run_id, feature);
    setManual((current) => ({ ...current, [feature]: result.values.map((value, index) => `Group ${index + 1}: ${value}`).join("\n") }));
    setExpanded(feature);
  };
  const createManual = async (feature) => {
    const groups = (manual[feature] || "").split(/\r?\n/).map((line) => {
      const [label, ...rest] = line.split(":");
      return { label: label.trim(), values: rest.join(":").split(",").map((value) => value.trim()).filter(Boolean) };
    }).filter((group) => group.label && group.values.length);
    const result = await createManualPsiBinDraftV2(manifest.run_id, feature, groups);
    await reload();
    await loadReview(feature, result.artifact.artifact_id);
  };
  const createCategoricalRevision = async (feature) => {
    const groups = (categoricalEdits[feature] || "").split(/\r?\n/).map((line) => {
      const [label, ...rest] = line.split(":");
      return { label: label.trim(), values: rest.join(":").split(",").map((value) => value.trim()).filter(Boolean) };
    }).filter((group) => group.label && group.values.length);
    setLoading(feature);
    try {
      const result = await createManualPsiBinDraftV2(manifest.run_id, feature, groups);
      await reload();
      await loadReview(feature, result.artifact.artifact_id);
    } finally { setLoading(""); }
  };
  const createNumericOverride = async (feature) => {
    const value = numericOverrides[feature] || {};
    setLoading(feature); setBatchError("");
    try {
      const result = await createNumericPsiBinOverrideV2(manifest.run_id, feature, {
        cuts: value.cuts || "", special_values: value.specialValues || "", rationale: value.rationale || "",
      });
      await reload();
      await loadReview(feature, result.artifact.artifact_id);
    } catch (error) { setBatchError(error.message); }
    finally { setLoading(""); }
  };
  const promoteUniversal = async (feature, artifactId) => {
    if (!window.confirm(`Make ${feature} the universal Diagnostic 2 definition for this exact full Baseline snapshot, table and governed target?`)) return;
    setLoading(feature); setBatchError("");
    try {
      const result = await promotePsiIvBinsV2(manifest.run_id, feature, artifactId);
      setPromoted((current) => ({ ...current, [feature]: result.revision_id }));
    } catch (error) { setBatchError(error.message); }
    finally { setLoading(""); }
  };
  const revise = async (feature, review) => {
    const definition = edits[feature];
    const result = await revisePsiBinsV2(manifest.run_id, feature, {
      artifact_id: review.artifact.artifact_id, definition,
    });
    await reload();
    await loadReview(feature, result.artifact.artifact_id);
  };
  const toggleNumericBoundary = (feature, value) => setEdits((current) => {
    const definition = current[feature];
    const selected = new Set(definition.numeric_splits || []);
    if (selected.has(value)) selected.delete(value); else selected.add(value);
    return { ...current, [feature]: { ...definition, numeric_splits: [...selected].sort((a, b) => a - b) } };
  });

  if (!rows.length) return <div className="rounded-lg border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">
    <strong>No persisted PSI variables are available for matching.</strong>
    <p className="mt-1">Return to PSI setup, select at least one eligible variable, and save the selection before choosing a binning source.</p>
    <Button className="mt-3" variant="outline" onClick={onBack}><ArrowLeft className="h-4 w-4" /> Back to PSI setup</Button>
  </div>;

  return <div className="grid gap-4" data-testid="psi-binning-workspace">
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4">
      <div><p className="text-xs font-semibold uppercase tracking-wide text-teal-700">PSI binning workspace</p><h2 className="mt-1 text-lg font-semibold text-slate-950">Review and approve bins</h2><p className="mt-1 text-xs text-slate-500">The route is automatic: {manifest.binning_source_choice?.explanation}</p></div>
      <Button variant="outline" disabled={!!batchProgress} onClick={onBack}><ArrowLeft className="h-4 w-4" /> Back to PSI setup</Button>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs"><span className="text-slate-500">Selected variables</span><strong className="mt-1 block text-lg">{rows.length}</strong></div>
      <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs"><span className="text-slate-500">Prepared definitions</span><strong className="mt-1 block text-lg">{livePreparedCount}</strong></div>
      <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs"><span className="text-slate-500">Approved and frozen</span><strong className="mt-1 block text-lg">{frozenCount}</strong></div>
      <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs"><span className="text-slate-500">Remaining review</span><strong className="mt-1 block text-lg">{rows.length - frozenCount}</strong></div>
    </div>
    {batchProgress && <div className="rounded-lg border border-teal-200 bg-teal-50 p-3 text-xs text-teal-900"><strong>Preparing Baseline coarse-bin drafts</strong><span className="ml-2">{batchProgress.current} of {batchProgress.total} · {batchProgress.feature}</span>{batchProgress.etaSeconds != null && batchProgress.etaSeconds > 0 && <span className="ml-2 text-teal-700">· about {Math.max(1, Math.ceil(batchProgress.etaSeconds / 60))} min remaining</span>}{batchProgress.optimizationProfile?.mode === "full" && <span className="mt-1 block text-[11px] text-teal-800">Full Diagnostic 2 optimization · up to {batchProgress.optimizationProfile.solver_time_limit_seconds}s solver time per variable · {batchProgress.optimizationProfile.max_workers} variables processed concurrently.</span>}<div className="mt-2 h-1.5 overflow-hidden rounded-full bg-teal-100"><div className="h-full bg-teal-600 transition-all" style={{ width: `${batchProgress.total ? (batchProgress.current / batchProgress.total) * 100 : 0}%` }} /></div></div>}
    {batchProgress && <ProgressiveDraftResults previews={batchPreviews} />}
    {batchError && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">Draft preparation stopped: {batchError}</div>}
    {!batchProgress && <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <header className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div><h3 className="text-sm font-semibold text-slate-900">Variable-level binning decisions</h3><p className="mt-1 text-xs text-slate-500">Review the generated or exact universal definition, its Baseline counts, missing/special handling and guards. Refinements create a PSI-specific version; they do not alter the universal Diagnostic 2 definition.</p></div>
        <div className="flex flex-wrap items-center gap-2">
          <BatchApprovalControls eligible={approvalEligible.length} selected={activeSelectedApprovals.length}
            approved={frozenCount} pending={pendingApprovalCount} busy={approvalBusy || busy}
            onToggleAll={toggleAllApprovals} onApprove={approveSelected} />
          <Button size="sm" disabled={!allBinsReady || approvalBusy || busy || !!batchProgress}
            title={allBinsReady ? "Open the final PSI review" : "Approve and freeze every selected variable before continuing"}
            onClick={onBack}><GitCompareArrows className="h-4 w-4" /> Continue to final review</Button>
        </div>
      </header>
      <div className="divide-y divide-slate-200">{rows.map((feature) => {
        const route = feature.bin_route || {}; const artifact = route.artifact;
        const isOpen = expanded === feature.column; const review = reviews[feature.column];
        const draftId = route.workflow === "review_draft" ? artifact?.artifact_id : null;
        const classification = binningClassification(route, manifest.binning_source_choice?.mode);
        const counts = reviewBinCounts(review, artifact);
        const editableBinning = psiBinningWorkspace(review);
        const sourceLabel = artifact?.artifact_type
          ? `${artifact.artifact_type}${artifact.scope ? ` · ${artifact.scope}` : ""}`
          : manifest.binning_source_choice?.mode === "repository" ? "Repository assessment" : "Baseline generation";
        const lineageLabel = artifact ? "Governed Baseline lineage" : "Awaiting diagnostic-specific definition";
        return <article key={feature.column}>
          <div className="flex flex-wrap items-center gap-3 px-4 py-3">
            {["confirm_frozen_reuse", "review_draft"].includes(route.workflow) && <input type="checkbox"
              aria-label={`Select ${feature.column} for approval`} checked={selectedApprovals.includes(feature.column)}
              disabled={approvalBusy || busy || loading === feature.column}
              onChange={() => toggleApproval(feature.column)} />}
            <button className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => isOpen ? setExpanded("") : artifact ? loadReview(feature.column, artifact.artifact_id) : setExpanded(feature.column)}>
              {isOpen ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}<span className="min-w-0"><strong className="block truncate text-sm text-slate-900">{feature.column}</strong><span className="block text-xs text-slate-500">{feature.logical_type} · {feature.distinct_count} unique values</span><span className="mt-0.5 block text-[11px] font-medium text-slate-700">{classification}</span><span className="block text-[11px] text-slate-500">{sourceLabel} · {lineageLabel} · {counts.coarse ?? "—"} proposed coarse bins</span><span className="mt-0.5 block text-[11px] text-slate-500">{route.reason}</span></span>
            </button>
            <Badge variant={route.readiness === "ready" ? "success" : "secondary"}>{statusLabel[route.readiness] || route.readiness}</Badge>
            <div className="flex flex-wrap gap-1">
              {route.workflow === "reuse_frozen" && <span className="flex items-center gap-1 text-xs font-medium text-emerald-700"><Check className="h-4 w-4" /> Approved</span>}
              {route.workflow === "confirm_frozen_reuse" && <><Button size="sm" variant="outline" disabled={approvalBusy || loading === feature.column} onClick={() => loadReview(feature.column, artifact.artifact_id)}>Review match</Button><Button size="sm" variant="outline" disabled={approvalBusy || loading === feature.column} onClick={() => generate(feature.column, true)}>Generate new</Button></>}
              {route.workflow === "bin_mismatch_choice" && <><Button size="sm" variant="outline" onClick={() => loadReview(feature.column, artifact.artifact_id)}>Review or adjust bins</Button><Button size="sm" variant="outline" onClick={() => generate(feature.column, true)}>Generate new</Button></>}
              {["generate_target_aware", "generate_target_free", "generate_constant_guard"].includes(route.workflow) && <Button size="sm" variant="outline" disabled={loading === feature.column} onClick={() => generate(feature.column)}>Generate draft</Button>}
              {["review_existing_automatic", "build_coarse_from_fine"].includes(route.workflow) && <><span className="text-xs text-slate-500">Preparing coarse draft…</span><Button size="sm" variant="outline" disabled={loading === feature.column || !!batchProgress} onClick={() => generate(feature.column, true)}>Generate new</Button></>}
              {route.workflow === "manual_grouping" && <Button size="sm" variant="outline" onClick={() => loadManual(feature.column)}>Define groups</Button>}
              {route.workflow === "review_draft" && <Button size="sm" variant="outline" disabled={approvalBusy || loading === feature.column} onClick={() => loadReview(feature.column, draftId)}>Review draft</Button>}
            </div>
          </div>
          {isOpen && <div className="border-t border-slate-100 bg-slate-50 p-4">
            {loading === feature.column && !review ? <p className="text-xs text-slate-500">Loading governed bin evidence…</p> : <>
              {route.warning && <p className="mb-3 flex gap-2 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800"><AlertTriangle className="h-4 w-4 shrink-0" />{route.warning}</p>}
              {review && <><div className="mb-3 flex flex-wrap gap-2 text-[11px] text-slate-500"><span>{review.population_match ? "Created from this exact Baseline" : "PSI-specific definition for this run"}</span><span>·</span><span>{review.target_compatible ? "Compatible with this PSI setup" : "Target setup differs"}</span></div><DefinitionSummary payload={review.payload} />
                <CoverageSummary applicability={review.applicability} />
                {editableBinning ? <BinningWorkspace key={review.artifact.artifact_id} initial={editableBinning.initial} initialGovernance={editableBinning.governance}
                  localOnly saveButtonLabel="Save PSI revision"
                  previewDefinition={(definition) => previewPsiBinsV2(manifest.run_id, feature.column, {
                    artifact_id: review.artifact.artifact_id, definition,
                  })}
                  saveDefinition={async (definition) => {
                    const saved = await revisePsiBinsV2(manifest.run_id, feature.column, {
                      artifact_id: review.artifact.artifact_id, definition,
                    });
                    return { ...saved.review, _artifact: saved.artifact };
                  }}
                  onChanged={async (_next, _governance, saved) => {
                    await reload();
                    await loadReview(feature.column, saved._artifact.artifact_id);
                  }} /> : <><BinTable title="Coarse-bin Baseline applicability" rows={review.payload?.bins} /><BinTable title="Fine-bin Baseline foundation" rows={review.fine_payload?.bins} /></>}
                {(review.payload?.kind === "numeric" || review.payload?.definition?.feature_type === "numeric") && <details className="mt-3 rounded border border-amber-200 bg-amber-50 p-3">
                  <summary className="cursor-pointer text-xs font-semibold text-amber-900">Advanced: create a PSI-only numeric override</summary>
                  <p className="mt-2 text-[11px] text-amber-800">This exceptional definition applies only to this PSI run. It rescans the Baseline feature once; future reruns resume the standard IV or PSI-contract route.</p>
                  <p className="mt-2 text-[11px] font-medium text-slate-700">✓ Missing values are kept in a separate bin. Includes blank cells and values recognized as missing during Data Sourcing, such as NA or Null.</p>
                  <div className="mt-3 grid gap-2 md:grid-cols-2">
                    <label className="text-xs text-slate-700">Special values (optional)<input className="mt-1 block w-full rounded border border-slate-300 bg-white px-2 py-1.5 font-mono" placeholder="-999,-888 (leave empty if none)" value={numericOverrides[feature.column]?.specialValues || ""} onChange={(event) => setNumericOverrides((current) => ({ ...current, [feature.column]: { ...current[feature.column], specialValues: event.target.value } }))} /></label>
                    <label className="text-xs text-slate-700">Numeric cuts<input className="mt-1 block w-full rounded border border-slate-300 bg-white px-2 py-1.5 font-mono" placeholder="10,25,50,100" value={numericOverrides[feature.column]?.cuts || ""} onChange={(event) => setNumericOverrides((current) => ({ ...current, [feature.column]: { ...current[feature.column], cuts: event.target.value } }))} /></label>
                  </div>
                  <label className="mt-2 block text-xs text-slate-700">Reason for override<input className="mt-1 block w-full rounded border border-slate-300 bg-white px-2 py-1.5" value={numericOverrides[feature.column]?.rationale || ""} onChange={(event) => setNumericOverrides((current) => ({ ...current, [feature.column]: { ...current[feature.column], rationale: event.target.value } }))} /></label>
                  <p className="mt-2 text-[11px] text-slate-600">Use commas only; thousands separators are not supported. NA, Null, Infinity, blank tokens and non-numeric text are rejected with the exact position.</p>
                  <Button className="mt-3" size="sm" disabled={loading === feature.column || !(numericOverrides[feature.column]?.cuts || "").trim()} onClick={() => createNumericOverride(feature.column)}>Rescan Baseline and create PSI override</Button>
                </details>}
                {!editableBinning && review.fine_payload?.definition?.feature_type === "numeric" && <section className="mt-3 rounded border border-slate-200 bg-white p-3"><h4 className="text-xs font-semibold">Coarse split points from cached fine bins</h4><p className="mt-1 text-[11px] text-slate-500">Select retained fine boundaries. Saving creates a new PSI-specific coarse revision.</p><div className="mt-2 flex flex-wrap gap-2">{(review.fine_payload.definition.numeric_splits || []).map((value) => <label key={value} className="flex items-center gap-1 rounded border border-slate-200 px-2 py-1 text-xs"><input type="checkbox" checked={(edits[feature.column]?.numeric_splits || []).includes(value)} onChange={() => toggleNumericBoundary(feature.column, value)} />{value}</label>)}</div><Button className="mt-3" size="sm" disabled={busy} onClick={() => revise(feature.column, review)}>Create PSI-specific revision</Button></section>}
                {!editableBinning && review.fine_payload?.definition?.feature_type === "categorical" && <section className="mt-3 rounded border border-slate-200 bg-white p-3"><h4 className="text-xs font-semibold">Editable categorical coarse groups</h4><p className="mt-1 text-[11px] text-slate-500">Each nested array is one coarse group. Every cached fine category must appear exactly once.</p><textarea key={review.artifact.artifact_id} className="mt-2 min-h-32 w-full rounded border border-slate-200 p-2 font-mono text-xs" defaultValue={JSON.stringify(edits[feature.column]?.categorical_groups || [], null, 2)} onBlur={(event) => { try { const groups = JSON.parse(event.target.value); setEdits((current) => ({ ...current, [feature.column]: { ...current[feature.column], categorical_groups: groups } })); } catch { setBatchError("Categorical groups must be valid JSON before a revision can be created."); } }} /><Button className="mt-2" size="sm" onClick={() => revise(feature.column, review)}>Create local revision</Button></section>}
                {!review.fine_payload && (review.payload?.kind === "categorical" || review.payload?.groups?.length > 0) && <section className="mt-3 rounded border border-slate-200 bg-white p-3"><h4 className="text-xs font-semibold">Editable categorical coarse groups</h4><p className="mt-1 text-[11px] text-slate-500">One group per line: Group name: exact value, exact value. Saving creates a new PSI-specific draft.</p><textarea className="mt-2 min-h-32 w-full rounded border border-slate-200 p-2 font-mono text-xs" value={categoricalEdits[feature.column] || ""} onChange={(event) => setCategoricalEdits((current) => ({ ...current, [feature.column]: event.target.value }))} /><Button className="mt-2" size="sm" disabled={loading === feature.column} onClick={() => createCategoricalRevision(feature.column)}>Create PSI-specific revision</Button></section>}
                <div className="mt-3 flex flex-wrap gap-2">{review.payload?.universal_eligibility === "candidate" && !promoted[feature.column] && <Button size="sm" variant="outline" disabled={loading === feature.column} onClick={() => promoteUniversal(feature.column, review.artifact.artifact_id)}>Make universal for Diagnostic 2</Button>}{promoted[feature.column] && <span className="flex items-center gap-1 text-xs font-medium text-emerald-700"><Check className="h-4 w-4" /> Universal for Diagnostic 2</span>}{route.workflow === "confirm_frozen_reuse" && <Button size="sm" disabled={approvalBusy || busy || loading === feature.column} onClick={() => approveReuse(feature.column, artifact)}>{loading === feature.column ? "Approving…" : "Use these bins"}</Button>}{route.workflow === "bin_mismatch_choice" && <Button size="sm" disabled={approvalBusy || busy || loading === feature.column || !review.applicability?.coarse_definition} onClick={() => adoptAppliedProposal(feature.column, review)}>{loading === feature.column ? "Applying…" : "Use applied proposal"}</Button>}{route.workflow === "review_draft" && <Button size="sm" disabled={approvalBusy || busy || loading === feature.column} onClick={() => freezeDraft(feature.column, review.artifact.artifact_id)}>{loading === feature.column ? "Approving…" : "Approve and freeze"}</Button>}</div></>}
              {manual[feature.column] != null && <div><p className="mb-1 text-xs text-slate-600">One group per line: Group name: value, value</p><textarea className="min-h-36 w-full rounded border border-slate-200 p-2 font-mono text-xs" value={manual[feature.column]} onChange={(event) => setManual((current) => ({ ...current, [feature.column]: event.target.value }))} /><Button className="mt-2" size="sm" onClick={() => createManual(feature.column)}>Create manual draft</Button></div>}
            </>}
          </div>}
        </article>;
      })}</div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-slate-50 px-4 py-3">
        <BatchApprovalControls eligible={approvalEligible.length} selected={activeSelectedApprovals.length}
          approved={frozenCount} pending={pendingApprovalCount} busy={approvalBusy || busy}
          onToggleAll={toggleAllApprovals} onApprove={approveSelected} />
        <span className="text-[11px] text-slate-500">Lineage mismatches remain individual decisions and are never batch-approved implicitly.</span>
      </footer>
    </section>}
    {allBinsReady && <div className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50 p-4"><span className="text-sm font-medium text-emerald-900">All selected variables have approved frozen bins.</span><Button onClick={onBack}><GitCompareArrows className="h-4 w-4" /> Continue to final review</Button></div>}
  </div>;
}
