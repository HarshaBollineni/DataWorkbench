import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronDown, Circle,
  BookOpen, ClipboardCheck, FileSearch, History, Lightbulb, Play, RotateCcw, Search, Ticket,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import RcaSourceEvidence from "@/features/rca/components/RcaSourceEvidence";
import {
  approveRcaConclusion, composeRcaHypothesis, createRcaCase, getRcaCase,
  proposeRcaReusableKnowledge,
  returnRcaToInvestigation, runRcaConfirmationCheck, runRcaCoveragePass1,
  runRcaCoveragePass2, runRcaLook, runRcaOpeningLook, runRcaPlannerLook,
  runRcaReopenedKillAttempt, raiseIssueV2,
} from "@/api/client";

const RCA_PAGES = [
  { id: "intake", label: "Intake & initial review" },
  { id: "investigate", label: "Investigate" },
  { id: "closure", label: "Closure" },
];
const INITIAL_STATES = new Set(["created", "triage", "intake"]);
const CONCLUSION_STATES = new Set(["awaiting_fix_approval", "all_hypotheses_rejected", "closed", "unresolved"]);

function stageFor(state) {
  if (INITIAL_STATES.has(state)) return "Intake";
  if (state === "opening_looks") return "Initial checks";
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
  return <ol className="grid gap-2 sm:grid-cols-3" aria-label="RCA pages">
    {RCA_PAGES.map((page, index) => {
      const done = complete || index < active;
      const selected = index === active;
      return <li key={page.id}><button type="button" onClick={() => onSelect(page.id)} aria-current={selected ? "step" : undefined} className={`w-full rounded-md border px-3 py-2 text-left ${selected ? "border-teal-600 bg-teal-50" : "border-slate-200 bg-white hover:border-teal-300"}`}>
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
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(4);
  if (Array.isArray(value)) return value.join(", ");
  return String(value).replaceAll("_", " ");
}

function analysisTitle(look) {
  if (look.sql_or_helper_ref === "profile_column") return "Profile the affected feature";
  if (look.sql_or_helper_ref === "segment_breakdown") return "Compare the result across segments";
  return "Review supporting evidence";
}

function interpretation(summary = {}) {
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

function EvidenceCard({ look, execution }) {
  const summary = execution?.summary_json || {};
  return <article className="rounded-lg border border-slate-200 bg-white p-4">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h4 className="text-sm font-semibold text-slate-900">{analysisTitle(look)}</h4>
        <p className="mt-0.5 text-xs text-slate-500">Question: {look.sql_or_helper_ref === "segment_breakdown" ? "Is the observed problem concentrated in a meaningful segment?" : "What does the affected feature look like in the retained snapshot?"}</p></div>
      <Badge variant={execution ? "success" : "secondary"}>{execution ? "Complete" : "Ready to run"}</Badge>
    </div>
    {execution && <>
      <p className="mt-3 text-sm text-slate-700">{interpretation(summary)}</p>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
        {Object.entries(summary).filter(([, value]) => ["string", "number", "boolean"].includes(typeof value)).slice(0, 5).map(([key, value]) =>
          <span key={key}><strong className="font-medium text-slate-700">{displayValue(key)}:</strong> {displayValue(value)}</span>)}
      </div>
      <details className="mt-3 text-xs text-slate-500"><summary className="cursor-pointer font-medium text-dq-purple">Technical evidence</summary><pre className="mt-2 max-h-48 overflow-auto rounded bg-slate-50 p-3">{JSON.stringify(summary, null, 2)}</pre></details>
      <p className="mt-2 text-[11px] text-slate-400">Evidence {look.look_id} · completed {execution.executed_at || "timestamp retained"}</p>
    </>}
  </article>;
}

function ConclusionForm({ c, issue, onApprove, onReturn, busy }) {
  const evidenceIds = [issue?.source_evidence?.result_id, ...Object.values(c.executions || {}).map((entry) => entry.execution_id)].filter(Boolean);
  const suggested = c.confirmed_hypothesis || c.hypotheses?.[0];
  const [form, setForm] = useState({
    conclusion_type: c.state === "all_hypotheses_rejected" ? "unresolved" : "root_cause_identified",
    root_cause: suggested?.statement || "",
    confidence: suggested?.tier === "strong" ? "High" : suggested?.tier === "weak" ? "Low" : "Moderate",
    limiting_evidence: "",
    alternatives_considered: c.suspects?.length > 1 ? "Other active and ruled-out explanations shown in the investigation record." : "No additional evidence-backed alternative was identified in the available scope.",
    affected_scope: `${issue?.table_name || c.table_name || "Table"}${(issue?.columns || []).length ? ` · ${issue.columns.join(", ")}` : ""}`,
    related_failures: "Reviewed against the failures attached to this RCA case.",
    owner: "",
    approval_rationale: "",
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
    <div className="mt-4 flex flex-wrap items-center gap-2"><Button disabled={busy || !ready} onClick={() => onApprove({ ...form, supporting_evidence_ids: evidenceIds })}><CheckCircle2 className="h-4 w-4" /> {identified ? "Approve root cause" : "Approve unresolved conclusion"}</Button><input className="h-9 min-w-56 flex-1 rounded-md border border-slate-200 px-3 text-xs" value={returnReason} onChange={(event) => setReturnReason(event.target.value)} placeholder="Reason to return to Investigate" /><Button variant="outline" disabled={busy || !returnReason.trim()} onClick={() => onReturn(returnReason.trim())}><RotateCcw className="h-4 w-4" /> Return to Investigate</Button></div>
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

function KnowledgeProposal({ c, issue, busy, onPropose }) {
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
  </section>;
}

export default function RcaCase({ issueRowId, issue }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [c, setCase] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { createRcaCase(issueRowId).then(setCase).catch((requestError) => setError(requestError.message)); }, [issueRowId]);
  const act = async (fn) => { setBusy(true); setError(""); try { const result = await fn(); const next = result?.case_id ? result : result?.case || await getRcaCase(c.case_id); setCase(next); return next; } catch (requestError) { setError(requestError.message); return null; } finally { setBusy(false); } };
  const executions = useMemo(() => Object.values(c?.executions || {}), [c]);
  const openLook = c?.looks?.find((look) => look.kind === "planned" && !executions.some((entry) => entry.look_id === look.look_id));
  const pendingCheck = c?.confirmation_checks?.find((check) => check.status === "pending");
  if (!c) return <div className="rounded-md border border-dashed border-slate-200 p-4 text-sm text-slate-500">{error || "Preparing the RCA case…"}</div>;
  const stage = stageFor(c.state);
  const complete = c.state === "closed" || Boolean(c.closure);
  const defaultPage = complete || CONCLUSION_STATES.has(c.state) ? "closure" : stage === "Investigate" ? "investigate" : "intake";
  const requestedPage = searchParams.get("rca_view");
  const page = RCA_PAGES.some((entry) => entry.id === requestedPage) ? requestedPage : defaultPage;
  const goTo = (nextPage) => setSearchParams((current) => { const next = new URLSearchParams(current); next.set("rca_view", nextPage); return next; });

  const action = (() => {
    if (c.state === "opening_looks") return ["Review initial evidence", () => runRcaOpeningLook(c.case_id), FileSearch];
    if (c.state === "investigation_loop" && openLook) return ["Run approved analysis", () => runRcaLook(openLook.look_id), Play];
    if (c.state === "investigation_loop") return ["Recommend next analysis", () => runRcaPlannerLook(c.case_id), Lightbulb];
    if (c.state === "coverage_challenge_blind") return ["Review alternative explanations", () => runRcaCoveragePass1(c.case_id), Search];
    if (c.state === "coverage_challenge_history") return ["Compare with prior evidence", () => runRcaCoveragePass2(c.case_id), Search];
    if (c.state === "reopened_kill_attempt") return ["Test the remaining alternative", () => runRcaReopenedKillAttempt(c.case_id), Play];
    if (c.state === "hypothesis_composition") return ["Prepare proposed conclusion", () => composeRcaHypothesis(c.case_id), ClipboardCheck];
    if (c.state === "confirmation_checks" && pendingCheck) return ["Test the proposed explanation", () => runRcaConfirmationCheck(pendingCheck.check_id), Play];
    return null;
  })();
  const goInvestigate = async () => {
    if (c.state === "opening_looks") {
      const next = await act(() => runRcaOpeningLook(c.case_id));
      if (!next) return;
    }
    goTo("investigate");
  };
  const selectPage = (nextPage) => nextPage === "investigate" ? goInvestigate() : goTo(nextPage);

  return <div className="space-y-4">
    <section className="rounded-lg border border-slate-200 bg-white p-4" data-testid="rca-workspace-header">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase text-slate-400">Managed issue {issue?.issue_row_id || issueRowId} · RCA {c.case_id}</p><h2 className="mt-1 text-lg font-semibold text-slate-950">{issue?.test_name || c.case_file?.checklist_json?.test_name || "Diagnostic investigation"}</h2><p className="text-xs text-slate-500">{issue?.item_name || "Dataset"} · {issue?.table_name || c.table_name} · {(issue?.columns || []).join(", ") || "assessed scope"}</p></div><div className="flex flex-wrap gap-2"><Badge variant={complete ? "success" : "warning"}>{complete ? "Closed" : "RCA in progress"}</Badge><Badge variant="outline">{RCA_PAGES.find((entry) => entry.id === page)?.label}</Badge><Badge variant={issue?.criticality === "Critical" ? "destructive" : "secondary"}>{issue?.criticality || "Criticality not assigned"}</Badge></div></div>
      <div className="mt-3 grid gap-2 border-t border-slate-100 pt-3 text-xs sm:grid-cols-3"><span><strong className="text-slate-700">Owner:</strong> {c.conclusion?.owner || "Not assigned"}</span><span><strong className="text-slate-700">Age:</strong> {ageLabel(c.created_at)}</span><span><strong className="text-slate-700">Next required action:</strong> {complete ? "RCA complete; optional handoffs are separate" : page === "closure" ? "Approve or return the conclusion" : page === "intake" ? "Conclude from intake or investigate further" : "Run, refine, or continue to closure"}</span></div>
    </section>
    <StagePath current={page} complete={complete} onSelect={selectPage} />
    {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
    {page === "intake" && <>
      <RcaSourceEvidence issue={issue} />
      <nav className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-teal-200 bg-[#f8faf6] p-4" aria-label="Intake decisions"><p className="text-xs text-slate-600">If the retained diagnostic evidence is sufficient, continue directly to closure. Otherwise open the investigation workspace.</p><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy || complete} onClick={() => goTo("closure")}><CheckCircle2 className="h-4 w-4" /> Conclude from intake</Button><Button disabled={busy || complete} onClick={goInvestigate}>Investigate more <ArrowRight className="h-4 w-4" /></Button>{complete && <Button onClick={() => goTo("closure")}>View closure <ArrowRight className="h-4 w-4" /></Button>}</div></nav>
    </>}
    {page === "investigate" && <>
      <section><div className="mb-2 flex items-end justify-between"><div><h3 className="font-semibold text-slate-950">Investigation record</h3><p className="text-xs text-slate-500">Questions, results, interpretations, and retained evidence.</p></div><Badge variant="secondary">{executions.length} completed</Badge></div>{c.looks?.length > 0 ? <div className="grid gap-3">{c.looks.map((look) => <EvidenceCard key={look.look_id} look={look} execution={executions.find((entry) => entry.look_id === look.look_id)} />)}</div> : <div className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-500">No additional analysis has been requested. The intake evidence remains available on the previous page.</div>}</section>
      {!complete && <section className="rounded-lg border border-teal-200 bg-teal-50/40 p-4"><div className="flex flex-wrap items-center gap-3">{action && <Button disabled={busy} onClick={() => act(action[1])}>{(() => { const Icon = action[2]; return <Icon className="h-4 w-4" />; })()}{action[0]} <ArrowRight className="h-4 w-4" /></Button>}<p className="text-xs text-slate-500">Run another governed read-only analysis or continue to closure when the evidence is sufficient.</p></div></section>}
      <nav className="flex items-center justify-between gap-3"><Button variant="outline" onClick={() => goTo("intake")}><ArrowLeft className="h-4 w-4" /> Intake & initial review</Button><Button onClick={() => goTo("closure")}>Continue to closure <ArrowRight className="h-4 w-4" /></Button></nav>
    </>}
    {page === "closure" && <>
      {complete ? <><section className="rounded-lg border border-emerald-200 bg-emerald-50 p-5"><div className="flex items-center gap-2 text-lg font-semibold text-emerald-950"><CheckCircle2 className="h-5 w-5" /> {outcomeLabel(c.closure)}</div><p className="mt-1 text-sm text-emerald-800">The investigation and issue workflow is closed. No Knowledge Base content or remediation task was created automatically.</p>{c.conclusion && <div className="mt-4 grid gap-2 text-sm sm:grid-cols-2"><p><strong>Conclusion:</strong> {c.conclusion.root_cause || "No root cause could be established."}</p><p><strong>Confidence:</strong> {c.conclusion.confidence}</p><p><strong>Approved by:</strong> {c.conclusion.approved_by}</p><p><strong>Approved:</strong> {c.conclusion.approved_at}</p></div>}</section><KnowledgeProposal c={c} issue={issue} busy={busy} onPropose={(body) => act(() => proposeRcaReusableKnowledge(c.case_id, body))} /><RemediationHandoff issue={issue} conclusion={c.conclusion} /></> : <ConclusionForm c={c} issue={issue} busy={busy} onApprove={(body) => act(() => approveRcaConclusion(c.case_id, body))} onReturn={(reason) => act(() => returnRcaToInvestigation(c.case_id, reason).then((result) => { goTo("investigate"); return result; }))} />}
      <nav><Button variant="outline" onClick={() => goTo(executions.length ? "investigate" : "intake")}><ArrowLeft className="h-4 w-4" /> {executions.length ? "Back to investigate" : "Back to intake"}</Button></nav>
    </>}
    <details className="rounded-lg border border-slate-200 bg-white p-4"><summary className="flex cursor-pointer items-center gap-2 text-sm font-semibold text-slate-900"><History className="h-4 w-4" /> Activity ({(c.transitions || []).length + executions.length}) <ChevronDown className="ml-auto h-4 w-4" /></summary><ol className="mt-3 border-l border-slate-200 pl-4 text-xs text-slate-600"><li className="mb-3"><strong>RCA started</strong><span className="block text-slate-400">{c.created_at} · {c.created_by}</span></li>{executions.map((entry) => <li key={entry.execution_id} className="mb-3"><strong>{analysisTitle(c.looks.find((look) => look.look_id === entry.look_id) || {})} completed</strong><span className="block text-slate-400">{entry.executed_at} · evidence {entry.execution_id}</span></li>)}{c.conclusion && <li><strong>Conclusion approved</strong><span className="block text-slate-400">{c.conclusion.approved_at} · {c.conclusion.approved_by}</span></li>}</ol></details>
  </div>;
}
