import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StepCard } from "@/pages/sourcing/SourcingPresentation";
import {
  getDatasetStructureMaterializationV2, getDatasetStructureReviewV2,
  getDatasetStructureReviewCandidatesV2, patchDatasetStructureDraftV2, postDatasetStructureDecisionsV2, retryDatasetStructureMaterializationV2,
} from "@/api/client";
import {
  boundedCandidates, candidateEvidenceSummary, createAutosaveCoordinator, draftPayload,
  canConfirmStructure, createDecisionCoordinator, decisionPayload, createMaterializationPoller, facetCandidates, initialStructureSelections, insufficientAssistance,
  mergeCandidatePage, REVIEWABLE_MATERIALIZATION_STATUSES, reviewNeedsReconfirmation, safeWarningLabel, selectionMessage,
} from "./datasetStructureReview.js";

const FACETS = [
  { key: "default_entity_candidate_id", candidates: "entities", title: "Entity candidate", empty: "No applicable selection", hint: "An identifier candidate for continuity across observations." },
  { key: "default_temporal_candidate_id", candidates: "temporals", title: "Date or Period candidate", empty: "No applicable selection", hint: "A Date or Period candidate supported by the reviewed metadata." },
  { key: "row_grain_candidate_id", candidates: "row_grains", title: "Row grain", empty: "No applicable selection", hint: "Evidence-backed key combinations only; a repeated entity can be valid over time." },
];

function statusLabel(status) {
  return String(status || "not_started").replaceAll("_", " ");
}

function CandidateSelect({ itemId, table, facet, value, onChange }) {
  const [filter, setFilter] = useState("");
  const [remote, setRemote] = useState(null);
  const [loadingPage, setLoadingPage] = useState(false);
  const [pageError, setPageError] = useState("");
  const requestRef = useRef(null);
  const limit = table.candidate_limits?.[facet.candidates];
  const local = facetCandidates(table, facet.candidates);
  const allCandidates = remote?.items || local.items;
  const page = remote?.page || local.page;
  const candidates = boundedCandidates(allCandidates, filter, remote ? { limit: 100 } : limit);
  const staleSelection = value && !allCandidates.some((candidate) => candidate.candidate_id === value);
  const loadPage = useCallback(async ({ q = "", cursor, append = false } = {}) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoadingPage(true);
    setPageError("");
    try {
      const response = await getDatasetStructureReviewCandidatesV2(itemId, { tableId: table.table, facet: facet.candidates, q, cursor, limit: 20, signal: controller.signal });
      if (controller.signal.aborted) return;
      setRemote((current) => ({
        items: append ? mergeCandidatePage(current?.items || local.items, response.candidates || []) : (response.candidates || []),
        page: response.page || null,
      }));
    } catch (error) {
      if (error.name !== "AbortError") setPageError(error.message || "Candidates could not be loaded.");
    } finally { if (!controller.signal.aborted) setLoadingPage(false); }
  }, [facet.candidates, itemId, local.items, table.table]);
  useEffect(() => {
    if (!filter) {
      requestRef.current?.abort();
      const timer = window.setTimeout(() => setRemote(null), 0);
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => { loadPage({ q: filter }); }, 250);
    return () => { window.clearTimeout(timer); requestRef.current?.abort(); };
  }, [filter, loadPage]);
  useEffect(() => () => requestRef.current?.abort(), []);
  return <section className="rounded-md border border-slate-200 bg-white p-4">
    <div className="flex flex-wrap items-start justify-between gap-2"><div><h4 className="font-semibold text-slate-900">{facet.title}</h4><p className="mt-1 text-xs text-slate-500">{facet.hint}</p></div><span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-semibold uppercase text-slate-600">ranked evidence</span></div>
    <Input className="mt-3 h-9" aria-label={`Filter ${facet.title} candidates for ${table.table}`} placeholder="Filter label, predicate, instance, or evidence" value={filter} onChange={(event) => setFilter(event.target.value)} />
    <label className="mt-3 block text-sm text-slate-700"><span className="sr-only">{facet.title} for {table.table}</span><select className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={value ?? ""} onChange={(event) => onChange(event.target.value || null)}><option value="">{facet.empty}</option>{staleSelection && <option value={value}>Previous selection — evidence changed</option>}{candidates.map((candidate) => <option key={candidate.candidate_id} value={candidate.candidate_id}>{candidate.display_label}{candidate.recommended ? " — Recommended" : ""}{candidateEvidenceSummary(candidate) ? ` · ${candidateEvidenceSummary(candidate)}` : ""}</option>)}</select></label>
    {staleSelection && <p className="mt-2 text-xs text-amber-700">Previous selection — evidence changed. Choose a current candidate or no applicable selection to replace it.</p>}
    {candidates.length === 0 && <p className="mt-2 text-xs text-slate-500">No eligible candidates match this filter.</p>}
    {limit?.truncated && !remote && <p className="mt-2 text-xs text-slate-500">Showing the bounded {limit.returned} returned candidates; load more or refine the filter to inspect this safe projection.</p>}
    {page?.next_page != null && <p className="mt-2 text-xs text-slate-500">Additional bounded candidate pages are available from the review service.</p>}
    {(page?.next_cursor || page?.next_offset != null || (!remote && limit?.truncated)) && <Button type="button" className="mt-2" size="sm" variant="outline" disabled={loadingPage} onClick={() => loadPage({ q: filter, cursor: page?.next_cursor || page?.next_offset || `offset:${allCandidates.length}`, append: true })}>{loadingPage ? "Loading candidates…" : "Load more candidates"}</Button>}
    {pageError && <p className="mt-2 text-xs text-red-700" role="alert">{pageError}</p>}
    {candidates.find((candidate) => candidate.candidate_id === value) && <p className="mt-2 text-xs text-slate-500">{candidates.find((candidate) => candidate.candidate_id === value).predicate} · {candidates.find((candidate) => candidate.candidate_id === value).instance_key}</p>}
    {candidates.find((candidate) => candidate.candidate_id === value)?.recommended && <p className="mt-2 flex items-center gap-1 text-xs text-emerald-700"><CheckCircle2 className="h-3.5 w-3.5" /> Recommended from aggregate evidence; it remains an unconfirmed draft.</p>}
  </section>;
}

function ExpectedCadence({ table, value, onChange }) {
  const proposal = table.recommendations?.expected_cadence;
  const selected = value || (proposal ? { action: "confirm", ...proposal } : { action: "clear", acknowledged: false });
  const update = (next) => onChange({ ...selected, ...next });
  const differs = proposal && selected.action === "confirm" && (proposal.value?.unit !== selected.value?.unit || Number(proposal.value?.step) !== Number(selected.value?.step));
  return <section className="mt-3 rounded-md border border-slate-200 bg-white p-3"><h4 className="font-semibold text-slate-900">Expected cadence</h4><p className="mt-1 text-xs text-slate-500">Observed cadence is read-only. This is your separately confirmed intended configuration.</p>{proposal ? <p className="mt-2 text-xs text-emerald-700">Recommended from exact regular observation: every {proposal.value?.step} {proposal.value?.unit}.</p> : <p className="mt-2 text-xs text-amber-700">No regular observed cadence proposal is available; a supported intended cadence may still be declared.</p>}<div className="mt-2 flex flex-wrap gap-2"><select aria-label={`Expected cadence action for ${table.table}`} className="h-9 rounded border border-slate-200 px-2 text-sm" value={selected.action} onChange={(event) => update({ action: event.target.value, acknowledged: false })}><option value="confirm">Confirm expected cadence</option><option value="clear">No expected cadence</option><option value="mark_not_applicable">Not applicable</option></select>{selected.action === "confirm" && <><select aria-label={`Expected cadence unit for ${table.table}`} className="h-9 rounded border border-slate-200 px-2 text-sm" value={selected.value?.unit || proposal?.value?.unit || "month"} onChange={(event) => update({ value: { unit: event.target.value, step: Number(selected.value?.step || proposal?.value?.step || 1) }, axis_candidate_id: selected.axis_candidate_id || proposal?.axis_candidate_id, grouping_candidate_id: selected.grouping_candidate_id ?? proposal?.grouping_candidate_id ?? null })}>{["day", "week", "month", "quarter", "year"].map((unit) => <option key={unit}>{unit}</option>)}</select><Input aria-label={`Expected cadence step for ${table.table}`} className="h-9 w-20" type="number" min="1" value={selected.value?.step || proposal?.value?.step || 1} onChange={(event) => update({ value: { unit: selected.value?.unit || proposal?.value?.unit || "month", step: Math.max(1, Number(event.target.value) || 1) }, axis_candidate_id: selected.axis_candidate_id || proposal?.axis_candidate_id, grouping_candidate_id: selected.grouping_candidate_id ?? proposal?.grouping_candidate_id ?? null })} /></>}</div>{selected.action !== "confirm" && <label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={Boolean(selected.acknowledged)} onChange={(event) => update({ acknowledged: event.target.checked })} />I acknowledge this choice.</label>}{differs && <p className="mt-2 text-xs text-amber-700">Your selection differs from the observed dataset structure. It will be preserved as your intended configuration, and applicable diagnostics will ask you to confirm the difference before execution.</p>}</section>;
}

function NoApplicableAction({ table, facet, selection, onSelection }) {
  const actionKey = `${facet.key}_action`, acknowledgementKey = `${facet.key}_acknowledged`;
  if (selection[facet.key]) return null;
  const action = selection[actionKey] || "clear";
  return <div className="mt-2 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900"><label>{facet.title} has no selected candidate. <select aria-label={`${facet.title} no-selection action for ${table.table}`} className="ml-1 rounded border border-amber-200 bg-white px-1" value={action} onChange={(event) => onSelection(actionKey, event.target.value)}><option value="clear">No applicable selection</option><option value="mark_not_applicable">Not applicable</option></select></label><label className="mt-1 flex items-center gap-1"><input type="checkbox" checked={Boolean(selection[acknowledgementKey])} onChange={(event) => onSelection(acknowledgementKey, event.target.checked)} />I acknowledge this decision.</label></div>;
}

function TableReview({ itemId, table, selections, onSelection }) {
  const warningCodes = table.warnings || [];
  const observedCadences = facetCandidates(table, "observed_cadences").items;
  return <section className="mt-4 rounded-lg border border-slate-200 bg-slate-50/60 p-4" aria-labelledby={`structure-table-${table.table}`}>
    <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 id={`structure-table-${table.table}`} className="font-semibold text-slate-950">{table.table}</h3><p className="text-xs text-slate-500">Review state: {statusLabel(table.state)}</p></div></div>
    <div className="mt-4 grid gap-3 xl:grid-cols-3">{FACETS.map((facet) => <CandidateSelect key={facet.key} itemId={itemId} table={table} facet={facet} value={selections[facet.key]} onChange={(candidateId) => onSelection(facet.key, candidateId)} />)}</div>
    {FACETS.map((facet) => <NoApplicableAction key={`${facet.key}-action`} table={table} facet={facet} selection={selections} onSelection={onSelection} />)}
    <section className="mt-3 rounded-md border border-slate-200 bg-white p-3" aria-label={`Observed cadence for ${table.table}`}><h4 className="font-semibold text-slate-900">Observed cadence</h4><p className="mt-1 text-xs text-slate-500">Read-only measurement from the dataset.</p>{observedCadences.length ? <ul className="mt-2 space-y-1 text-sm text-slate-700">{observedCadences.map((candidate) => <li key={candidate.candidate_id || candidate.display_label}>{candidate.display_label}{candidateEvidenceSummary(candidate) ? ` · ${candidateEvidenceSummary(candidate)}` : ""}</li>)}</ul> : <p className="mt-2 text-sm text-slate-500">No observed cadence evidence is available.</p>}</section>
    <ExpectedCadence table={table} value={selections.expected_cadence} onChange={(value) => onSelection("expected_cadence", value)} />
    {warningCodes.length > 0 && <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"><p className="flex items-center gap-1 font-medium"><AlertTriangle className="h-4 w-4" /> Review warnings</p><ul className="mt-1 list-disc pl-5 text-xs">{warningCodes.map((warning, index) => <li key={`${safeWarningLabel(warning)}-${index}`}>{safeWarningLabel(warning)}. This choice does not establish authority.</li>)}</ul></div>}
  </section>;
}

export default function DatasetStructureReview({ itemId, onReturnToColumns }) {
  const [review, setReview] = useState(null);
  const [selections, setSelections] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [reconfirmation, setReconfirmation] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saveAttempt, setSaveAttempt] = useState(0);
  const reviewRef = useRef(null);
  const autosaveRef = useRef(createAutosaveCoordinator());
  const decisionRef = useRef(createDecisionCoordinator());

  const loadReview = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const next = await getDatasetStructureReviewV2(itemId);
      reviewRef.current = next;
      setReview(next);
      setSelections(initialStructureSelections(next));
      setDirty(false);
      setReconfirmation(reviewNeedsReconfirmation(next));
      autosaveRef.current = createAutosaveCoordinator();
    } catch (requestError) {
      setError(requestError.message || "Dataset Structure review could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [itemId]);

  useEffect(() => {
    // Defer the initial request one task so this effect only subscribes to the
    // item identity; the asynchronous response owns the UI state transition.
    const timer = window.setTimeout(() => { loadReview(); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadReview]);

  const materialization = review?.materialization;
  const pollStatus = materialization?.status;
  useEffect(() => {
    if (!materialization?.job_id || !REVIEWABLE_MATERIALIZATION_STATUSES.has(pollStatus)) return undefined;
    return createMaterializationPoller({
      jobId: materialization.job_id,
      status: pollStatus,
      retryAfterMs: materialization.retry_after_ms,
      poll: ({ signal }) => getDatasetStructureMaterializationV2(itemId, materialization.job_id, { signal }),
      reload: loadReview,
      update: (current) => setReview((previous) => previous ? { ...previous, materialization: { ...previous.materialization, ...current } } : previous),
      onError: (pollError) => setError(pollError.message || "Materialization status could not be refreshed."),
    });
  }, [itemId, loadReview, materialization?.job_id, materialization?.retry_after_ms, pollStatus]);

  useEffect(() => {
    if (!dirty || !review) return undefined;
    const timer = window.setTimeout(async () => {
      const operation = autosaveRef.current.begin(draftPayload(reviewRef.current, selections));
      if (!operation) return;
      setSaving(true);
      setSaveError("");
      try {
        const response = await patchDatasetStructureDraftV2(itemId, operation.payload, operation.idempotencyKey);
        const current = reviewRef.current;
        const next = { ...current, draft: { ...(current?.draft || {}), revision: response.draft_revision ?? current?.draft?.revision, evidence_fingerprint: response.evidence_fingerprint || current?.draft?.evidence_fingerprint, selections: response.preserved_selections || current?.draft?.selections } };
        reviewRef.current = next;
        setReview(next);
        setReconfirmation(reviewNeedsReconfirmation(next, response.status));
        const completion = autosaveRef.current.finish(operation);
        if (completion.hasNewerEdits) setSaveAttempt((currentAttempt) => currentAttempt + 1);
        else setDirty(false);
      } catch (saveFailure) {
        autosaveRef.current.finish(operation);
        setSaveError(`${saveFailure.message || "Draft could not be saved."} Your selections are still retained on this screen; retrying will not discard them.`);
      } finally { setSaving(false); }
    }, 650);
    return () => window.clearTimeout(timer);
  }, [dirty, itemId, review, saveAttempt, selections]);

  const assistance = useMemo(() => insufficientAssistance(review), [review]);
  const metadataIncompatible = review?.source_metadata_incompatible || (review?.tables || []).some((table) =>
    (table.warnings || []).some((warning) => String(warning?.code || warning) === "source_metadata_incompatible"));
  const retry = async () => {
    setError("");
    try { await retryDatasetStructureMaterializationV2(itemId); await loadReview(); }
    catch (retryError) { setError(retryError.message || "Materialization retry could not be requested."); }
  };
  const changeSelection = (tableName, key, candidateId) => {
    setSelections((current) => ({ ...current, [tableName]: { ...(current[tableName] || {}), [key]: candidateId } }));
    autosaveRef.current.markDirty();
    setDirty(true);
  };
  const confirmStructure = async () => {
    const payload = decisionPayload(reviewRef.current, selections);
    const operation = decisionRef.current.begin(payload);
    setConfirming(true); setSaveError("");
    try {
      const response = await postDatasetStructureDecisionsV2(itemId, operation.payload, operation.idempotencyKey);
      if (response.status === "needs_reconfirmation") {
        setSelections(initialStructureSelections({ ...reviewRef.current, draft: { ...reviewRef.current.draft, selections: response.preserved_selections, revision: response.draft_revision, evidence_fingerprint: response.evidence_fingerprint } }));
        setReconfirmation(true); await loadReview();
      } else { await loadReview(); }
    } catch (failure) { setSaveError(`${failure.message || "Confirmation could not be saved."} Your selections remain on this screen; retrying uses the same confirmation request.`); }
    finally { setConfirming(false); }
  };
  const confirmEnabled = canConfirmStructure(review, selections, { saving: saving || confirming, dirty, metadataIncompatible });

  return <StepCard step="6" title="Dataset Structure Review" subtitle="Review evidence-backed recommendations. Selections are autosaved as an unconfirmed draft." testId="upl-step-6">
    {loading && <p className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />Loading structural evidence…</p>}
    {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">{error}<Button className="ml-3" size="sm" variant="outline" onClick={loadReview}>Retry</Button></div>}
    {!loading && review && <>
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm"><span><strong>Materialization:</strong> {statusLabel(review.materialization?.status || review.structure_review_state)}</span><span><strong>Evidence freshness:</strong> {review.materialization?.freshness || "unavailable"}{review.materialization?.generation != null ? ` · generation ${review.materialization.generation}` : ""}</span>{["failed", "revoked"].includes(review.materialization?.status) && <Button size="sm" variant="outline" onClick={retry}><RefreshCw className="h-4 w-4" />Retry materialization</Button>}</div>
      {REVIEWABLE_MATERIALIZATION_STATUSES.has(review.materialization?.status) && <p className="mt-3 flex items-center gap-2 text-sm text-slate-600"><Loader2 className="h-4 w-4 animate-spin" />Materializing structural evidence in the background. This view refreshes automatically.</p>}
      {!REVIEWABLE_MATERIALIZATION_STATUSES.has(review.materialization?.status) && <><p className="mt-4 rounded-md border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-950">{selectionMessage(review, selections)}</p>
        {reconfirmation && <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">Evidence changed while this draft was saved. Your selections are preserved and remain editable; review the changed evidence before any later confirmation.</div>}
        {assistance.length > 0 && <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950"><strong>The selected details are insufficient for certain diagnostics: {assistance.map((entry) => entry.diagnostic_name || entry.diagnostic_id).join(", ")}.</strong> Resolve the following requirements to enable them: {assistance.flatMap((entry) => entry.requirements || [entry.message]).filter(Boolean).join("; ")}. Other diagnostics are unaffected.</div>}
        {(review.tables || []).map((table) => <TableReview key={table.table} itemId={itemId} table={table} selections={selections[table.table] || {}} onSelection={(key, candidateId) => changeSelection(table.table, key, candidateId)} />)}
        <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-slate-600">{saving ? <><Loader2 className="h-4 w-4 animate-spin" />Saving draft…</> : dirty ? "Draft changes are queued to save." : <><CheckCircle2 className="h-4 w-4 text-emerald-700" />Draft saved. These choices are not confirmed decisions.</>}{saveError && <span className="w-full rounded-md border border-red-200 bg-red-50 p-2 text-red-800" role="alert">{saveError} <Button size="sm" variant="outline" onClick={() => setSaveAttempt((currentAttempt) => currentAttempt + 1)}>Retry save</Button></span>}</div>
        <div className="mt-4 rounded-md border border-indigo-200 bg-indigo-50 p-3"><p className="text-sm text-indigo-950">{review.structure_review_state === "confirmed" ? "These selections are supported by the dataset evidence. They will be used to prefill applicable diagnostics, and you can review them before execution." : "Confirming records these selections as Dataset Structure decisions. It does not run a diagnostic."}</p><Button className="mt-3" disabled={!confirmEnabled} onClick={confirmStructure}>{confirming ? <><Loader2 className="h-4 w-4 animate-spin" />Confirming…</> : "Confirm dataset structure"}</Button>{!confirmEnabled && <p className="mt-2 text-xs text-slate-600">Save pending changes and resolve required selections or metadata warnings before confirmation.</p>}</div>
      </>}
      {metadataIncompatible && <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950"><p>Source metadata is incompatible. Your draft is retained while column definitions are corrected.</p><Button className="mt-2" size="sm" variant="outline" onClick={() => onReturnToColumns?.(review.return_to_column_definitions || review.metadata_return || {})}>Return to column definitions</Button></div>}
    </>}
  </StepCard>;
}
