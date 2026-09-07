import { useMemo, useState } from "react";
import {
  AlertTriangle, ArrowLeft, ArrowRight, BookPlus, Brain, Check, ChevronDown,
  ChevronRight, GitBranch, Play, RotateCcw, Target,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import PopulationBuilder from "@/components/analysis/PopulationBuilder";
import { getDirectionalitySplitOptionsV2 } from "@/api/client";
import {
  aiSuggestionDraft, bulkEligibleScope, candidateSelectionDraft, expectedBucket,
  governedExactDecisionState, hasUsableAiSuggestion, initialRelationshipReviewOpen,
  isBulkEligibleRole, needsAiSuggestion,
  quickAcceptanceDraft, REFERENCE_DIRECTION_LABELS, requestAiSuggestionsSequentially,
  reviewSuggestionSummary, scopeCandidates, suggestedScope, visibleCandidates,
} from "./directionalityWorkflow";

const CAUTION = "Text similarity is only a search aid. Confirm the economic meaning and representation before using a suggested concept.";
const FILTERS = [
  ["ALL", "All"], ["NEEDS_REVIEW", "Needs review"], ["INCREASING", "Increasing"],
  ["DECREASING", "Decreasing"], ["NON_MONOTONIC", "Non-monotonic"],
  ["NO_CLEAR_DIRECTION", "No clear direction"], ["NOT_APPLICABLE", "Not applicable"],
  ["EXCLUDED", "Excluded"],
];

function Step({ number, title, complete, children }) {
  return <section className="rounded-xl border border-slate-200 bg-white p-4">
    <header className="mb-3 flex items-center gap-3">
      <span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold ${complete ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
        {complete ? <Check className="h-4 w-4" /> : number}
      </span>
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
    </header>
    {children}
  </section>;
}

function ChoiceCard({ selected, disabled, title, description, note, onClick, icon: Icon }) {
  return <button type="button" disabled={disabled} onClick={onClick}
    className={`rounded-lg border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${selected ? "border-dq-purple bg-dq-purple/5 ring-1 ring-dq-purple/20" : "border-slate-200 bg-white hover:border-slate-300"}`}>
    <span className="flex items-start justify-between gap-3"><span className="flex items-center gap-2"><Icon className="h-4 w-4 text-slate-500" /><strong className="text-sm text-slate-900">{title}</strong></span>{selected && <Badge>Selected</Badge>}</span>
    <span className="mt-2 block text-xs leading-5 text-slate-600">{description}</span>
    {note && <span className="mt-2 block text-[11px] leading-4 text-slate-500">{note}</span>}
  </button>;
}

function OrientationToggle({ value, busy, onChange }) {
  return <div>
    <span className="flex items-baseline gap-1 text-xs font-medium text-slate-600">Target risk direction <span className="text-red-600">*</span><small className="font-normal text-slate-400">Required</small></span>
    <div className="mt-1 grid max-w-md grid-cols-2 rounded-lg bg-slate-100 p-1" role="group" aria-label="Reference contract">
      {["HIGHER_IS_WORSE", "HIGHER_IS_BETTER"].map((option) => <button key={option} type="button" disabled={busy}
        aria-pressed={value === option} onClick={() => onChange(option)}
        className={`rounded-md px-3 py-2 text-xs font-medium transition ${value === option ? "bg-white text-slate-950 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}>
        {REFERENCE_DIRECTION_LABELS[option]}
      </button>)}
    </div>
    <p className="mt-1 text-[11px] text-slate-500">For binary targets, “higher value” refers to the selected event class. For continuous targets, it refers to a larger numeric value. This choice never changes the KB expectation.</p>
  </div>;
}

function ScopeFeatureCard({ feature, checked, disabled, onToggle }) {
  const bulkEligible = isBulkEligibleRole(feature.role);
  return <button type="button" disabled={disabled || !feature.numeric} onClick={onToggle}
    className={`min-h-32 rounded-lg border p-3 text-left disabled:cursor-not-allowed disabled:opacity-55 ${checked ? "border-dq-purple bg-dq-purple/5 ring-1 ring-dq-purple/20" : "border-slate-200 bg-white"}`}>
    <span className="flex min-w-0 items-start gap-2"><strong className="min-w-0 flex-1 break-words text-sm text-slate-900">{feature.feature}</strong><span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${checked ? "border-dq-purple bg-dq-purple text-white" : "border-slate-300 bg-white"}`}>{checked && <Check className="h-3.5 w-3.5" />}</span></span>
    <span className="mt-1 line-clamp-2 block text-xs leading-5 text-slate-500">{feature.description || "No saved description"}</span>
    <span className="mt-2 flex flex-wrap gap-1"><Badge variant="secondary">{feature.role || "Role not assigned"}</Badge>{!bulkEligible && <Badge variant="secondary">Manual selection only</Badge>}{feature.reused_decision?.reused_from_completed_run && <Badge>Prior decision retained</Badge>}{feature.classification_source === "KB_V0_3_EXACT" && <Badge>KB match</Badge>}</span>
  </button>;
}

const DIRECTION_LABELS = {
  INCREASING: "Higher feature value → Higher risk",
  DECREASING: "Higher feature value → Lower risk",
  NON_MONOTONIC: "Non-monotonic relationship",
  NO_CLEAR_DIRECTION: "No clear expected direction",
  NOT_APPLICABLE: "Not applicable",
  EXCLUDED: "Excluded from this run",
};

function AiSuggestionSummary({ feature, changed, busy, onRestore }) {
  const output = feature.adjudication?.result?.output;
  if (!output || !feature.classification_source?.startsWith("LLM_")) return null;
  const matched = output.decision === "MATCH";
  const unavailable = feature.adjudication?.status === "unavailable";
  const linkedCandidate = (feature.candidates || []).find(
    (item) => item.canonical_feature === output.selected_candidate,
  );
  const outcome = {
    NO_CANDIDATE_MATCH: "No suitable KB match found",
    NOT_DIRECTIONAL: "No numeric direction suggested",
    INSUFFICIENT_CONTEXT: "More context is needed",
  }[output.decision];
  return <div className="mb-3 rounded border border-indigo-200 bg-indigo-50 p-3 text-xs text-indigo-950">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="flex items-start gap-2"><Brain className="mt-0.5 h-3.5 w-3.5 shrink-0" /><div><strong>{unavailable ? "Previous AI suggestion" : matched ? "AI match found" : "AI review result"}</strong>{matched ? <p className="mt-0.5 text-indigo-800">Linked Knowledge Base concept · <strong>{output.selected_candidate}</strong></p> : <p className="mt-0.5 text-indigo-800">{outcome}</p>}</div></div>
      <Badge variant="secondary">{unavailable ? "Previous result · review required" : "Confirmation required"}</Badge>
    </div>
    <div className="mt-2 rounded border border-indigo-100 bg-white/80 px-3 py-2">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-indigo-500">{matched ? "Why AI linked it" : "AI rationale"}</span>
      <p className="mt-1 leading-5">{output.reason}</p>
      {matched && linkedCandidate?.rationale && <><span className="mt-2 block text-[10px] font-semibold uppercase tracking-wide text-indigo-500">Knowledge Base rationale</span><p className="mt-1 leading-5">{linkedCandidate.rationale}</p></>}
      {(matched || output.decision === "NOT_DIRECTIONAL") && <><span className="mt-2 block text-[10px] font-semibold uppercase tracking-wide text-indigo-500">Proposed direction</span><p className="mt-1 font-medium leading-5">{feature.expected_direction ? DIRECTION_LABELS[feature.expected_direction] : "Direction not established"}</p></>}
    </div>
    <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
      <p className="max-w-4xl text-[11px] leading-4 text-indigo-700">Review the proposed direction and rationale below, then confirm or change them. If this match does not fit, open <strong>Review possible KB concepts ({feature.candidates?.length || 0})</strong> and select another. Use <strong>Restore AI suggestion</strong> to return to this proposal before confirming.</p>
      <Button size="sm" variant="outline" disabled={busy || !changed} onClick={onRestore}>{changed ? <RotateCcw className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5" />}{changed ? (unavailable ? "Restore previous AI suggestion" : "Restore AI suggestion") : "AI suggestion selected"}</Button>
    </div>
  </div>;
}

function ExactKbMatchSummary({ feature }) {
  if (feature.classification_source !== "KB_V0_3_EXACT" || !feature.canonical_feature) return null;
  const linkedCandidate = (feature.candidates || []).find(
    (item) => item.canonical_feature === feature.canonical_feature,
  );
  return <div className="mb-3 rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-950"><div className="flex flex-wrap items-center gap-2"><Check className="h-3.5 w-3.5" /><strong>Knowledge Base match found</strong><span>Linked Knowledge Base concept · <strong>{feature.canonical_feature}</strong></span></div>{(linkedCandidate?.rationale || feature.rationale) && <p className="mt-1 leading-5 text-emerald-800">{linkedCandidate?.rationale || feature.rationale}</p>}</div>;
}

function knowledgeProposalStatus(proposal) {
  if (!proposal?.materialized) {
    return "Knowledge Base proposal queued. It will be deduplicated when this run starts; any required publish or archive decision remains with a Knowledge Base reviewer.";
  }
  if (proposal.proposal_action === "already_governed") {
    return "The same decision is already governed Knowledge Base content. No duplicate proposal or reviewer action was created.";
  }
  if (proposal.proposal_action === "conflict_retained") {
    return "This decision was retained as conflicting evidence on the existing proposal for Knowledge Base reviewer consideration.";
  }
  return "Knowledge Base proposal retained for reviewer action. A reviewer must publish or archive it.";
}

function ReviewRow({ feature, busy, patch, expanded, onToggle }) {
  const initialDirection = feature.expected_direction || "NO_CLEAR_DIRECTION";
  const initialCandidate = feature.canonical_feature || "";
  const initialOrientation = feature.representation_orientation || "SAME";
  const initiallyCoveredByExactKb = governedExactDecisionState(
    feature.governed_exact_decision,
    {
      canonical_feature: initialCandidate || null,
      representation_orientation: initialCandidate ? initialOrientation : null,
      expected_direction: initialDirection,
    },
  ) === "unchanged";
  const [direction, setDirection] = useState(initialDirection);
  const [rationale, setRationale] = useState(feature.rationale || "");
  const [candidate, setCandidate] = useState(initialCandidate);
  const [orientation, setOrientation] = useState(initialOrientation);
  const [includeKb, setIncludeKb] = useState(Boolean(
    feature.kb_proposal?.requested && !feature.kb_proposal?.materialized
      && !initiallyCoveredByExactKb
      && !["NOT_APPLICABLE", "EXCLUDED"].includes(initialDirection),
  ));
  const [localError, setLocalError] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [acceptBusy, setAcceptBusy] = useState(false);
  const candidates = visibleCandidates(feature);
  const originalAiDraft = aiSuggestionDraft(feature);
  const quickDraft = quickAcceptanceDraft(feature);
  const suggestion = reviewSuggestionSummary(feature);
  const aiDraftChanged = Boolean(originalAiDraft && (
    candidate !== originalAiDraft.candidate
    || direction !== originalAiDraft.direction
    || orientation !== originalAiDraft.orientation
    || rationale !== originalAiDraft.rationale
  ));
  const restoreAiSuggestion = () => {
    if (!originalAiDraft) return;
    setCandidate(originalAiDraft.candidate);
    setDirection(originalAiDraft.direction);
    setIncludeKb(false);
    setOrientation(originalAiDraft.orientation);
    setRationale(originalAiDraft.rationale);
    setLocalError("");
  };
  const chooseCandidate = (item) => {
    const draft = candidateSelectionDraft(item);
    setCandidate(draft.candidate);
    setDirection(draft.direction);
    setIncludeKb(false);
    setOrientation(draft.orientation);
    setRationale(draft.rationale);
    setLocalError("");
  };
  const changeOrientation = (nextOrientation) => {
    setOrientation(nextOrientation);
    setIncludeKb(false);
    const selectedCandidate = (feature.candidates || []).find(
      (item) => item.canonical_feature === candidate,
    );
    if (!selectedCandidate || nextOrientation === "UNDETERMINED") return;
    const draft = candidateSelectionDraft({
      ...selectedCandidate,
      representation_evidence: nextOrientation === "INVERSE" ? "inverse" : "normal",
    });
    setDirection(draft.direction);
  };
  const save = async () => {
    setLocalError("");
    try {
      await patch({ kind: "feature_classification", feature: feature.feature,
        expected_direction: direction, rationale, canonical_feature: candidate || null,
        representation_orientation: candidate ? orientation : null,
        include_in_kb: effectiveIncludeKb });
      onToggle(false);
    } catch (error) { setLocalError(error.message); }
  };
  const askAi = async () => {
    setLocalError(""); setAiBusy(true); onToggle(true);
    try { await patch({ kind: "semantic_adjudication", feature: feature.feature }); }
    catch (error) { setLocalError(error.message); }
    finally { setAiBusy(false); }
  };
  const acceptSuggestion = async () => {
    if (!quickDraft) return;
    setLocalError(""); setAcceptBusy(true);
    try {
      await patch({ kind: "feature_classification", feature: feature.feature, ...quickDraft });
      onToggle(false);
    } catch (error) {
      setLocalError(error.message);
      onToggle(true);
    } finally { setAcceptBusy(false); }
  };
  const needsReview = expectedBucket(feature) === "NEEDS_REVIEW";
  const aiUnavailable = feature.adjudication?.status === "unavailable";
  const showAiAction = needsReview && feature.candidates?.length > 0
    && (!hasUsableAiSuggestion(feature) || aiUnavailable);
  const governedDecisionState = governedExactDecisionState(
    feature.governed_exact_decision,
    {
      canonical_feature: candidate || null,
      representation_orientation: candidate ? orientation : null,
      expected_direction: direction,
    },
  );
  const alreadyCoveredByExactKb = governedDecisionState === "unchanged";
  const proposesKbChange = governedDecisionState === "changed";
  const runSpecificDecision = ["NOT_APPLICABLE", "EXCLUDED"].includes(direction);
  const kbProposalEligible = !alreadyCoveredByExactKb
    && !runSpecificDecision;
  const effectiveIncludeKb = includeKb && kbProposalEligible;
  const canQuickAccept = Boolean(quickDraft && !aiDraftChanged && !effectiveIncludeKb);
  return <article className="border-b border-slate-100 last:border-0" data-testid="directionality-feature-card">
    <div className="grid items-center gap-3 px-4 py-3 md:grid-cols-[minmax(0,1.15fr)_minmax(11rem,0.7fr)_minmax(13rem,1fr)_auto]">
      <div className="min-w-0"><div className="flex min-w-0 flex-wrap items-center gap-2"><strong className="min-w-0 break-words text-sm text-slate-900">{feature.feature}</strong><Badge variant="secondary">{feature.role || "Role not assigned"}</Badge>{needsReview && <Badge>Decision required</Badge>}</div><p className="mt-0.5 line-clamp-1 text-xs text-slate-500">{feature.description || "No saved description"}</p></div>
      <div className="min-w-0"><span className="block text-[10px] font-semibold uppercase tracking-wide text-slate-400">{suggestion.label}</span><strong className="mt-0.5 block break-words text-xs text-slate-800" title={suggestion.conceptId ? `KB concept: ${suggestion.conceptId}` : undefined}>{suggestion.concept}</strong>{feature.expected_direction && <span className={`mt-1 block text-[11px] font-medium ${needsReview ? "text-amber-700" : "text-slate-500"}`}>{DIRECTION_LABELS[feature.expected_direction]}</span>}</div>
      <div className="min-w-0"><span className="block text-[10px] font-semibold uppercase tracking-wide text-slate-400">{suggestion.reasonLabel}</span><span className="mt-0.5 line-clamp-2 block text-xs leading-4 text-slate-600" title={suggestion.reason}>{suggestion.reason}</span>{aiUnavailable && <span className="mt-0.5 block text-[10px] text-amber-700">Latest AI retry unavailable · previous work retained</span>}</div>
      <div className="flex flex-wrap justify-end gap-2">{showAiAction && <Button size="sm" variant="outline" disabled={busy || aiBusy || acceptBusy} onClick={askAi}>{aiUnavailable ? <RotateCcw className="h-3.5 w-3.5" /> : <Brain className="h-3.5 w-3.5" />}{aiBusy ? "Requesting…" : aiUnavailable ? "Retry AI" : "Ask AI"}</Button>}{needsReview && canQuickAccept && <Button size="sm" disabled={busy || aiBusy || acceptBusy} onClick={acceptSuggestion}><Check className="h-3.5 w-3.5" />{acceptBusy ? "Accepting…" : feature.adjudication?.result?.output?.decision === "NOT_DIRECTIONAL" ? "Accept as not directional" : aiUnavailable ? "Accept previous suggestion" : "Accept suggestion"}</Button>}<Button size="sm" variant="outline" disabled={busy || aiBusy || acceptBusy} onClick={() => onToggle(!expanded)}>{expanded ? "Close details" : "Review details"}<ChevronRight className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-90" : ""}`} /></Button></div>
    </div>
    {expanded && <div className="border-t border-slate-100 bg-slate-50 px-4 py-4">
      {aiUnavailable && <div className="mb-3 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900"><AlertTriangle className="mr-1 inline h-3.5 w-3.5" /><strong>{feature.adjudication?.result?.output ? "The latest AI retry was unavailable." : "No AI suggestion was applied."}</strong> {feature.adjudication?.result?.output ? "The previous suggestion, candidate matches, and your edits are preserved; continue manually or retry." : "The model service did not return a usable response. Candidate matches and your draft are preserved; continue manually or retry."}</div>}
      <AiSuggestionSummary feature={feature} changed={aiDraftChanged} busy={busy} onRestore={restoreAiSuggestion} />
      <ExactKbMatchSummary feature={feature} />
      {localError && <p className="mb-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">{localError}</p>}
      {!!feature.candidates?.length && <details className="mb-3 rounded border border-amber-200 bg-white p-2"><summary className="cursor-pointer text-xs font-medium text-amber-900">Review possible KB concepts ({feature.candidates.length})</summary><p className="mt-1 text-[11px] text-amber-800">{CAUTION}</p><div className="mt-2 grid gap-1 lg:grid-cols-2">{candidates.map((item) => <button type="button" key={item.canonical_feature} disabled={busy} onClick={() => chooseCandidate(item)} className={`rounded border px-2 py-1 text-left text-xs disabled:cursor-not-allowed disabled:opacity-60 ${candidate === item.canonical_feature ? "border-indigo-400 bg-indigo-50" : "border-slate-200 bg-white"}`}><strong>{item.canonical_feature}</strong><span className="ml-2 text-slate-500">text score {item.combined_similarity_score}</span><small className="block text-slate-500">Matched text: {item.best_matching_kb_text}</small>{item.rationale && <small className="mt-1 block text-slate-600">Direction rationale: {item.rationale}</small>}</button>)}</div>{feature.candidate_display_limit < feature.candidates.length && <Button className="mt-2" size="sm" variant="outline" disabled={busy} onClick={() => patch({ kind: "candidate_display", feature: feature.feature, limit: feature.candidate_display_limit + 3 })}><ChevronDown className="h-3.5 w-3.5" /> Show 3 more</Button>}</details>}
      <div className="grid gap-3 lg:grid-cols-2"><label className="text-xs font-medium text-slate-600">Expected risk direction<select value={direction} disabled={busy} onChange={(event) => { setDirection(event.target.value); setIncludeKb(false); }} className="mt-1 block h-9 w-full rounded border border-slate-300 bg-white px-2 text-sm"><option value="INCREASING">Higher feature value → Higher risk</option><option value="DECREASING">Higher feature value → Lower risk</option><option value="NON_MONOTONIC">Non-monotonic relationship</option><option value="NO_CLEAR_DIRECTION">No clear expected direction</option><option value="NOT_APPLICABLE">Not applicable</option><option value="EXCLUDED">Exclude from this run</option></select></label>
        {candidate && <label className="text-xs font-medium text-slate-600">How this variable relates to the KB concept<select value={orientation} disabled={busy} onChange={(event) => changeOrientation(event.target.value)} className="mt-1 block h-9 w-full rounded border border-slate-300 bg-white px-2 text-sm"><option value="SAME">Moves in the same direction as the KB concept</option><option value="INVERSE">Moves in the opposite direction</option><option value="UNDETERMINED">Direction not established</option></select></label>}
        <label className="text-xs font-medium text-slate-600 lg:col-span-2">Decision rationale<textarea rows="2" value={rationale} disabled={busy} onChange={(event) => setRationale(event.target.value)} className="mt-1 block w-full resize-none rounded border border-slate-300 bg-white px-2 py-1.5 text-sm" placeholder="Explain why this direction is economically appropriate." /></label>
        <div className="flex flex-wrap items-start justify-between gap-3 lg:col-span-2"><div><label className="flex items-center gap-2 text-xs text-slate-600"><input type="checkbox" checked={effectiveIncludeKb} disabled={busy || !kbProposalEligible} onChange={(event) => setIncludeKb(event.target.checked)} /><BookPlus className="h-3.5 w-3.5" /> {proposesKbChange && !runSpecificDecision ? "Propose a Knowledge Base change" : "Save as a Knowledge Base proposal"}</label><p className="mt-1 max-w-3xl text-[11px] leading-4 text-slate-500">{alreadyCoveredByExactKb ? "Already covered by KB v0.3; your confirmation remains part of this run, so no proposal is needed." : runSpecificDecision ? "Not applicable and excluded decisions are specific to this run and cannot be proposed as reusable knowledge." : proposesKbChange ? "This differs from the exact KB v0.3 match. Select it to propose the change for Knowledge Base reviewer consideration when the run starts. Only a reviewer can publish or archive it; this does not change KB v0.3 or the current run." : "When this run starts, the request is deduplicated against open and published knowledge. Only a Knowledge Base reviewer can publish or archive a proposal; this does not change KB v0.3 or the current run."}</p></div><Button size="sm" disabled={busy || !rationale.trim()} onClick={save}><ArrowRight className="h-3.5 w-3.5" /> Confirm expected direction</Button></div>
        {feature.kb_proposal?.requested && !alreadyCoveredByExactKb && <p className="text-xs text-emerald-700 lg:col-span-2">{knowledgeProposalStatus(feature.kb_proposal)}</p>}
      </div>
    </div>}
  </article>;
}

export default function DirectionalityScopeGate({ manifest, busy, patch, runNow }) {
  const savedScope = manifest.scope_features || manifest.features.filter((row) => row.scope_selected).map((row) => row.feature);
  const [selectionDraft, setSelectionDraft] = useState(null);
  const [reviewOpen, setReviewOpen] = useState(() => initialRelationshipReviewOpen(manifest));
  const [filter, setFilter] = useState(() => manifest.features.some(
    (row) => savedScope.includes(row.feature) && expectedBucket(row) === "NEEDS_REVIEW",
  ) ? "NEEDS_REVIEW" : "ALL");
  const [expandedFeature, setExpandedFeature] = useState(null);
  const [targetType, setTargetType] = useState(manifest.reference.type || "auto");
  const [positiveClass, setPositiveClass] = useState(manifest.reference.positive_class ?? "");
  const [bulkAiProgress, setBulkAiProgress] = useState(null);
  const [bulkAiNotice, setBulkAiNotice] = useState(null);
  const [segmentBuilderOpen, setSegmentBuilderOpen] = useState(Boolean(manifest.segment_definition));
  const selected = selectionDraft ?? savedScope;
  const referenceReady = Boolean(manifest.reference.column && manifest.reference.orientation);
  const referenceCandidates = manifest.reference_candidates || [{ column: manifest.reference.column, is_saved_target: true }];
  const segmentCandidates = (manifest.segment_candidates || []).map((row) => typeof row === "string" ? { column: row } : row)
    .filter((row) => row.column !== manifest.reference.column);
  const analysisSequence = manifest.analysis_sequence || {};
  const segmentAnalysisAvailable = Boolean(analysisSequence.segment_analysis_available);
  const selectableFeatures = useMemo(() => scopeCandidates(
    manifest.features, manifest.reference.column, manifest.segment_column,
  ), [manifest.features, manifest.reference.column, manifest.segment_column]);
  const suggested = useMemo(() => suggestedScope(
    manifest.features, manifest.reference.column, manifest.segment_column,
  ), [manifest.features, manifest.reference.column, manifest.segment_column]);
  const bulkEligible = useMemo(() => bulkEligibleScope(
    manifest.features, manifest.reference.column, manifest.segment_column,
  ), [manifest.features, manifest.reference.column, manifest.segment_column]);
  const omittedNonNumeric = useMemo(() => manifest.features.filter((row) => !row.numeric
    && row.feature !== manifest.reference.column && row.feature !== manifest.segment_column),
  [manifest.features, manifest.reference.column, manifest.segment_column]);
  const reviewFeatures = useMemo(() => manifest.features.filter((row) => savedScope.includes(row.feature)), [manifest.features, savedScope]);
  const visibleReview = filter === "ALL" ? reviewFeatures : reviewFeatures.filter((row) => expectedBucket(row) === filter);
  const remainingReviews = reviewFeatures.filter((row) => expectedBucket(row) === "NEEDS_REVIEW").length;
  const confirmedReviews = reviewFeatures.length - remainingReviews;
  const bulkAiFeatures = reviewFeatures.filter(needsAiSuggestion);
  const manualOnlyReviews = reviewFeatures.filter((row) => expectedBucket(row) === "NEEDS_REVIEW"
    && !(row.candidates?.length > 0)).length;
  const workflowBusy = busy || Boolean(bulkAiProgress);
  const aiReviewGuidance = bulkAiProgress || bulkAiFeatures.length > 0
    ? "AI reviews only newly added pending items that do not already have a suggestion. Retained prior-run decisions are not submitted again. Every proposed direction still requires your confirmation."
    : manualOnlyReviews < remainingReviews
      ? "AI suggestions are ready. Confirm or change each decision before running the analysis."
      : "Open Confirm to classify each remaining item manually.";
  const toggleFeature = (name) => setSelectionDraft((draft) => {
    const current = draft ?? savedScope;
    return current.includes(name) ? current.filter((item) => item !== name) : [...current, name];
  });
  const continueToReview = async () => {
    if (!selected.length) return;
    await patch({ kind: "feature_selection", features: selected });
    setSelectionDraft(null);
    setReviewOpen(true);
  };
  const selectReference = async (column) => {
    await patch({ kind: "reference_selection", column });
    setTargetType("auto"); setPositiveClass(""); setSelectionDraft(null); setReviewOpen(false); setSegmentBuilderOpen(false);
  };
  const previewSegment = ({ feature, expression, specialPolicy }) => patch({
    kind: "segment_split", feature, value: expression, special_policy: specialPolicy,
  });
  const askAiForAll = async () => {
    const pending = [...bulkAiFeatures];
    if (!pending.length) return;
    setBulkAiNotice(null);
    const outcome = await requestAiSuggestionsSequentially(
      pending,
      (feature) => patch({ kind: "semantic_adjudication", feature }),
      setBulkAiProgress,
    );
    setBulkAiProgress(null);
    if (outcome.status === "stopped") {
      setBulkAiNotice({ tone: "warning", message: `Bulk AI review stopped while processing ${outcome.failedFeature}. ${outcome.notAttempted} later item${outcome.notAttempted === 1 ? " was" : "s were"} not attempted; retry individually or start the bulk action again.` });
      return;
    }
    setBulkAiNotice(outcome.unavailable
      ? { tone: "warning", message: `AI suggestions were added for ${outcome.added} of ${outcome.total} items. ${outcome.unavailable} could not be completed; retry them individually or review them manually.` }
      : { tone: "success", message: `AI suggestions were added for ${outcome.added} items. Review and confirm each one before running the analysis.` });
  };

  if (reviewOpen) return <section className="grid gap-4" data-testid="directionality-scope-gate">
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><h2 className="text-base font-semibold text-slate-950">Confirm expected risk directions</h2><div className="flex gap-2"><Button size="sm" variant="outline" disabled={workflowBusy} onClick={() => setReviewOpen(false)}><ArrowLeft className="h-4 w-4" /> Change setup or columns</Button><Button size="sm" disabled={workflowBusy || !manifest.ready_to_run} onClick={runNow}><Play className="h-4 w-4" /> Run analysis</Button></div></div>
      <div className="mt-3 grid gap-2 md:grid-cols-3">
        <div className="flex gap-2 rounded-md bg-slate-50 px-3 py-2"><span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-700">1</span><div><strong className="text-xs text-slate-800">Review the match</strong><p className="mt-0.5 text-[11px] leading-4 text-slate-500">Check that the linked Knowledge Base concept describes the variable.</p></div></div>
        <div className="flex gap-2 rounded-md bg-slate-50 px-3 py-2"><span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-700">2</span><div><strong className="text-xs text-slate-800">Confirm the direction</strong><p className="mt-0.5 text-[11px] leading-4 text-slate-500">Example: if higher CLTV increases default risk, choose <strong>Higher feature value → Higher risk</strong>.</p></div></div>
        <div className="flex gap-2 rounded-md bg-slate-50 px-3 py-2"><span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-700">3</span><div><strong className="text-xs text-slate-800">Complete every variable</strong><p className="mt-0.5 text-[11px] leading-4 text-slate-500">Confirm the suggestion, choose another KB concept, or classify it manually.</p></div></div>
      </div>
      <p className="mt-2 rounded-md bg-indigo-50 px-3 py-2 text-[11px] leading-4 text-indigo-800"><strong>Next: Run analysis.</strong> The diagnostic compares each confirmed expectation with the observed empirical direction for the overall portfolio and any selected segments, then flags mismatches for RCA.</p>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md bg-slate-50 px-3 py-2 text-xs"><span className="text-slate-500">Target <strong className="ml-1 text-slate-800">{manifest.reference.column} · {REFERENCE_DIRECTION_LABELS[manifest.reference.orientation]}</strong></span><span className="text-slate-500">View <strong className="ml-1 text-slate-800">{manifest.segment_column || "Overall portfolio"}</strong></span><span className="text-slate-500">Progress <strong className="ml-1 text-slate-800">{confirmedReviews} of {reviewFeatures.length} confirmed</strong></span></div>
      {remainingReviews > 0 && <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-900"><div><strong>{remainingReviews} decision{remainingReviews === 1 ? "" : "s"} remaining.</strong> {aiReviewGuidance}{bulkAiProgress && <span className="ml-1 text-amber-700" aria-live="polite">Getting AI suggestions · {bulkAiProgress.current} of {bulkAiProgress.total} — {bulkAiProgress.feature}.</span>}{manualOnlyReviews > 0 && <span className="ml-1 text-amber-700">{manualOnlyReviews} item{manualOnlyReviews === 1 ? " has" : "s have"} no KB candidates and must be reviewed manually.</span>}{expandedFeature && bulkAiFeatures.length > 0 && <span className="ml-1 text-amber-700">Close the open variable review before starting the bulk AI request so local edits are not lost.</span>}</div>{(bulkAiFeatures.length > 0 || bulkAiProgress) && <Button size="sm" variant="outline" disabled={workflowBusy || Boolean(expandedFeature)} onClick={askAiForAll}><Brain className="h-3.5 w-3.5" />{bulkAiProgress ? `Getting suggestions ${bulkAiProgress.current} of ${bulkAiProgress.total}` : `Ask AI for ${bulkAiFeatures.length} pending item${bulkAiFeatures.length === 1 ? "" : "s"}`}</Button>}</div>}
      {bulkAiNotice && remainingReviews > 0 && <p className={`mt-2 rounded border px-3 py-2 text-xs ${bulkAiNotice.tone === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}>{bulkAiNotice.message}</p>}
      {!remainingReviews && <p className="mt-2 rounded bg-emerald-50 px-3 py-2 text-xs text-emerald-800"><strong>Expected directions confirmed.</strong> Review any optional changes, then run the empirical analysis.</p>}
      <div className="mt-3 flex flex-wrap gap-2">{FILTERS.map(([key, label]) => { const count = key === "ALL" ? reviewFeatures.length : reviewFeatures.filter((row) => expectedBucket(row) === key).length; return <button type="button" key={key} onClick={() => setFilter(key)} className={`rounded-full border px-3 py-1 text-xs font-medium ${filter === key ? "border-dq-purple bg-dq-purple text-white" : "border-slate-200 bg-white text-slate-600"}`}>{label} · {count}</button>; })}</div>
    </div>
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white"><header className="border-b border-slate-200 bg-slate-50 px-4 py-2"><h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">{filter === "ALL" ? "All selected variables" : FILTERS.find(([key]) => key === filter)?.[1]} · {visibleReview.length}</h3></header>{visibleReview.map((feature) => <ReviewRow key={`${feature.feature}:${feature.classification_source}:${feature.expected_direction}:${feature.adjudication?.attempts?.length || 0}:${feature.governed_exact_decision?.canonical_feature || ""}:${feature.governed_exact_decision?.representation_orientation || ""}:${feature.governed_exact_decision?.expected_direction || ""}`} feature={feature} busy={workflowBusy} patch={patch} expanded={expandedFeature === feature.feature} onToggle={(open) => setExpandedFeature(open ? feature.feature : null)} />)}{!visibleReview.length && <div className="p-8 text-center text-sm text-slate-500">No selected variables match this filter.</div>}</div>
  </section>;

  return <section className="grid gap-4" data-testid="directionality-scope-gate">
    {manifest.prior_run_reuse?.reused_feature_count > 0 && <div className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-xs text-indigo-950"><strong>Prior run details retained.</strong> {manifest.prior_run_reuse.reused_feature_count} previously selected variable{manifest.prior_run_reuse.reused_feature_count === 1 ? "" : "s"}, their confirmed directions, and target setup were carried forward from completed run <code>{manifest.prior_run_reuse.source_run_id}</code>. Review the complete workflow and adjust the setup as needed. This rerun will calculate new empirical evidence without making new AI requests for retained variables; Ask AI remains available for newly added unresolved variables.</div>}
    <Step number="1" title="Define the target and analysis view" complete={referenceReady}>
      <p className="mb-3 text-xs text-slate-500">Choose the outcome to assess, map higher target values to risk, and decide whether to analyse the overall portfolio or selected segments.</p>
      <div className="overflow-hidden rounded-lg border border-slate-200">
        <section className="p-3">
          <header className="mb-3 flex flex-wrap items-start justify-between gap-2"><div><div className="flex items-center gap-2"><Target className="h-4 w-4 text-slate-500" /><h3 className="text-sm font-semibold text-slate-900">Target for this analysis</h3></div><p className="mt-0.5 text-xs text-slate-500">Use the saved target or select a run-level substitute.</p></div><Badge variant="secondary">{manifest.reference.source}</Badge></header>
          <div className="grid gap-3 lg:grid-cols-[minmax(15rem,0.8fr)_minmax(26rem,1.2fr)]">
            <label className="block text-xs font-medium text-slate-600">Target column<select value={manifest.reference.column} disabled={busy} onChange={(event) => selectReference(event.target.value)} className="mt-1 block h-9 w-full rounded border border-slate-300 bg-white px-2 text-sm">{referenceCandidates.map((row) => <option key={row.column} value={row.column}>{row.column}{row.is_saved_target ? " · saved target" : " · substitute"}</option>)}</select></label>
            <OrientationToggle value={manifest.reference.orientation} busy={busy} onChange={(orientation) => patch({ kind: "reference_orientation", orientation })} />
          </div>
          <details className="mt-3 rounded border border-slate-200 bg-slate-50 px-3 py-2"><summary className="cursor-pointer text-xs font-medium text-slate-700">Target type and event class</summary><div className="mt-2 flex flex-wrap gap-2"><select value={targetType} onChange={(event) => setTargetType(event.target.value)} className="h-9 rounded border border-slate-300 bg-white px-2 text-xs"><option value="auto">Detect automatically</option><option value="binary">Binary</option><option value="continuous">Continuous</option></select><input className="h-9 w-36 rounded border border-slate-300 bg-white px-2 text-xs" value={positiveClass} onChange={(event) => setPositiveClass(event.target.value)} placeholder="Positive class" /><Button size="sm" variant="outline" disabled={busy} onClick={() => patch({ kind: "target_config", target_type: targetType, positive_class: positiveClass || null })}>Save</Button></div></details>
        </section>
        <section className="border-t border-slate-200 bg-slate-50/60 p-3">
          <header className="mb-3 flex flex-wrap items-start justify-between gap-2"><div><div className="flex items-center gap-2"><GitBranch className="h-4 w-4 text-slate-500" /><h3 className="text-sm font-semibold text-slate-900">Optional segment analysis</h3></div><p className="mt-0.5 text-xs text-slate-500">{segmentAnalysisAvailable ? "A completed overall run is available. Keep the overall view or choose one field to add segment-level results to this rerun." : "The first run establishes the overall portfolio result. Segment analysis becomes available on the next run."}</p></div>{!manifest.segment_column && <Badge variant="secondary">Overall portfolio</Badge>}</header>
          {!segmentAnalysisAvailable ? <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2"><div><strong className="text-xs text-slate-800">First run · overall portfolio</strong><span className="ml-2 text-xs text-slate-500">Complete this run to unlock the PSI-style segment builder for a rerun.</span></div><Badge className="border-transparent bg-emerald-100 text-emerald-800">Selected</Badge></div> : !segmentCandidates.length ? <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2"><div><strong className="text-xs text-amber-900">No eligible split fields found</strong><span className="ml-2 text-xs text-amber-800">A non-constant field other than the target or an identifier is required.</span></div><Badge variant="secondary">Overall portfolio</Badge></div> : <div className="grid gap-3">
            <div className="grid gap-2 md:grid-cols-2"><ChoiceCard icon={GitBranch} title="Overall portfolio only" selected={!segmentBuilderOpen && !manifest.segment_definition} disabled={busy} onClick={async () => { await patch({ kind: "segment_selection", segment_column: null }); setSegmentBuilderOpen(false); }} description="Calculate the full-portfolio directionality result only." note="No rows are separated for segment-level analysis." /><ChoiceCard icon={GitBranch} title="Analyse a selected segment" selected={segmentBuilderOpen || Boolean(manifest.segment_definition)} disabled={busy} onClick={() => setSegmentBuilderOpen(true)} description="Use the same one-dataset split builder as PSI." note="The selected side is analysed; its complement is explicitly not analysed." /></div>
            {(segmentBuilderOpen || manifest.segment_definition) && <div className="rounded-md border border-slate-200 bg-white p-3"><PopulationBuilder
              features={segmentCandidates} disabled={busy} busy={busy}
              preview={manifest.segment_preview} confirmedDefinition={manifest.segment_definition}
              loadOptions={(feature) => getDirectionalitySplitOptionsV2(manifest.run_id, feature)}
              onPreview={previewSegment} baselineLabel="Analysed segment" currentLabel="Not analysed" />
              <p className="mt-2 text-[11px] leading-4 text-slate-500">The overall portfolio is still calculated first. Only the <strong>Analysed segment</strong> receives an additional directionality result; the complementary sample is retained in the split audit but is not tested.</p></div>}
          </div>}
        </section>
      </div>
    </Step>
    <Step number="2" title="Select columns for directionality analysis" complete={savedScope.length > 0}>
      {!referenceReady ? <p className="text-xs text-slate-500">Choose the reference contract above to unlock column selection.</p> : <>
        <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-600"><strong className="text-slate-800">Why string and other non-numeric features are not shown.</strong> This diagnostic requires a meaningful numeric order to evaluate Spearman correlation, regression direction, and binned trends. Assigning arbitrary numbers to category labels could create a false directional relationship. Use a governed numeric or ordinal encoding, or a categorical-association diagnostic, when those features need analysis.
          {!!omittedNonNumeric.length && <details className="mt-2 overflow-hidden rounded-md border border-slate-200 bg-white"><summary className="cursor-pointer px-3 py-2 font-medium text-slate-700">Non-numeric columns not analysed · {omittedNonNumeric.length}</summary><div className="max-h-52 overflow-y-auto border-t border-slate-100"><div className="hidden grid-cols-[minmax(0,1fr)_minmax(7rem,0.35fr)_minmax(7rem,0.35fr)_minmax(10rem,0.6fr)] gap-2 bg-slate-50 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400 md:grid"><span>Column</span><span>Role</span><span>Type</span><span>Reason</span></div>{omittedNonNumeric.map((feature) => <div key={feature.feature} className="grid gap-1 border-t border-slate-100 px-3 py-2 text-xs md:grid-cols-[minmax(0,1fr)_minmax(7rem,0.35fr)_minmax(7rem,0.35fr)_minmax(10rem,0.6fr)] md:gap-2"><strong className="min-w-0 break-words text-slate-800">{feature.feature}</strong><span><small className="text-slate-400 md:hidden">Role · </small>{feature.role || "Not assigned"}</span><span><small className="text-slate-400 md:hidden">Type · </small>{feature.data_type || "Unknown"}</span><span className="text-slate-500"><small className="text-slate-400 md:hidden">Reason · </small>No meaningful numeric order</span></div>)}</div></details>}
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-2"><Button size="sm" variant="outline" onClick={() => setSelectionDraft(suggested)}>Suggested KB matches</Button><Button size="sm" variant="outline" onClick={() => setSelectionDraft(bulkEligible.map((row) => row.feature))}>Select all eligible</Button><Button size="sm" variant="outline" onClick={() => setSelectionDraft([])}>Clear</Button><span className="text-xs text-slate-500">{selected.length} selected · non-selected columns will be ignored</span></div>
        <div className="max-h-[31rem] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-3"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{selectableFeatures.map((feature) => <ScopeFeatureCard key={feature.feature} feature={feature} checked={selected.includes(feature.feature)} disabled={busy} onToggle={() => toggleFeature(feature.feature)} />)}</div></div>
        <div className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3"><div><strong className="text-sm text-slate-900">{selected.length} column(s) selected</strong><p className="text-xs text-slate-500">Continue to confirm the expected economic relationship for each selected column.</p></div><Button disabled={busy || !selected.length} onClick={continueToReview}>Continue to relationship review <ChevronRight className="h-4 w-4" /></Button></div>
      </>}
    </Step>
  </section>;
}
