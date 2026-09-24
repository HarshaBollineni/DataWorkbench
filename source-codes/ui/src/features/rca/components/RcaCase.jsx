import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Brain, Check, CheckCircle2, ChevronDown, Circle,
  BookOpen, ClipboardCheck, Download, FileSearch, History, Lightbulb, Lock, MessageSquare,
  Play, RotateCcw, Search, Send, Ticket, Code2, FlaskConical, ChartNoAxesColumnIncreasing, Copy,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import RcaSourceEvidence from "@/features/rca/components/RcaSourceEvidence";
import RcaProgress from "./RcaProgress";
import { formatDisplayNumber } from "@/lib/numberFormat";
import { presentationMetrics, metricValue, metricChange } from "@/features/rca/resultPresentation";
import { candidateLabel, hypothesisGroups } from "@/features/rca/hypothesisGroups";
import {
  approveRcaConclusion, addRcaInvestigationContext, askRcaDataChat, composeRcaHypothesis, continueRcaFromInitialReview, createRcaCase, getRcaCase,
  downloadAnalysisArtifactV2, downloadRcaReport, exploreRcaHypothesis,
  proposeRcaReusableKnowledge,
  returnRcaToInvestigation, runRcaConfirmationCheck, runRcaCoveragePass1,
  runRcaCoveragePass2, runRcaLook, runRcaOpeningLook, runRcaPlannerLook,
  runRcaReopenedKillAttempt, raiseIssueV2, reviewRcaHypothesis, startRcaAfresh,
} from "@/api/client";

const RCA_PAGES = [
  { id: "intake", label: "Intake" },
  { id: "initial-review", label: "Initial Review" },
  { id: "investigate", label: "Investigate" },
  { id: "closure", label: "Closure" },
];
const INITIAL_STATES = new Set(["created", "triage", "intake"]);
const CONCLUSION_STATES = new Set(["awaiting_fix_approval", "all_hypotheses_rejected", "closed", "unresolved"]);
const NATURAL_ORDER = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const TEMPORAL_COLUMNS = [
  "year", "origination_year", "origination_cohort", "reporting_year",
  "reporting_quarter", "quarter", "reporting_month", "month", "date", "period",
];
const MAX_VISIBLE_EVIDENCE_ROWS = 12;

function stageFor(state) {
  if (INITIAL_STATES.has(state)) return "Intake";
  if (["opening_looks", "initial_review_complete"].includes(state)) return "Initial Review";
  if (CONCLUSION_STATES.has(state)) return "Conclusion approval";
  return "Investigate";
}

function outcomeLabel(closure) {
  if (!closure) return null;
  return closure.outcome === "unresolved" ? "RCA complete — Unresolved" : "RCA complete — Root cause identified";
}

function ageLabel(createdAt) {
  const elapsed = Date.now() - new Date(createdAt).getTime();
  if (!Number.isFinite(elapsed) || elapsed < 0) return "New";
  const days = Math.floor(elapsed / 86400000);
  if (days) return `${days}d`;
  const hours = Math.floor(elapsed / 3600000);
  return hours ? `${hours}h` : "<1h";
}

function StagePath({ current, complete, onSelect }) {
  const active = RCA_PAGES.findIndex((page) => page.id === current);
  return <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" aria-label="RCA pages">
    {RCA_PAGES.map((page, index) => {
      const done = complete || index < active;
      const selected = index === active;
      return <li key={page.id}><button type="button" onClick={() => onSelect(page.id)} aria-current={selected ? "step" : undefined} className={`w-full rounded-md border px-3 py-1.5 text-left ${selected ? "border-teal-600 bg-teal-50" : "border-slate-200 bg-white hover:border-teal-300"}`}>
        <span className="flex items-center gap-2 text-xs font-semibold">
          {done ? <Check className="h-4 w-4 text-emerald-600" /> : selected ? <Circle className="h-4 w-4 fill-teal-600 text-teal-600" /> : <Circle className="h-4 w-4 text-slate-300" />}
          <span className={selected ? "text-slate-950" : "text-slate-500"}>{index + 1}. {page.label}</span>
        </span>
      </button></li>;
    })}
  </ol>;
}

function displayValue(value) {
  if (value == null || value === "") return "Not available";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return formatDisplayNumber(value, { fallback: "Not available" });
  if (Array.isArray(value)) return value.join(", ");
  return String(value).replaceAll("_", " ");
}

function planParameterValue(key, value) {
  if (key === "target_spec" && value && typeof value === "object") {
    const candidates = value.candidate_columns || [];
    return [
      `${displayValue(value.target_mode)} target on ${displayValue(value.affected_column || "affected field")}`,
      value.target_bin_id ? `bin ${value.target_bin_id}` : null,
      candidates.length ? `${candidates.length} eligible predictors` : null,
    ].filter(Boolean).join("; ");
  }
  if (value && typeof value === "object") return JSON.stringify(value);
  return displayValue(value);
}

function flattenedMetrics(value, prefix = "", depth = 0) {
  if (!value || typeof value !== "object" || Array.isArray(value) || depth > 3) return [];
  return Object.entries(value).flatMap(([key, item]) => {
    const label = prefix ? `${prefix} / ${key}` : key;
    if (item == null || ["string", "number", "boolean"].includes(typeof item)) return [[label, item]];
    return flattenedMetrics(item, label, depth + 1);
  }).slice(0, 24);
}

function EvidenceRowsTable({ rows = [] }) {
  const ranked = rows.some((row) => /contribut|rank|top/i.test(String(row?.section || "")));
  const sortColumns = TEMPORAL_COLUMNS.filter((column) => rows.some((row) => row?.[column] != null));
  const orderedRows = ranked || !sortColumns.length ? rows : [...rows].sort((left, right) => {
    for (const column of sortColumns) {
      const comparison = NATURAL_ORDER.compare(String(left?.[column] ?? ""), String(right?.[column] ?? ""));
      if (comparison) return comparison;
    }
    return 0;
  });
  const displayedRows = orderedRows.slice(0, MAX_VISIBLE_EVIDENCE_ROWS);
  const columns = [...new Set(displayedRows.flatMap((row) => Object.keys(row || {})))].slice(0, 12);
  if (!displayedRows.length || !columns.length) return null;
  return <div className="mt-3 w-full max-w-full overflow-x-auto rounded border border-emerald-200 bg-white" data-testid="rca-evidence-table">
    <table className="min-w-full text-left text-xs">
      <thead className="bg-emerald-50 text-slate-700"><tr>{columns.map((column) => <th key={column} className="whitespace-nowrap border-b border-emerald-200 px-3 py-2 font-semibold">{displayValue(column)}</th>)}</tr></thead>
      <tbody>{displayedRows.map((row, index) => <tr key={`${row.section || "evidence"}-${index}`} className="border-b border-slate-100 last:border-0">
        {columns.map((column) => <td key={column} className="max-w-64 break-words px-3 py-2 align-top text-slate-700">{displayValue(row?.[column])}</td>)}
      </tr>)}</tbody>
    </table>
  </div>;
}

function analysisTitle(look) {
  if (look.sql_or_helper_ref === "profile_column") return "Profile the affected feature";
  if (look.sql_or_helper_ref === "segment_breakdown") return "Compare the result across segments";
  if (look.fork_json?.kind === "agent_driver_search") return "Numerical driver discovery";
  if (look.fork_json?.agent_runtime) return "Agent-planned hypothesis test";
  return "Review supporting evidence";
}

function evidenceLabel(entry) {
  const labels = {
    case_context_created: "Intake context retained",
    static_initial_review: "Static initial review",
    llm_initial_review: "LLM initial review",
    human_decision: "Human decision",
    workflow_reset: "RCA started afresh",
    agent_investigation_plan: "Agent investigation plan",
    library_search: "Approved-library search",
    code_generation: "Investigation code generation",
    sandbox_execution: "Sandbox execution",
    agent_interpretation: "Agent evidence interpretation",
    sandbox_output: "Downloadable sandbox output",
    driver_target_definition: "Driver-search target definition",
    human_context: "Investigation context added",
    investigation_plan_cancelled: "Investigation plan superseded",
    combined_hypothesis_run: "Hypothesis run: discovery and confirmation",
    data_chat_user_message: "Data-chat question",
    data_chat_plan: "Data-chat plan",
    data_chat_library_search: "Data-chat library search",
    data_chat_code_generation: "Data-chat code generation",
    data_chat_analysis_execution: "Data-chat helper execution",
    data_chat_sandbox_execution: "Data-chat sandbox execution",
    data_chat_sandbox_output: "Downloadable data-chat output",
    data_chat_assistant_message: "Data-chat answer",
  };
  return `${labels[entry.evidence_kind] || displayValue(entry.evidence_kind)} · ${displayValue(entry.status)}`;
}

function HypothesisReviewControls({ hypothesis, busy = false, onInvestigate, progress }) {
  const [editing, setEditing] = useState(false);
  const [comment, setComment] = useState("");
  const [statement, setStatement] = useState(hypothesis.statement || "");
  const [evidenceBasis, setEvidenceBasis] = useState(hypothesis.evidence_basis || "");
  const [proposedTest, setProposedTest] = useState(
    hypothesis.proposed_test || hypothesis.testable_next_step || ""
  );
  const ready = statement.trim() && evidenceBasis.trim() && proposedTest.trim();
  const investigate = () => onInvestigate(hypothesis.hypothesis_id, {
    comment: comment.trim(), statement: statement.trim(),
    evidence_basis: evidenceBasis.trim(), proposed_test: proposedTest.trim(),
  });
  return <div className="mt-3 border-t border-slate-200 pt-3">
    {editing && <div className="grid gap-3 rounded-md bg-slate-50 p-3">
      <label className="text-xs font-semibold text-slate-600">Hypothesis statement<textarea aria-label="Hypothesis statement" maxLength={4000} className="mt-1 min-h-16 w-full rounded-md border border-slate-200 bg-white p-3 text-sm font-normal" value={statement} onChange={(event) => setStatement(event.target.value)} /></label>
      <label className="text-xs font-semibold text-slate-600">Evidence interpretation<textarea aria-label="Evidence interpretation" maxLength={8000} className="mt-1 min-h-16 w-full rounded-md border border-slate-200 bg-white p-3 text-sm font-normal" value={evidenceBasis} onChange={(event) => setEvidenceBasis(event.target.value)} /></label>
      <label className="text-xs font-semibold text-slate-600">Confirmation test<textarea aria-label="Confirmation test" maxLength={8000} className="mt-1 min-h-16 w-full rounded-md border border-slate-200 bg-white p-3 text-sm font-normal" value={proposedTest} onChange={(event) => setProposedTest(event.target.value)} /></label>
      <label className="text-xs font-semibold text-slate-600">Your context or comment (optional)<textarea aria-label="Hypothesis context or comment" maxLength={4000} className="mt-1 min-h-16 w-full rounded-md border border-slate-200 bg-white p-3 text-sm font-normal" value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Add domain context, a correction, or a condition the investigation should consider." /></label>
      <p className="text-xs text-slate-500">The original remains in the AAR. Any edits create a linked reviewed version, and your comment is retained with a timestamp.</p>
    </div>}
    <div className="mt-3 flex flex-wrap gap-2">
      <Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing((value) => !value)}>{editing ? "Cancel revision" : "Add context or revise"}</Button>
      <Button size="sm" disabled={busy || !ready} onClick={investigate}><Lightbulb className="h-4 w-4" /> {progress ? "Preparing investigation…" : "Investigate this hypothesis"} <ArrowRight className="h-4 w-4" /></Button>
    </div>
    {progress && <div className="mt-2">{progress}</div>}
  </div>;
}

function LlmInitialReviewCard({ event, candidates = [], canSelect = false, busy = false, onInvestigate, progressFor }) {
  if (!event) return <article className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-500">No LLM review has been recorded for this RCA generation.</article>;
  if (event.status === "failed") return <article className="rounded-lg border border-amber-200 bg-amber-50 p-4"><div className="flex items-center gap-2 text-sm font-semibold text-amber-950"><AlertTriangle className="h-4 w-4" /> LLM review unavailable</div><p className="mt-1 text-xs text-amber-800">The deterministic evidence is preserved. Continue manually or start afresh after the model service is available.</p></article>;
  const details = event.details || {};
  const output = details.output || {};
  const model = details.selected_model || {};
  const displayedCandidates = candidates.length
    ? candidates.map((candidate) => ({
        ...candidate,
        testable_next_step: candidate.proposed_test,
        persisted: true,
      }))
    : output.candidate_hypotheses || [];
  return <article className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-4" data-testid="rca-llm-initial-review">
    <div className="flex flex-wrap items-start justify-between gap-2"><div className="flex items-center gap-2"><Brain className="h-4 w-4 text-indigo-700" /><div><h4 className="text-sm font-semibold text-slate-900">LLM evidence review</h4><p className="text-xs text-slate-500">Advisory interpretation; it does not establish the root cause.</p></div></div><Badge variant="outline">{model.model_name || "Configured model"}{model.model_version ? ` · ${model.model_version}` : ""}</Badge></div>
    <p className="mt-3 text-sm text-slate-700">{output.summary}</p>
    {output.observed_signals?.length > 0 && <div className="mt-3"><h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Observed signals</h5><ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-slate-700">{output.observed_signals.map((value) => <li key={value}>{value}</li>)}</ul></div>}
    {displayedCandidates.length > 0 && <div className="mt-3"><h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Candidate hypotheses</h5><p className="mt-1 text-xs text-slate-500">Choose the explanation the agent should investigate first. Selection is retained as a human decision and does not establish root cause.</p><div className="mt-2 grid gap-2">{displayedCandidates.map((hypothesis) => {
      const selected = hypothesis.lifecycle_status === "selected";
      return <div key={hypothesis.hypothesis_id || `${hypothesis.statement}:${hypothesis.testable_next_step}`} className={`rounded border p-3 text-xs text-slate-700 ${selected ? "border-indigo-400 bg-indigo-50" : "border-transparent bg-white"}`} data-testid={hypothesis.hypothesis_id ? `rca-hypothesis-${hypothesis.hypothesis_id}` : undefined}>
        <HypothesisIdentity id={hypothesis.hypothesis_id} label={candidateLabel(displayedCandidates.indexOf(hypothesis))} />
        <div className="flex flex-wrap items-start justify-between gap-2"><strong className="text-slate-900">{hypothesis.statement}</strong>{selected && <Badge variant="success">Selected</Badge>}</div>
        <p className="mt-1">Basis: {hypothesis.evidence_basis}</p><p className="mt-1 text-indigo-800">Next test: {hypothesis.testable_next_step}</p>
        {hypothesis.persisted && canSelect && <HypothesisReviewControls hypothesis={hypothesis} busy={busy} onInvestigate={onInvestigate} progress={progressFor?.(`hypothesis-${hypothesis.hypothesis_id}`)} />}
      </div>;
    })}</div></div>}
    {output.limitations?.length > 0 && <p className="mt-3 text-xs text-slate-500"><strong>Limitations:</strong> {output.limitations.join(" ")}</p>}
    <p className="mt-3 text-[11px] text-slate-400">AAR {event.artifact_id} · {event.recorded_at}</p>
  </article>;
}

function interpretation(summary = {}) {
  if (summary.agent_runtime && summary.runtime?.ok === false) {
    const runtime = summary.runtime;
    const reason = runtime.error || runtime.errors?.join(" ") || "The sandbox did not return an approved result.";
    return `Analysis did not complete: ${reason}`;
  }
  if (summary.agent_runtime && summary.result?.summary) return summary.result.summary;
  if (summary.diagnostic?.kind === "population_stability_index") {
    const diagnostic = summary.diagnostic;
    const driver = diagnostic.dominant_driver;
    const profile = summary.profile || {};
    const driverText = driver
      ? ` The largest contribution is ${driver.bin_label || displayValue(driver.bin)}: baseline ${(Number(driver.baseline_proportion || 0) * 100).toFixed(2)}% versus current ${(Number(driver.current_proportion || 0) * 100).toFixed(2)}%, contributing ${Number(driver.contribution || 0).toFixed(4)} (${(Number(driver.absolute_contribution_share || 0) * 100).toFixed(1)}% of absolute PSI contribution).`
      : "";
    const specialValues = profile.special_values_confirmed
      ? profile.declared_special_values || profile.normalized_special_values || []
      : [];
    const profileText = profile.mean != null
      ? ` The governed ${displayValue(profile.profile_basis || "profile")} mean is ${Number(profile.mean).toFixed(4)}${specialValues.length ? ` after excluding confirmed special values ${specialValues.join(", ")}` : ""}.`
      : "";
    const scopeText = summary.profile_population_scope === "combined_baseline_and_current_source_snapshot"
      ? " The general profile covers the combined baseline and current source snapshot."
      : "";
    return `PSI is ${Number(diagnostic.psi).toFixed(4)} for ${diagnostic.feature || summary.column || "the affected feature"}.${driverText}${profileText}${scopeText}`;
  }
  if (summary.null_share_by_segment) {
    const highest = Object.entries(summary.null_share_by_segment).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
    return highest
      ? `${highest[0]} has the highest observed missing share (${(Number(highest[1]) * 100).toFixed(1)}%). This concentration may indicate a segment-specific process or source difference.`
      : "No material segment difference was found.";
  }
  if (summary.null_share != null) {
    return `${summary.column || "The affected feature"} is ${(Number(summary.null_share) * 100).toFixed(1)}% missing with ${displayValue(summary.distinct)} distinct values. This establishes the opening data shape for comparison.`;
  }
  return "The analysis completed and its governed result is retained as supporting evidence.";
}

function HypothesisIdentity({ id, number, label }) {
  const [notice, setNotice] = useState("");
  return <span className="inline-flex max-w-full flex-wrap items-center gap-x-2 gap-y-1 text-xs">
    <strong>{label || `Hypothesis ${number}`}</strong><code className="break-all font-normal text-slate-500">{id}</code>
    {id && <button type="button" aria-label={`Copy hypothesis ID ${id}`} className="inline-flex items-center gap-1 text-slate-500 hover:text-teal-700" onClick={async (event) => {
      event.preventDefault(); event.stopPropagation();
      try { await navigator.clipboard.writeText(id); setNotice("Copied"); }
      catch { setNotice("Select the ID to copy manually"); }
    }}><Copy aria-hidden="true" className="h-3 w-3" />Copy ID</button>}
    {notice && <span role="status" className="text-slate-500">{notice}</span>}
  </span>;
}

function HypothesisContext({ busy, onSave, intake = false }) {
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  return <details className="mt-2 text-xs" data-testid="rca-add-hypothesis-context">
    <summary className="cursor-pointer font-semibold text-indigo-700"><MessageSquare aria-hidden="true" className="mr-1 inline h-3.5 w-3.5" />Add context</summary>
    <form className="mt-2 space-y-2" onSubmit={async (event) => {
      event.preventDefault(); if (!comment.trim() || busy) return;
      setError(""); setSaved(false);
      try { await onSave(comment.trim()); setComment(""); setSaved(true); }
      catch (requestError) { setError(requestError.message || "Context could not be saved."); }
    }}>
      <p className="text-slate-600">{intake ? "Optional: add source changes, business events or corrections. Saved context informs the initial hypothesis review; it does not change the diagnostic evidence." : "Context informs the next test of this hypothesis. Saving replaces an unexecuted plan; completed results are unchanged."}</p>
      <label className="block font-medium">{intake ? "Your context" : "Hypothesis context"}<textarea aria-label={intake ? "Your context" : "Hypothesis context"} className="mt-1 min-h-20 w-full rounded border border-slate-200 bg-white p-2 font-normal" maxLength={4000} value={comment} onChange={(event) => setComment(event.target.value)} disabled={busy} /></label>
      <Button type="submit" size="sm" disabled={busy || !comment.trim()}>{busy ? "Saving…" : "Save context"}</Button>
      {error && <p role="alert" className="text-red-700">{error}</p>}
      {saved && <p role="status" className="text-teal-800">{intake ? "Context saved for the initial review." : "Context saved. Choose an exploration action to run the next test using this context."}</p>}
    </form>
  </details>;
}

function EvidenceCard({ look, execution, interpretationEvent, latest = true }) {
  const [downloadError, setDownloadError] = useState("");
  const summary = execution?.summary_json || {};
  const agentPlan = look.fork_json?.agent_runtime ? look.fork_json : null;
  const retainedCode = agentPlan?.execution_artifact?.implementation_source
    || agentPlan?.generated_code?.python_code;
  const runtime = summary.runtime || {};
  const result = summary.result || runtime.result || {};
  const interpretationDetails = interpretationEvent?.details?.interpretation;
  const runtimeFailed = agentPlan && runtime.ok === false;
  const isDriverSearch = agentPlan?.kind === "agent_driver_search";
  const metrics = presentationMetrics(execution?.presentation);
  const associationOnly = isDriverSearch && agentPlan?.combined_run_state;
  const supported = !associationOnly && interpretationDetails?.assessment === "supported";
  const AssessmentIcon = supported ? CheckCircle2 : Search;
  const runtimeError = runtime.error || runtime.errors?.join(" ") || "The sandbox did not return an approved result.";
  const downloadFullResult = async () => {
    setDownloadError("");
    try {
      const artifact = await downloadAnalysisArtifactV2(result.download_artifact_id);
      const payload = JSON.parse(await artifact.text());
      const text = JSON.stringify(payload?.details?.result ?? payload, null, 2);
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = result.download_filename || `${look.look_id}-sandbox-output.txt`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setDownloadError(error.message || "The full result could not be downloaded.");
    }
  };
  return <details open={latest || !execution} className="group/record rounded-lg border border-slate-200 bg-white p-3" data-testid="rca-investigation-record">
    <summary className="flex cursor-pointer list-none items-start gap-2 [&::-webkit-details-marker]:hidden">
      <FileSearch aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-teal-700" />
      <div className="flex min-w-0 flex-1 flex-wrap items-start justify-between gap-2">
      <div><h4 className="text-sm font-semibold text-slate-900">{look.display_number ? `${look.display_number} ${look.display_stage} · ` : ""}{analysisTitle(look)}</h4>
        <p className="mt-0.5 text-xs text-slate-500">Question: {agentPlan?.plan?.question || (look.sql_or_helper_ref === "segment_breakdown" ? "Is the observed problem concentrated in a meaningful segment?" : "What does the affected feature look like in the retained snapshot?")}</p></div>
      <Badge variant={runtimeFailed ? "destructive" : execution?.status === "completed" || execution?.status === "done" ? "success" : "secondary"}>{execution ? displayValue(runtimeFailed ? runtime.status : execution.status || "Complete") : "Ready to run"}</Badge>
      </div><ChevronDown aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-slate-400 transition-transform group-open/record:rotate-180" />
    </summary>
    {agentPlan && !execution && <div className="mt-2 grid gap-2 rounded-md bg-slate-50 p-2 text-xs text-slate-700 sm:grid-cols-2" data-testid="rca-agent-plan">
      <p><strong>Selected hypothesis:</strong> {agentPlan.hypothesis}</p>
      <p><strong>Execution:</strong> {agentPlan.execution_mode === "approved_helper" ? `Approved helper · ${look.sql_or_helper_ref}` : "Generated code · isolated sandbox"}</p>
      <p><strong>Supports when:</strong> {agentPlan.plan?.supports_hypothesis_when}</p>
      <p><strong>Rejects when:</strong> {agentPlan.plan?.rejects_hypothesis_when}</p>
    </div>}
    {execution && <>
      {!agentPlan && <p className="mt-3 text-sm text-slate-700">{interpretation(summary)}</p>}
      {agentPlan && <div className="mt-2 flex min-w-0 flex-col gap-2">
        <details className="order-2 min-w-0 rounded bg-slate-50 px-3 py-2" data-testid="rca-executed-code">
          <summary className="cursor-pointer text-xs font-semibold text-slate-600"><FlaskConical aria-hidden="true" className="mr-1.5 inline h-4 w-4" />Investigation method</summary>
          <p className="mt-1 text-xs text-slate-600">{agentPlan.plan?.rationale || agentPlan.generated_code?.rationale}</p>
          <dl className="mt-2 grid min-w-0 gap-2 text-xs sm:grid-cols-2">
            {agentPlan.hypothesis && <div><dt className="font-semibold text-slate-700">Tested hypothesis</dt><dd>{agentPlan.hypothesis}</dd></div>}
            {agentPlan.plan?.supports_hypothesis_when && <div><dt className="font-semibold text-slate-700">Supports when</dt><dd>{agentPlan.plan.supports_hypothesis_when}</dd></div>}
            {agentPlan.plan?.rejects_hypothesis_when && <div><dt className="font-semibold text-slate-700">Rejects when</dt><dd>{agentPlan.plan.rejects_hypothesis_when}</dd></div>}
            {agentPlan.plan?.analysis_kind && <div><dt className="font-semibold text-slate-700">Analysis approach</dt><dd className="break-words text-slate-600">{displayValue(agentPlan.plan.analysis_kind)}</dd></div>}
            {Object.keys(agentPlan.plan?.helper_params || {}).length > 0 && <div><dt className="font-semibold text-slate-700">Inputs and grouping</dt><dd className="break-words text-slate-600">{Object.entries(agentPlan.plan.helper_params).map(([key, value]) => `${displayValue(key)}: ${planParameterValue(key, value)}`).join(" · ")}</dd></div>}
            {agentPlan.plan?.expected_output?.length > 0 && <div><dt className="font-semibold text-slate-700">Expected evidence</dt><dd className="break-words text-slate-600">{agentPlan.plan.expected_output.join("; ")}</dd></div>}
            <div><dt className="font-semibold text-slate-700">Execution control</dt><dd className="text-slate-600">Read-only analysis with bounded output, runtime timeout, and retained audit evidence.</dd></div>
          </dl>
          {agentPlan.execution_mode === "approved_helper" && <p className="mt-1 text-xs text-slate-500">No fresh code was generated. The planner reused <strong>{look.sql_or_helper_ref}</strong> from the governed library.</p>}
          <details className="mt-3 border-t border-slate-200 pt-2 text-xs" data-testid="rca-execution-code">
            <summary className="cursor-pointer font-medium text-slate-600"><Code2 aria-hidden="true" className="mr-1.5 inline h-4 w-4" />View generated / executed code</summary>
            {retainedCode
              ? <pre className="mt-2 max-h-72 w-full overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-[11px] text-slate-100">{retainedCode}</pre>
              : <p className="mt-2 text-slate-500">Source code was not retained for this execution. The helper reference and available parameters remain in the audit record.</p>}
          </details>
        </details>
        <section className={`order-1 min-w-0 overflow-hidden rounded-md border-l-4 p-3 ${runtimeFailed ? "border-red-400 bg-red-50/50" : supported ? "border-teal-500 bg-slate-50" : "border-slate-300 bg-slate-50"}`} data-testid="rca-analysis-result">
          <h5 className={`flex items-center gap-2 text-sm font-semibold ${runtimeFailed ? "text-red-800" : "text-slate-900"}`}>{runtimeFailed ? <AlertTriangle aria-hidden="true" className="h-4 w-4" /> : <AssessmentIcon aria-hidden="true" className="h-4 w-4 text-teal-700" />}{runtimeFailed ? "Execution failed" : supported ? "Explanation supported by evidence" : "What the evidence shows"}</h5>
          {execution?.status === "cancelled"
            ? <p className="mt-2 text-sm text-slate-700">Plan superseded. Choose an exploration action to continue. No analysis was performed.</p>
            : runtimeFailed
            ? <><p className="mt-2 text-sm font-medium text-red-900">{runtimeError}</p><p className="mt-2 text-xs text-red-800">No analytical conclusion was created from this execution. Revise the plan or generated code and run it again.</p></>
            : <>{!interpretationDetails && <p className="mt-2 text-sm font-medium text-slate-900">{result.summary || "Analysis completed. Review the supporting evidence."}</p>}
              {interpretationDetails && <section className="mt-2 text-sm" data-testid="rca-agent-interpretation"><Badge variant={supported ? "success" : "secondary"}>{associationOnly ? "Discovery stage · association evidence" : `Hypothesis assessment · ${displayValue(interpretationDetails.assessment)}`}</Badge><p className="mt-2 leading-relaxed text-slate-800">{interpretationDetails.rationale}</p></section>}
              {metrics.length > 0 && <div className="mt-3 grid gap-2 border-t border-slate-200 pt-2 sm:grid-cols-2" data-testid="rca-key-metrics">{metrics.map((metric) => <div key={metric.label} className="text-xs"><p className="inline-flex items-center gap-1 font-medium text-slate-600"><ChartNoAxesColumnIncreasing aria-hidden="true" className="h-4 w-4" />{metric.label}</p><div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">{metric.comparison && <><span>{metric.comparison.label} <strong>{metricValue(metric.comparison.value, metric.unit)}</strong></span><ArrowRight aria-hidden="true" className="h-3.5 w-3.5 text-slate-400" /></>}<span>{metric.value_label} <strong>{metricValue(metric.value, metric.unit)}</strong></span>{metric.comparison && <span className="text-slate-600">{metricChange(metric)}</span>}</div></div>)}</div>}
              {(result.evidence_rows?.length > 0 || result.model_validation || interpretationDetails?.evidence_points?.length > 0 || (interpretationDetails && result.summary)) && <details className="mt-2 text-xs" data-testid="rca-supporting-evidence"><summary className="cursor-pointer font-semibold text-slate-700"><ChartNoAxesColumnIncreasing aria-hidden="true" className="mr-1.5 inline h-4 w-4" />Supporting evidence</summary>
              {interpretationDetails?.evidence_points?.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 text-slate-600">{interpretationDetails.evidence_points.map((value) => <li key={value}>{value}</li>)}</ul>}
              {interpretationDetails && result.summary && <p className="mt-2 text-slate-700">{result.summary}</p>}
              {result.evidence_rows?.length > 0 && <><h6 className="mt-3 font-medium text-slate-600">{isDriverSearch ? "Baseline/current symptom attribution" : "Evidence rows"} (showing {Math.min(result.evidence_rows.length, MAX_VISIBLE_EVIDENCE_ROWS)} of {result.evidence_rows.length})</h6><EvidenceRowsTable rows={result.evidence_rows} /></>}
              {isDriverSearch && result.model_validation && <details className="mt-3 rounded border border-emerald-200 bg-white p-3 text-xs"><summary className="cursor-pointer font-semibold text-slate-700">Model validation details</summary><dl className="mt-3 grid gap-2 sm:grid-cols-2">{flattenedMetrics(result.model_validation).map(([key, value]) => <div key={key}><dt className="font-semibold text-slate-500">{displayValue(key)}</dt><dd>{displayValue(value)}</dd></div>)}</dl>{result.model_validation.feature_importance?.length > 0 && <div className="mt-3"><EvidenceRowsTable rows={result.model_validation.feature_importance} /></div>}</details>}
              {result.truncation?.evidence_rows_truncated && <p className="mt-2 text-[11px] text-slate-500">Showing the {displayValue(result.truncation.evidence_rows_retained)} most relevant of {displayValue(result.truncation.evidence_rows_available)} generated evidence rows. The bounded result and execution record are retained in the AAR.</p>}
              </details>}
            </>}
        </section>
      </div>}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-slate-100 pt-2">
      {!runtimeFailed && result.download_artifact_id && <button type="button" className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900" onClick={downloadFullResult}><Download aria-hidden="true" className="h-3.5 w-3.5" />Download full output (.txt)</button>}
      <Dialog>
        <DialogTrigger asChild><button type="button" className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800"><History aria-hidden="true" className="h-3.5 w-3.5" />View audit record</button></DialogTrigger>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-auto">
          <DialogTitle>Investigation audit record</DialogTitle>
          <DialogDescription>Retained plan, execution details and available AAR interpretation for reproducibility and troubleshooting.</DialogDescription>
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-3 text-xs text-slate-600">{JSON.stringify({ look, execution, interpretation_event: interpretationEvent || null }, null, 2)}</pre>
        </DialogContent>
      </Dialog>
      </div>
      {downloadError && <p className="mt-2 text-xs text-red-700">{downloadError}</p>}
    </>}
  </details>;
}

function DataChatPanel({ caseId, generation, afterSequence, chat, onUpdated }) {
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const turns = chat?.turns || [];
  const remaining = Math.max(
    0, Number(chat?.required_successful_investigations || 2)
      - Number(chat?.successful_investigation_count || 0)
  );
  if (!chat?.unlocked) return <section className="rounded-lg border border-slate-200 bg-slate-50/70 px-4 py-3" data-testid="rca-data-chat-locked">
    <div className="flex items-start gap-3"><Lock className="mt-0.5 h-4 w-4 text-slate-500" /><div><h3 className="text-sm font-semibold text-slate-800">Ask about this data</h3><p className="mt-0.5 text-xs text-slate-500">Available after two successful hypothesis-test runs. {remaining} more successful {remaining === 1 ? "run is" : "runs are"} required; opening reviews and failed or cancelled runs do not count.</p></div></div>
  </section>;

  const submit = async (event) => {
    event.preventDefault();
    const value = question.trim();
    if (!value || sending) return;
    setSending(true);
    setError("");
    try {
      const next = await askRcaDataChat(caseId, value);
      setQuestion("");
      onUpdated(next);
    } catch (requestError) {
      setError(requestError.message || "The question could not be answered.");
    } finally {
      setSending(false);
    }
  };
  const download = async (turn) => {
    const artifactId = turn.execution?.download_artifact_id
      || turn.execution?.result?.download_artifact_id;
    if (!artifactId) return;
    setDownloadError("");
    try {
      const artifact = await downloadAnalysisArtifactV2(artifactId);
      const url = URL.createObjectURL(artifact.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = turn.execution?.result?.download_filename || artifact.filename
        || `rca-${caseId}-${turn.turn_id}-chat-output.txt`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setDownloadError(requestError.message || "The retained output could not be downloaded.");
    }
  };
  return <section className="rounded-lg border border-cyan-200 bg-cyan-50/30 p-4" data-testid="rca-data-chat">
    <div className="flex flex-wrap items-start justify-between gap-2"><div className="flex items-start gap-2"><MessageSquare className="mt-0.5 h-4 w-4 text-cyan-700" /><div><h3 className="text-sm font-semibold text-slate-900">Ask about this data</h3><p className="text-xs text-slate-500">Answers use retained RCA evidence and at most one governed read-only calculation.</p></div></div><Badge variant="outline">{chat.successful_investigation_count} successful tests</Badge></div>
    {turns.length > 0 && <ol className="mt-4 space-y-4" aria-label="Data chat messages">{turns.map((turn) => {
      const result = turn.execution?.result || {};
      const method = turn.library_search?.selected_helper_id
        ? `Approved helper: ${turn.library_search.selected_helper_id}`
        : turn.generated_code ? "Generated code in guarded sandbox"
          : "Retained evidence only";
      return <li key={turn.turn_id} className="space-y-2">
        <div className="ml-auto max-w-[88%] rounded-lg bg-slate-800 px-3 py-2 text-sm text-white"><p>{turn.question}</p><span className="mt-1 block text-[10px] text-slate-300">{turn.asked_at}</span></div>
        <div className={`max-w-[94%] rounded-lg border px-3 py-2 ${turn.status === "failed" ? "border-red-200 bg-red-50" : "border-cyan-200 bg-white"}`}>
          <p className="text-sm text-slate-800">{turn.answer || (turn.execution?.status ? `Analysis ${displayValue(turn.execution.status)}.` : "Answer preparation is retained in the activity record.")}</p>
          {result.evidence_rows?.length > 0 && <EvidenceRowsTable rows={result.evidence_rows} />}
          {turn.limitations?.length > 0 && <p className="mt-2 text-xs text-slate-500"><strong>Limitations:</strong> {turn.limitations.join(" ")}</p>}
          <details className="mt-2 rounded border border-slate-100 bg-slate-50 px-2 py-1.5 text-xs" data-testid="rca-chat-method"><summary className="cursor-pointer font-semibold text-cyan-800">Evidence and method</summary><div className="mt-2 space-y-1 text-slate-600"><p><strong>Method:</strong> {method}</p>{turn.plan?.rationale && <p><strong>Plan:</strong> {turn.plan.rationale}</p>}{turn.model && <p><strong>Planner:</strong> {turn.model.model_name || "Configured model"}{turn.model.model_version ? ` · ${turn.model.model_version}` : ""}</p>}<p><strong>AAR references:</strong> {(turn.evidence_references || []).join(", ") || turn.assistant_evidence_artifact_id || "Retained with this turn"}</p>{turn.generated_code?.rationale && <p><strong>Generated method:</strong> {turn.generated_code.rationale}</p>}</div></details>
          {turn.generated_code?.python_code && <details className="mt-2 min-w-0 rounded border border-slate-100 bg-slate-50 px-2 py-1.5 text-xs" data-testid="rca-chat-code">
            <summary className="cursor-pointer font-medium text-cyan-800"><Code2 aria-hidden="true" className="mr-1.5 inline h-4 w-4" />View generated code</summary>
            <pre className="mt-2 max-h-72 w-full overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-[11px] text-slate-100">{turn.generated_code.python_code}</pre>
          </details>}
          {(turn.execution?.download_artifact_id || result.download_artifact_id) && <Button className="mt-2" size="sm" variant="outline" onClick={() => download(turn)}><Download className="h-3.5 w-3.5" /> Download full output</Button>}
          <span className="mt-2 block text-[10px] text-slate-400">{turn.answered_at || turn.events?.at(-1)?.recorded_at} · AAR {turn.assistant_evidence_artifact_id || turn.events?.at(-1)?.artifact_id}</span>
        </div>
      </li>;
    })}</ol>}
    {!turns.length && <p className="mt-3 rounded border border-dashed border-cyan-200 bg-white p-3 text-xs text-slate-500">No questions yet. Ask for a concise explanation of retained evidence or one bounded calculation.</p>}
    <div data-testid="rca-chat-action"><form className="mt-4 flex items-end gap-2" onSubmit={submit}><label className="min-w-0 flex-1 text-xs font-semibold text-slate-600">Question<textarea aria-label="Ask about this data" maxLength={2000} rows={2} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="What does the evidence show about…?" className="mt-1 w-full resize-y rounded-md border border-cyan-200 bg-white p-2 text-sm font-normal text-slate-900" /></label><Button type="submit" disabled={sending || !question.trim()}>{sending ? "Analyzing…" : <><Send className="h-4 w-4" /> Ask</>}</Button></form>{sending && <div className="mt-2"><RcaProgress caseId={caseId} generation={generation} afterSequence={afterSequence} operation="Data chat" label="Preparing your answer" /></div>}</div>
    {error && <div className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800" role="alert">{error}</div>}
    {downloadError && <div className="mt-2 text-xs text-red-700" role="alert">{downloadError}</div>}
  </section>;
}

function ConclusionForm({ c, issue, onApprove, onReturn, busy, progress, pendingAction }) {
  const evidenceIds = [
    issue?.source_evidence?.result_id,
    ...(c.aar_evidence || []).map((entry) => entry.artifact_id),
  ].filter(Boolean);
  const suggested = c.confirmed_hypothesis || c.hypotheses?.[0];
  const draft = c.conclusion_draft || {};
  const [form, setForm] = useState({
    conclusion_type: draft.conclusion_type || (c.state === "all_hypotheses_rejected" ? "unresolved" : "root_cause_identified"),
    root_cause: draft.root_cause || suggested?.statement || "",
    confidence: draft.confidence || (suggested?.tier === "strong" ? "High" : suggested?.tier === "weak" ? "Low" : "Moderate"),
    limiting_evidence: draft.limiting_evidence || "",
    alternatives_considered: draft.alternatives_considered || (c.suspects?.length > 1 ? "Other active and ruled-out explanations shown in the investigation record." : "No additional evidence-backed alternative was identified in the available scope."),
    affected_scope: `${issue?.table_name || c.table_name || "Table"}${(issue?.columns || []).length ? ` · ${issue.columns.join(", ")}` : ""}`,
    related_failures: draft.related_failures || "Reviewed against the failures attached to this RCA case.",
    owner: draft.owner || "",
    approval_rationale: draft.approval_rationale || "",
  });
  const [returnReason, setReturnReason] = useState("");
  const set = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const identified = form.conclusion_type === "root_cause_identified";
  const ready = evidenceIds.length > 0 && form.limiting_evidence.trim() && form.alternatives_considered.trim()
    && form.related_failures.trim() && form.approval_rationale.trim()
    && (!identified || form.root_cause.trim());
  const checks = [
    ["Intake problem is recorded", Boolean(c.complaint_text || c.case_file)],
    ["Supporting evidence is cited", evidenceIds.length > 0],
    ["Limits and contradictions are disclosed", Boolean(form.limiting_evidence.trim())],
    ["Reasonable alternatives are recorded", Boolean(form.alternatives_considered.trim())],
    ["Related failures are reconciled", Boolean(form.related_failures.trim())],
    ["Approval rationale is recorded", Boolean(form.approval_rationale.trim())],
  ];
  return <section className="rounded-lg border border-dq-purple/30 bg-white p-5" data-testid="conclusion-approval">
    <div className="flex items-start gap-3"><ClipboardCheck className="mt-0.5 h-5 w-5 text-dq-purple" /><div><h3 className="font-semibold text-slate-950">Proposed conclusion</h3><p className="text-sm text-slate-500">Approve the investigation finding. This does not confirm that the underlying issue was fixed.</p></div></div>
    <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_20rem]">
      <div className="space-y-3">
        <label className="block text-xs font-semibold text-slate-600">Conclusion type<select className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={form.conclusion_type} onChange={(event) => set("conclusion_type", event.target.value)}><option value="root_cause_identified">Root cause identified</option><option value="unresolved">Unresolved</option></select></label>
        {identified && <label className="block text-xs font-semibold text-slate-600">Proposed root cause<textarea className="mt-1 min-h-20 w-full rounded-md border border-slate-200 p-3 text-sm" value={form.root_cause} onChange={(event) => set("root_cause", event.target.value)} /></label>}
        <div className="grid gap-3 sm:grid-cols-2"><label className="block text-xs font-semibold text-slate-600">Confidence<select className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={form.confidence} onChange={(event) => set("confidence", event.target.value)}>{["High", "Moderate", "Low"].map((value) => <option key={value}>{value}</option>)}</select></label><label className="block text-xs font-semibold text-slate-600">Accountable owner (if follow-up is needed)<input className="mt-1 h-10 w-full rounded-md border border-slate-200 px-3 text-sm" value={form.owner} onChange={(event) => set("owner", event.target.value)} placeholder="Owner or team" /></label></div>
        <label className="block text-xs font-semibold text-slate-600">Contradicting or limiting evidence<textarea className="mt-1 min-h-16 w-full rounded-md border border-slate-200 p-3 text-sm" value={form.limiting_evidence} onChange={(event) => set("limiting_evidence", event.target.value)} placeholder="State known limits; enter ‘None identified’ when appropriate." /></label>
        <label className="block text-xs font-semibold text-slate-600">Alternative explanations considered<textarea className="mt-1 min-h-16 w-full rounded-md border border-slate-200 p-3 text-sm" value={form.alternatives_considered} onChange={(event) => set("alternatives_considered", event.target.value)} /></label>
        <label className="block text-xs font-semibold text-slate-600">Approval rationale<textarea className="mt-1 min-h-16 w-full rounded-md border border-slate-200 p-3 text-sm" value={form.approval_rationale} onChange={(event) => set("approval_rationale", event.target.value)} placeholder={identified ? "Why is this conclusion acceptable?" : "Why should this RCA complete unresolved?"} /></label>
      </div>
      <aside className="rounded-md bg-slate-50 p-4"><h4 className="text-xs font-semibold uppercase text-slate-500">Completion check</h4><ul className="mt-3 space-y-2">{checks.map(([label, passed]) => <li key={label} className="flex gap-2 text-xs text-slate-600">{passed ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" /> : <Circle className="h-4 w-4 shrink-0 text-slate-300" />}{label}</li>)}</ul><p className="mt-4 text-[11px] text-slate-500">Approval closes the RCA and issue workflow. It does not create or publish Knowledge Base content.</p></aside>
    </div>
    <div className="mt-4 flex flex-wrap items-center gap-2"><Button disabled={busy || !ready} onClick={() => onApprove({ ...form, supporting_evidence_ids: evidenceIds })}><CheckCircle2 className="h-4 w-4" /> {progress && pendingAction === "approve" ? "Saving conclusion…" : identified ? "Approve root cause" : "Approve unresolved conclusion"}</Button><input className="h-9 min-w-56 flex-1 rounded-md border border-slate-200 px-3 text-xs" value={returnReason} onChange={(event) => setReturnReason(event.target.value)} placeholder="Reason to return to Investigate" /><Button variant="outline" disabled={busy || !returnReason.trim()} onClick={() => onReturn(returnReason.trim())}><RotateCcw className="h-4 w-4" /> {progress && pendingAction === "return" ? "Returning to investigation…" : "Return to Investigate"}</Button></div>
    {progress && <div className="mt-2" data-testid="rca-conclusion-progress">{progress}</div>}
  </section>;
}

function RemediationHandoff({ issue, conclusion }) {
  const [tracked, setTracked] = useState(issue?.tracked || null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    title: `Remediate ${issue?.test_name || "approved RCA finding"}`,
    description: conclusion?.root_cause || "Follow up on the approved RCA conclusion.",
    owner: conclusion?.owner || "", priority: "Medium", target_date: "",
  });
  const set = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const create = async () => { setBusy(true); setError(""); try { const updated = await raiseIssueV2(issue.issue_row_id, form); setTracked(updated.tracked); setOpen(false); } catch (requestError) { setError(requestError.message); } finally { setBusy(false); } };
  return <section className="rounded-lg border border-slate-200 bg-white p-4">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-slate-900">Optional remediation handoff</h3><p className="text-xs text-slate-500">Separate from RCA completion. Its execution does not change the accepted outcome.</p></div>{!tracked && !open && <Button size="sm" variant="outline" onClick={() => setOpen(true)}><Ticket className="h-4 w-4" /> Create tracked remediation</Button>}</div>
    {tracked && <div className="mt-3 rounded-md bg-slate-50 p-3 text-sm"><strong>{tracked.issue_id}</strong><span className="text-slate-500"> · {tracked.status} · {tracked.owner || "Owner not assigned"}{tracked.target_date ? ` · due ${tracked.target_date}` : ""}</span></div>}
    {open && <div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-xs font-semibold text-slate-600 sm:col-span-2">Title<input className="mt-1 h-9 w-full rounded-md border border-slate-200 px-3 text-sm" value={form.title} onChange={(event) => set("title", event.target.value)} /></label><label className="text-xs font-semibold text-slate-600 sm:col-span-2">Recommended action<textarea className="mt-1 min-h-16 w-full rounded-md border border-slate-200 p-3 text-sm" value={form.description} onChange={(event) => set("description", event.target.value)} /></label><label className="text-xs font-semibold text-slate-600">Owner<input className="mt-1 h-9 w-full rounded-md border border-slate-200 px-3 text-sm" value={form.owner} onChange={(event) => set("owner", event.target.value)} /></label><label className="text-xs font-semibold text-slate-600">Priority<select className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={form.priority} onChange={(event) => set("priority", event.target.value)}>{["Critical", "High", "Medium", "Low"].map((value) => <option key={value}>{value}</option>)}</select></label><label className="text-xs font-semibold text-slate-600">Target date<input type="date" className="mt-1 h-9 w-full rounded-md border border-slate-200 px-3 text-sm" value={form.target_date} onChange={(event) => set("target_date", event.target.value)} /></label><div className="flex items-end gap-2"><Button size="sm" disabled={busy || !form.title.trim()} onClick={create}>Create handoff</Button><Button size="sm" variant="outline" onClick={() => setOpen(false)}>Cancel</Button></div></div>}
    {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
  </section>;
}

function KnowledgeProposal({ c, issue, busy, onPropose, progress }) {
  const conclusion = c.conclusion || {};
  const evidenceIds = conclusion.supporting_evidence_ids || [];
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    reusable_lesson: conclusion.root_cause || "",
    applicability_scope: conclusion.affected_scope || issue?.table_name || "",
    generalization_reason: "",
    related_tables: issue?.table_name || "",
    related_diagnostic_id: issue?.diagnostic_id == null ? "" : String(issue.diagnostic_id),
  });
  const set = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  if (c.closure?.outcome === "unresolved") return <section className="rounded-lg border border-slate-200 bg-white p-4"><h3 className="text-sm font-semibold text-slate-900">Reusable knowledge</h3><p className="mt-1 text-xs text-slate-500">No knowledge proposal is available because this RCA closed without an identified root cause.</p></section>;
  if (c.closure?.knowledge_draft_id) return <section className="rounded-lg border border-blue-200 bg-blue-50 p-4"><div className="flex items-center gap-2"><BookOpen className="h-4 w-4 text-blue-700" /><h3 className="text-sm font-semibold text-blue-950">Reusable knowledge proposed</h3></div><p className="mt-1 text-xs text-blue-800">Candidate {c.closure.knowledge_draft_id} is awaiting independent review in Knowledge Base → Learning candidates. It is not published knowledge.</p></section>;
  const ready = form.reusable_lesson.trim() && form.applicability_scope.trim()
    && form.generalization_reason.trim() && evidenceIds.length > 0;
  const submit = () => onPropose({
    reusable_lesson: form.reusable_lesson.trim(),
    applicability_scope: form.applicability_scope.trim(),
    generalization_reason: form.generalization_reason.trim(),
    supporting_evidence_ids: evidenceIds,
    related_tables: form.related_tables.split(",").map((value) => value.trim()).filter(Boolean),
    related_diagnostic_id: form.related_diagnostic_id ? Number(form.related_diagnostic_id) : null,
  }).then((result) => { if (result) setOpen(false); });
  return <section className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><BookOpen className="h-4 w-4 text-dq-purple" /><h3 className="text-sm font-semibold text-slate-900">Optional reusable-knowledge proposal</h3></div><p className="mt-1 text-xs text-slate-500">The RCA is already closed. Use this only when the lesson is applicable beyond this case.</p></div>{!open && <Button size="sm" variant="outline" onClick={() => setOpen(true)}><BookOpen className="h-4 w-4" /> Propose as reusable knowledge</Button>}</div>
    {open && <div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-xs font-semibold text-slate-600 sm:col-span-2">Reusable lesson<textarea className="mt-1 min-h-20 w-full rounded-md border border-slate-200 p-3 text-sm" value={form.reusable_lesson} onChange={(event) => set("reusable_lesson", event.target.value)} /></label><label className="text-xs font-semibold text-slate-600 sm:col-span-2">Applicability and scope<textarea className="mt-1 min-h-16 w-full rounded-md border border-slate-200 p-3 text-sm" value={form.applicability_scope} onChange={(event) => set("applicability_scope", event.target.value)} placeholder="Where and under what conditions does this lesson apply?" /></label><label className="text-xs font-semibold text-slate-600 sm:col-span-2">Why this generalizes beyond this case<textarea className="mt-1 min-h-16 w-full rounded-md border border-slate-200 p-3 text-sm" value={form.generalization_reason} onChange={(event) => set("generalization_reason", event.target.value)} /></label><label className="text-xs font-semibold text-slate-600">Related tables<input className="mt-1 h-9 w-full rounded-md border border-slate-200 px-3 text-sm" value={form.related_tables} onChange={(event) => set("related_tables", event.target.value)} placeholder="Comma-separated" /></label><label className="text-xs font-semibold text-slate-600">Related diagnostic ID<input type="number" className="mt-1 h-9 w-full rounded-md border border-slate-200 px-3 text-sm" value={form.related_diagnostic_id} onChange={(event) => set("related_diagnostic_id", event.target.value)} disabled={issue?.diagnostic_id == null} /></label><div className="rounded-md bg-slate-50 p-3 text-xs text-slate-600 sm:col-span-2"><strong>Supporting evidence retained from closure:</strong> {evidenceIds.join(", ") || "None"}</div><div className="flex gap-2 sm:col-span-2"><Button size="sm" disabled={busy || !ready} onClick={submit}>{busy ? "Submitting..." : "Submit learning candidate"}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>Cancel</Button></div></div>}
    {progress && <div className="mt-2">{progress}</div>}
  </section>;
}

export default function RcaCase({ issueRowId, issue }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [c, setCase] = useState(null);
  const [error, setError] = useState("");
  const [errorLocation, setErrorLocation] = useState("page");
  const initialLoad = useRef(null);
  const [busy, setBusy] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);
  const [reportError, setReportError] = useState("");
  const [progressPage, setProgressPage] = useState(null);
  const [activeAction, setActiveAction] = useState(null);
  const exportReport = async (format = "pdf") => {
    setReportBusy(format);
    setReportError("");
    try { await downloadRcaReport(c.case_id, format); }
    catch (requestError) { setReportError(requestError.message || "Report download failed."); }
    finally { setReportBusy(false); }
  };
  useEffect(() => {
    const requestKey = `${issueRowId}:${issue?.rca_case_id || "new"}`;
    let current = true;
    if (initialLoad.current?.key !== requestKey) initialLoad.current = {
      key: requestKey, promise: issue?.rca_case_id
        ? getRcaCase(issue.rca_case_id) : createRcaCase(issueRowId),
    };
    initialLoad.current.promise.then((result) => { if (current) setCase(result); })
      .catch((requestError) => { if (current) setError(requestError.message); });
    return () => { current = false; };
  }, [issue?.rca_case_id, issueRowId]);
  const act = async (fn, location = "page", targetPage = page, actionName = null) => { setActiveAction(actionName); setProgressPage(targetPage); setBusy(true); setError(""); setErrorLocation(location); try { const result = await fn(); const next = result?.case_id ? result : result?.case || await getRcaCase(c.case_id); setCase(next); return next; } catch (requestError) { if (requestError.rcaCase) setCase(requestError.rcaCase); setError(requestError.message); return null; } finally { setBusy(false); } };
  const executions = useMemo(() => Object.values(c?.executions || {}), [c]);
  const latestExecutedLookId = [...(c?.looks || [])].reverse().find(
    (look) => executions.some((entry) => entry.look_id === look.look_id)
  )?.look_id;
  const aarEvidence = c?.aar_evidence || [];
  const llmInitialReview = [...aarEvidence].reverse().find((entry) => entry.evidence_kind === "llm_initial_review" && entry.status !== "started");
  const hypothesisCandidates = c?.hypothesis_candidates || [];
  const selectedInitialHypothesis = c?.selected_initial_hypothesis
    || hypothesisCandidates.find((candidate) => candidate.lifecycle_status === "selected");
  const focusedHypothesisCandidates = c?.focused_hypothesis_candidates || [];
  const selectedFocusedHypothesis = c?.selected_focused_hypothesis
    || focusedHypothesisCandidates.find((candidate) => candidate.lifecycle_status === "selected");
  const pendingFocusedSelection = focusedHypothesisCandidates.length > 0 && !selectedFocusedHypothesis && c?.active_investigation_hypothesis?.origin !== "alternative_explanation";
  const activeInvestigationHypothesis = c?.active_investigation_hypothesis || selectedFocusedHypothesis || selectedInitialHypothesis;
  const groupedHypotheses = hypothesisGroups(c);
  const latestAgentInterpretation = [...aarEvidence].reverse().find(
    (entry) => entry.evidence_kind === "agent_interpretation" && entry.status === "completed"
  )?.details?.interpretation;
  const hasAgentDiscoveryAttempt = c?.looks?.some(
    (look) => look.kind === "planned" && look.fork_json?.agent_runtime
  );
  const hasAgentExecution = c?.looks?.some((look) => look.fork_json?.agent_runtime && executions.some((entry) => entry.look_id === look.look_id));
  const investigationLimitReached = c?.state === "investigation_loop" && Boolean(c?.investigation_limit?.reached);
  const openLook = c?.looks?.find((look) => look.kind === "planned" && !look.fork_json?.combined_parent_look_id && !executions.some((entry) => entry.look_id === look.look_id));
  const pendingCheck = c?.confirmation_checks?.find((check) => check.status === "pending");
  if (!c) return <div className="rounded-md border border-dashed border-slate-200 p-4 text-sm text-slate-500">{error || "Preparing the RCA case…"}</div>;
  const stage = stageFor(c.state);
  const complete = c.state === "closed" || Boolean(c.closure);
  const defaultPage = complete || CONCLUSION_STATES.has(c.state)
    ? "closure"
    : stage === "Investigate"
      ? "investigate"
      : stage === "Initial Review" ? "initial-review" : "intake";
  const requestedPage = searchParams.get("rca_view");
  const page = RCA_PAGES.some((entry) => entry.id === requestedPage) ? requestedPage : defaultPage;
  const goTo = (nextPage) => setSearchParams((current) => { const next = new URLSearchParams(current); next.set("rca_view", nextPage); return next; });

  const action = (() => {
    if (c.state === "investigation_loop" && openLook) return [openLook.fork_json?.kind === "agent_driver_search" ? "Run hypothesis (discovery + confirmation)" : openLook.fork_json?.execution_mode === "generated_code_sandbox" ? "Run in guarded sandbox" : "Run approved helper", () => runRcaLook(openLook.look_id), Play];
    if (c.state === "investigation_loop" && pendingFocusedSelection) return null;
    if (c.state === "investigation_loop") return [selectedInitialHypothesis
      ? (!hasAgentDiscoveryAttempt ? "Discover potential drivers" : latestAgentInterpretation?.assessment === "inconclusive" ? "Plan required follow-up" : "Generate investigation plan")
      : "Recommend next analysis", () => runRcaPlannerLook(c.case_id), Lightbulb];
    if (c.state === "coverage_challenge_blind") return ["Review alternative explanations", () => runRcaCoveragePass1(c.case_id), Search];
    if (c.state === "coverage_challenge_history") return ["Compare with prior evidence", () => runRcaCoveragePass2(c.case_id), Search];
    if (c.state === "reopened_kill_attempt") return ["Test the remaining alternative", () => runRcaReopenedKillAttempt(c.case_id), Play];
    if (c.state === "hypothesis_composition") return ["Prepare proposed conclusion", () => composeRcaHypothesis(c.case_id), ClipboardCheck];
    if (c.state === "confirmation_checks" && pendingCheck) return ["Test the proposed explanation", () => runRcaConfirmationCheck(pendingCheck.check_id), Play];
    return null;
  })();
  const startInitialReview = async () => {
    goTo("initial-review");
    await act(() => runRcaOpeningLook(c.case_id), "page", "initial-review");
  };
  const continueToInvestigation = async () => {
    goTo("investigate");
    if (c.state === "initial_review_complete") {
      const next = await act(() => continueRcaFromInitialReview(c.case_id), "investigation-action", "investigate");
      if (!next) return;
    }
    goTo("investigate");
  };
  const investigateInitialHypothesis = async (hypothesisId, review) => {
    const next = await act(async () => {
      await reviewRcaHypothesis(c.case_id, hypothesisId, review);
      await continueRcaFromInitialReview(c.case_id);
      return runRcaPlannerLook(c.case_id);
    }, `hypothesis-${hypothesisId}`);
    if (next) goTo("investigate");
  };
  const investigateFocusedHypothesis = (hypothesisId, review) => act(async () => {
    await reviewRcaHypothesis(c.case_id, hypothesisId, review);
    return runRcaPlannerLook(c.case_id);
  }, `hypothesis-${hypothesisId}`);
  const saveHypothesisContext = async (hypothesisId, comment) => {
    setProgressPage(null);
    setBusy(true);
    try { setCase(await addRcaInvestigationContext(c.case_id, comment, hypothesisId)); }
    finally { setBusy(false); }
  };
  const startAfresh = async () => {
    const confirmed = window.confirm(
      "Start this RCA afresh?\n\nAll current analysis, hypotheses, generated code, sandbox results, chat, and draft decisions will be permanently lost. The original issue and source evidence will remain."
    );
    if (!confirmed) return;
    const next = await act(() => startRcaAfresh(c.case_id), "reset");
    if (next) goTo("intake");
  };
  const selectPage = (nextPage) => {
    if (nextPage === "investigate" && ["intake", "opening_looks", "initial_review_complete"].includes(c.state)) {
      goTo("initial-review");
      return;
    }
    goTo(nextPage);
  };

  const progressFor = (location) => busy && progressPage === page && errorLocation === location
    ? <RcaProgress caseId={c.case_id} generation={c.workflow_generation || 1} afterSequence={Math.max(0, ...aarEvidence.map((entry) => entry.sequence_no || 0))} showOpening={page === "initial-review" && location === "page"} /> : null;

  return <div className="space-y-3">
    <section className="rounded-lg border border-slate-200 bg-white p-3" data-testid="rca-workspace-header">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2"><FileSearch aria-hidden="true" className="mt-1 h-5 w-5 shrink-0 text-teal-700" /><div className="min-w-0"><h2 className="text-base font-semibold text-slate-950">{issue?.test_name || c.case_file?.checklist_json?.test_name || "Diagnostic investigation"}</h2><p className="break-words text-xs text-slate-500">{issue?.item_name || "Dataset"} · {issue?.table_name || c.table_name} · {(issue?.columns || []).join(", ") || "assessed scope"}</p></div></div>
        <div className="flex flex-wrap items-center gap-2" data-testid="rca-report-actions"><Badge variant={complete ? "secondary" : "warning"}>{complete ? outcomeLabel(c.closure) || "RCA complete" : "RCA in progress"}</Badge>{issue?.criticality && <Badge variant={issue.criticality === "Critical" ? "destructive" : "secondary"}>{issue.criticality}</Badge>}{complete && <Button size="sm" disabled={reportBusy} onClick={() => exportReport("pdf")}><Download aria-hidden="true" className="h-4 w-4" />{reportBusy === "pdf" ? "Preparing PDF…" : "Download RCA report"}</Button>}{complete && <Button variant="outline" size="sm" disabled={reportBusy} onClick={() => exportReport("text")}>{reportBusy === "text" ? "Preparing text report…" : "Download text report"}</Button>}{reportBusy && <div className="w-full"><RcaProgress pollEnabled={false} label="Preparing report from retained evidence" /></div>}</div>
      </div>
      <details className="mt-2 text-xs text-slate-500" data-testid="rca-header-details"><summary className="cursor-pointer font-medium">Case details</summary><div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-slate-100 pt-2"><span>Issue: {issue?.issue_row_id || issueRowId}</span><span>RCA: {c.case_id}</span>{c.conclusion?.owner && <span>Owner: {c.conclusion.owner}</span>}<span>Age: {ageLabel(c.created_at)}</span>{!complete && c.state !== "intake" && <Button variant="outline" size="sm" disabled={busy} onClick={startAfresh}><RotateCcw className="h-4 w-4" /> {progressFor("reset") ? "Starting afresh…" : "Start afresh"}</Button>}</div>{progressFor("reset")}</details>
      {reportError && <p className="mt-2 text-xs text-red-700" role="alert">{reportError}</p>}
    </section>
    <StagePath current={page} complete={complete} onSelect={selectPage} />
    {error && errorLocation !== "investigation-action" && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
    {page === "intake" && <>
      <RcaSourceEvidence issue={issue}>
        <section className="rounded-lg border border-slate-200 bg-white p-3" data-testid="rca-intake-context">
          {c.state === "intake" && <HypothesisContext intake busy={busy} onSave={(comment) => saveHypothesisContext(null, comment)} />}
          {(c.investigation_context || []).length > 0 && <details className="mt-2 text-xs"><summary className="cursor-pointer font-semibold text-slate-600">Saved context ({c.investigation_context.length})</summary>{c.investigation_context.map((entry) => <p key={entry.id} className="mt-2 text-slate-600">{entry.answer}<span className="block text-slate-400">{entry.answered_at} · {entry.answered_by}</span></p>)}</details>}
        </section>
      </RcaSourceEvidence>
      <nav className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-teal-200 bg-[#f8faf6] p-4" aria-label="Intake decisions"><p className="text-xs text-slate-600">Start with an evidence review to develop testable explanations, or conclude if the retained evidence is sufficient.</p><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy || complete} onClick={() => goTo("closure")}><CheckCircle2 className="h-4 w-4" /> Conclude from available evidence</Button><Button disabled={busy || complete || c.state !== "intake"} onClick={startInitialReview}><FileSearch className="h-4 w-4" /> Start investigation <ArrowRight className="h-4 w-4" /></Button>{c.state !== "intake" && !complete && <Button onClick={() => goTo("initial-review")}>View initial review <ArrowRight className="h-4 w-4" /></Button>}{complete && <Button onClick={() => goTo("closure")}>View closure <ArrowRight className="h-4 w-4" /></Button>}</div></nav>
    </>}
    {page === "initial-review" && <>
      <section data-testid="rca-initial-review-content">{progressFor("page")}<div className="mb-2 flex items-end justify-between"><div><h3 className="font-semibold text-slate-950">Initial review</h3><p className="text-xs text-slate-500">Deterministic opening evidence retained for the static RCA review, followed by a governed LLM interpretation.</p></div><Badge variant="secondary">{executions.filter((entry) => c.looks?.some((look) => look.look_id === entry.look_id && look.kind === "opening")).length} completed</Badge></div>{c.looks?.filter((look) => look.kind === "opening").length ? <div className="grid gap-3">{c.looks.filter((look) => look.kind === "opening").map((look) => <EvidenceCard key={look.look_id} look={look} execution={executions.find((entry) => entry.look_id === look.look_id)} />)}<LlmInitialReviewCard event={llmInitialReview} candidates={hypothesisCandidates} canSelect={c.state === "initial_review_complete"} busy={busy} onInvestigate={investigateInitialHypothesis} progressFor={progressFor} /></div> : <div className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-500">{busy ? "Initial review is running. Findings appear above as they become available." : "The initial review has not run yet. Start it to create the first retained evidence."}</div>}</section>
      <nav className="flex flex-wrap items-center justify-between gap-3"><Button variant="outline" onClick={() => goTo("intake")}><ArrowLeft className="h-4 w-4" /> Intake</Button><div className="flex flex-wrap items-center gap-2">{c.state === "intake" && <Button disabled={busy} onClick={startInitialReview}><FileSearch className="h-4 w-4" /> Start initial review</Button>}{c.state === "initial_review_complete" && <><Button disabled={busy || (hypothesisCandidates.length > 0 && !selectedInitialHypothesis)} onClick={continueToInvestigation}>{hypothesisCandidates.length ? "Investigate selected hypothesis" : "Continue manually to investigation"} <ArrowRight className="h-4 w-4" /></Button>{hypothesisCandidates.length > 0 && !selectedInitialHypothesis && <span className="text-xs text-amber-700">Select one hypothesis to continue.</span>}</>}{!["intake", "opening_looks", "initial_review_complete"].includes(c.state) && <Button onClick={() => goTo("investigate")}>View investigation <ArrowRight className="h-4 w-4" /></Button>}</div></nav>
    </>}
    {page === "investigate" && <>
      <section>
        <div className="grid gap-3">
          {groupedHypotheses.groups.filter((group) => group.looks.length || group.hypothesis_id === activeInvestigationHypothesis?.hypothesis_id).map((group) => {
            const active = group.hypothesis_id === activeInvestigationHypothesis?.hypothesis_id;
            const contexts = aarEvidence.filter((entry) => entry.evidence_kind === "human_context" && (entry.details?.hypothesis_id === group.hypothesis_id || entry.details?.reviewed_hypothesis_id === group.hypothesis_id));
            const finding = [...aarEvidence].reverse().find((entry) => ["agent_interpretation", "combined_hypothesis_run"].includes(entry.evidence_kind) && entry.status === "completed" && (entry.details?.hypothesis_id === group.hypothesis_id || group.looks.some((look) => look.look_id === entry.details?.look_id)))?.details?.interpretation;
            return <details key={group.hypothesis_id} open={active || (!activeInvestigationHypothesis && group.looks.some((look) => look.look_id === latestExecutedLookId))} className="rounded-lg border border-indigo-200 bg-indigo-50/20 p-3" data-testid="rca-hypothesis-group">
              <summary className="cursor-pointer text-slate-800"><HypothesisIdentity id={group.hypothesis_id} number={group.number} />{finding && <Badge className="ml-2" variant={finding.assessment === "supported" ? "success" : "secondary"}>{displayValue(finding.assessment)}</Badge>}<span className="mt-1 block text-sm font-semibold">{group.statement || "Statement not retained"}</span></summary>
              {finding && <p className="mt-2 border-l-4 border-teal-500 bg-slate-50 p-3 text-sm text-slate-800" data-testid="rca-hypothesis-finding">{finding.rationale}</p>}
              {active && !complete && c.state === "investigation_loop" && <HypothesisContext busy={busy || c.looks?.some((look) => look.fork_json?.combined_run_state === "running")} onSave={(comment) => saveHypothesisContext(group.hypothesis_id, comment)} />}
              {contexts.length > 0 && <details className="mt-2 text-xs" data-testid="rca-hypothesis-context"><summary className="cursor-pointer font-medium">User-added context ({contexts.length})</summary>{contexts.map((entry) => <p key={entry.artifact_id} className="mt-1">{entry.details?.comment} · {entry.recorded_at}</p>)}</details>}
              <details className="mt-3" data-testid="rca-hypothesis-tests"><summary className="cursor-pointer text-xs font-semibold text-slate-600">Tests and evidence ({group.looks.length})</summary><div className="mt-2 grid gap-2">{group.looks.map((look) => <EvidenceCard key={look.look_id} look={look} latest={look.look_id === latestExecutedLookId} execution={executions.find((entry) => entry.look_id === look.look_id)} interpretationEvent={[...aarEvidence].reverse().find((entry) => entry.evidence_kind === "agent_interpretation" && entry.status === "completed" && entry.details?.look_id === look.look_id)} />)}</div></details>
            </details>;
          })}
          {groupedHypotheses.unassigned.length > 0 && <details className="rounded-lg border border-slate-200 p-3" data-testid="rca-background-evidence"><summary className="cursor-pointer text-xs font-semibold text-slate-600">Background evidence</summary><div className="grid gap-2">{groupedHypotheses.unassigned.map((look) => <EvidenceCard key={look.look_id} look={look} latest={look.look_id === latestExecutedLookId} execution={executions.find((entry) => entry.look_id === look.look_id)} interpretationEvent={[...aarEvidence].reverse().find((entry) => entry.evidence_kind === "agent_interpretation" && entry.status === "completed" && entry.details?.look_id === look.look_id)} />)}</div></details>}
          {!c.looks?.length && <p className="text-sm text-slate-500">No additional analysis has been requested.</p>}
        </div>
      </section>
      {pendingFocusedSelection && !investigationLimitReached && <section className="rounded-lg border border-amber-200 bg-amber-50/50 p-4" data-testid="rca-focused-hypotheses"><p className="text-xs font-semibold uppercase tracking-wide text-amber-700">Next step</p><h3 className="mt-1 text-sm font-semibold text-slate-900">Review the driver-focused hypothesis</h3><p className="mt-1 text-xs text-slate-600">The completed analysis identified an associated separator. Add context or revise the hypothesis, then create its governed investigation plan.</p><div className="mt-3 grid gap-2">{focusedHypothesisCandidates.map((candidate) => <article key={candidate.hypothesis_id} className="rounded border border-amber-200 bg-white p-3 text-xs"><HypothesisIdentity id={candidate.hypothesis_id} label={candidateLabel(focusedHypothesisCandidates.indexOf(candidate))} /><p className="font-semibold text-slate-900">{candidate.statement}</p><p className="mt-1 text-slate-600"><strong>Evidence basis:</strong> {candidate.evidence_basis}</p><p className="mt-1 text-indigo-700"><strong>Confirm with:</strong> {candidate.proposed_test}</p><HypothesisReviewControls hypothesis={candidate} busy={busy} onInvestigate={investigateFocusedHypothesis} progress={progressFor(`hypothesis-${candidate.hypothesis_id}`)} /></article>)}</div></section>}
      {!complete && (!pendingFocusedSelection || investigationLimitReached) && <section className="rounded-lg border border-teal-200 bg-teal-50/40 p-4" data-testid="rca-investigation-action"><div className="flex flex-wrap items-center gap-3">{c.state === "investigation_loop" && activeInvestigationHypothesis && hasAgentExecution ? <><Button disabled={busy || investigationLimitReached} onClick={() => act(() => exploreRcaHypothesis(c.case_id, "follow_up"), "investigation-action", page, "follow_up")}><Search className="h-4 w-4" />{progressFor("investigation-action") && activeAction === "follow_up" ? "Running investigation…" : "Continue exploring"}</Button><Button variant="outline" disabled={busy || investigationLimitReached} onClick={() => act(() => exploreRcaHypothesis(c.case_id, "alternative"), "investigation-action", page, "alternative")}><Lightbulb className="h-4 w-4" />{progressFor("investigation-action") && activeAction === "alternative" ? "Preparing investigation…" : "Explore another explanation"}</Button></> : action && <Button disabled={busy || investigationLimitReached} onClick={() => act(action[1], "investigation-action")}>{(() => { const Icon = action[2]; return <Icon className="h-4 w-4" />; })()}{progressFor("investigation-action") ? "Running investigation…" : action[0]} <ArrowRight className="h-4 w-4" /></Button>}<p className="text-xs text-slate-500">{investigationLimitReached ? (c.data_chat?.unlocked ? "Two-run limit reached. Ask about this data or continue to closure." : "Two-run limit reached. Continue to closure; data chat requires two successful runs.") : "Run another governed read-only analysis or continue to closure when the evidence is sufficient."}</p></div>{progressFor("investigation-action") && <div className="mt-2">{progressFor("investigation-action")}</div>}{error && errorLocation === "investigation-action" && <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert"><strong>Investigation action could not be completed.</strong><span className="mt-1 block">{error}</span></div>}</section>}
      <DataChatPanel caseId={c.case_id} generation={c.workflow_generation || 1} afterSequence={Math.max(0, ...aarEvidence.map((entry) => entry.sequence_no || 0))} chat={c.data_chat} onUpdated={setCase} />
      <nav className="flex items-center justify-between gap-3"><Button variant="outline" onClick={() => goTo("initial-review")}><ArrowLeft className="h-4 w-4" /> Initial Review</Button><Button onClick={() => goTo("closure")}>Continue to closure <ArrowRight className="h-4 w-4" /></Button></nav>
    </>}
    {page === "closure" && <>
      {complete ? <><section className="rounded-lg border border-emerald-200 bg-emerald-50 p-5"><div className="flex items-center gap-2 text-lg font-semibold text-emerald-950"><CheckCircle2 className="h-5 w-5" /> {outcomeLabel(c.closure)}</div><p className="mt-1 text-sm text-emerald-800">The investigation and issue workflow is closed. No Knowledge Base content or remediation task was created automatically.</p>{c.conclusion && <div className="mt-4 grid gap-2 text-sm sm:grid-cols-2"><p><strong>Conclusion:</strong> {c.conclusion.root_cause || "No root cause could be established."}</p><p><strong>Confidence:</strong> {c.conclusion.confidence}</p><p><strong>Approved by:</strong> {c.conclusion.approved_by}</p><p><strong>Approved:</strong> {c.conclusion.approved_at}</p></div>}</section><KnowledgeProposal c={c} issue={issue} busy={busy} progress={progressFor("knowledge")} onPropose={(body) => act(() => proposeRcaReusableKnowledge(c.case_id, body), "knowledge")} /><RemediationHandoff issue={issue} conclusion={c.conclusion} /></> : <ConclusionForm pendingAction={activeAction} progress={progressFor("page")} c={c} issue={issue} busy={busy} onApprove={(body) => act(() => approveRcaConclusion(c.case_id, body), "page", page, "approve")} onReturn={(reason) => act(() => returnRcaToInvestigation(c.case_id, reason).then((result) => { goTo("investigate"); return result; }), "page", page, "return")} />}
      <nav><Button variant="outline" onClick={() => goTo(executions.length ? "investigate" : "intake")}><ArrowLeft className="h-4 w-4" /> {executions.length ? "Back to investigate" : "Back to intake"}</Button></nav>
    </>}
    <details className="rounded-lg border border-slate-200 bg-white p-4"><summary className="flex cursor-pointer items-center gap-2 text-sm font-semibold text-slate-900"><History className="h-4 w-4" /> Activity ({aarEvidence.length || ((c.transitions || []).length + executions.length)}) <ChevronDown className="ml-auto h-4 w-4" /></summary><ol className="mt-3 border-l border-slate-200 pl-4 text-xs text-slate-600">{aarEvidence.length ? aarEvidence.map((entry) => <li key={entry.artifact_id} className="mb-3"><strong>{evidenceLabel(entry)}</strong><span className="block text-slate-400">{entry.recorded_at} · {entry.created_by || entry.summary?.fields?.actor || "system"} · AAR {entry.artifact_id}</span></li>) : <><li className="mb-3"><strong>RCA started</strong><span className="block text-slate-400">{c.created_at} · {c.created_by}</span></li>{executions.map((entry) => <li key={entry.execution_id} className="mb-3"><strong>{analysisTitle(c.looks.find((look) => look.look_id === entry.look_id) || {})} completed</strong><span className="block text-slate-400">{entry.executed_at} · evidence {entry.execution_id}</span></li>)}{c.conclusion && <li><strong>Conclusion approved</strong><span className="block text-slate-400">{c.conclusion.approved_at} · {c.conclusion.approved_by}</span></li>}</>}</ol></details>
  </div>;
}
