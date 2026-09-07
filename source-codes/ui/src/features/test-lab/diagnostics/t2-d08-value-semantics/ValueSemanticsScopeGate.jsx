import { useMemo, useState } from "react";
import { Bot, CheckCircle2, Database, Info, Play, ShieldCheck, Tags } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  aiRoleReviewSummary, canRequestAiRoleReview, hasCompletedAiRoleReview,
  isFieldNotApplicable, needsAiRoleReview,
  requestAiRoleReviewsSequentially,
} from "./valueSemanticsWorkflow";

const CONTEXTS = ["GENERAL", "PD", "LGD", "EAD"];
const STEP_LABELS = ["About", "Data & context", "Role bindings", "Rules & launch"];

function TagDefinition({ row }) {
  const palette = {
    CENSORED: "border-violet-200 bg-violet-50 text-violet-900",
    STALE_FROZEN: "border-rose-200 bg-rose-50 text-rose-900",
    NOT_APPLICABLE: "border-sky-200 bg-sky-50 text-sky-900",
  };
  return <div className={`rounded-lg border p-3 ${palette[row.tag] || "border-slate-200 bg-slate-50"}`}>
    <strong className="text-sm">{row.tag}</strong><p className="mt-1 text-xs leading-5">{row.meaning}</p>
  </div>;
}

function AboutPage({ manifest, busy, patch, onContinue }) {
  const info = manifest.introduction;
  const acknowledge = async () => {
    if (!manifest.introduction_acknowledged) {
      await patch({ kind: "introduction_acknowledgement", enabled: true });
    }
    onContinue();
  };
  return <section className="grid gap-4" data-testid="value-semantics-about">
    <header className="rounded-xl border border-teal-200 bg-gradient-to-br from-teal-50 to-white p-5">
      <div className="flex items-start gap-3"><div className="rounded-lg bg-teal-700 p-2 text-white"><Tags className="h-5 w-5" /></div><div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal-700">Test 2 · Diagnostic 8</p><h2 className="mt-1 text-xl font-semibold text-slate-950">{info.title}</h2><p className="mt-2 max-w-4xl text-sm leading-6 text-slate-600">{info.purpose}</p></div></div>
    </header>
    <div className="grid gap-3 md:grid-cols-3">{info.tags.map((row) => <TagDefinition key={row.tag} row={row} />)}</div>
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-teal-700" /><h3 className="font-semibold text-slate-900">How to use the result</h3></div><p className="mt-2 text-sm leading-6 text-slate-600">Use the tags to exclude or separately treat cells whose meaning differs from ordinary missing or observed values. Review grouped anomalies before escalating them. The diagnostic prepares evidence; a human decides whether an Issue and RCA are warranted.</p></div>
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4"><div className="flex items-center gap-2"><Info className="h-4 w-4 text-amber-700" /><h3 className="font-semibold text-amber-950">Important boundaries</h3></div><ul className="mt-2 grid gap-1.5 text-sm leading-5 text-amber-900">{info.boundaries.map((value) => <li key={value}>• {value}</li>)}</ul></div>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4"><p className="max-w-3xl text-sm text-slate-600">You can begin with <strong>General</strong>. The diagnostic can still execute any rule whose confirmed semantic roles and declarations are available.</p><Button disabled={busy} onClick={acknowledge}><CheckCircle2 className="h-4 w-4" /> I understand — configure diagnostic</Button></div>
  </section>;
}

function DataContext({ manifest, busy, patch }) {
  const selected = manifest.context.selected || ["GENERAL"];
  const toggle = (context) => {
    const next = selected.includes(context) ? selected.filter((value) => value !== context) : [...selected, context];
    patch({ kind: "context_selection", value: next.length ? next : ["GENERAL"] });
  };
  return <section className="grid gap-4" data-testid="value-semantics-data-context">
    <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex items-center gap-2"><Database className="h-4 w-4 text-teal-700" /><h3 className="font-semibold text-slate-900">Data scope</h3></div><label className="mt-3 block text-xs font-medium text-slate-600">Profiled table<select className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={manifest.table} disabled={busy} onChange={(event) => patch({ kind: "table_selection", table: event.target.value })}>{manifest.tables.map((table) => <option key={table} value={table}>{table}</option>)}</select></label><p className="mt-2 text-xs text-slate-500">{manifest.fields.length} profiled fields are available. Exact KB representations are selected automatically; other fields stay available for review.</p></div>
    <div className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="font-semibold text-slate-900">Optional analytical context</h3><p className="mt-1 text-sm text-slate-600">Context helps users interpret the report. It does not activate, suppress, or prioritize rules.</p><div className="mt-3 flex flex-wrap gap-2">{CONTEXTS.map((context) => <label key={context} className={`flex cursor-pointer items-center gap-2 rounded-full border px-3 py-2 text-sm ${selected.includes(context) ? "border-teal-500 bg-teal-50 text-teal-900" : "border-slate-200 bg-white text-slate-600"}`}><input type="checkbox" checked={selected.includes(context)} disabled={busy} onChange={() => toggle(context)} />{context === "GENERAL" ? "General / unspecified" : context}</label>)}</div>{manifest.context.suggestions?.length > 0 && <div className="mt-4 rounded-lg border border-indigo-200 bg-indigo-50 p-3"><p className="text-xs font-semibold text-indigo-900">Explainable suggestions</p><div className="mt-2 flex flex-wrap gap-2">{manifest.context.suggestions.map((row) => <Badge key={row.context} variant="outline" title={`${row.explanation} Evidence: ${row.example_fields.join(", ")}`}>{row.context} · {row.evidence_count} field signals</Badge>)}</div><p className="mt-2 text-xs text-indigo-800">These are deterministic hints only. You may accept, correct, combine, or ignore them.</p></div>}</div>
  </section>;
}

function AiRoleReview({ field, busy, patch }) {
  const summary = aiRoleReviewSummary(field);
  if (!summary) return null;
  const confirmed = summary.roles.length > 0
    && summary.roles.every((role) => field.confirmed_roles?.includes(role));
  const palette = summary.tone === "match"
    ? "border-indigo-200 bg-indigo-50 text-indigo-950"
    : "border-amber-200 bg-amber-50 text-amber-950";
  return <div className={`mt-2 rounded-lg border p-3 text-xs ${palette}`} data-testid={`ai-role-review-${field.column}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="flex min-w-0 flex-1 items-start gap-2">
        <Bot className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <div className="min-w-0"><strong>{summary.title}</strong><p className="mt-1 font-medium leading-5">{summary.recommendation}</p></div>
      </div>
      <Badge variant="secondary">{confirmed ? "Confirmed" : summary.confirmable ? "Confirmation required" : "Review result"}</Badge>
    </div>
    {field.adjudication?.reused_from && <div className="mt-2 flex flex-wrap items-center gap-2">
      <Badge variant="outline">Reused prior AI inference</Badge>
      <span className="text-[10px] opacity-75">Source run {field.adjudication.reused_from.run_id}; no new AI call</span>
    </div>}
    <div className="mt-2 rounded border border-white/80 bg-white/75 px-3 py-2">
      <span className="text-[10px] font-semibold uppercase tracking-wide opacity-70">Why AI recommends this</span>
      <p className="mt-1 leading-5">{summary.reason}</p>
    </div>
    {summary.confirmable && !confirmed && <Button
      className="mt-2" size="sm" variant="outline" disabled={busy || isFieldNotApplicable(field)}
      onClick={() => patch({ kind: "role_binding", feature: field.column, value: summary.roles })}
    ><CheckCircle2 className="h-3.5 w-3.5" /> Confirm {summary.roles.join(" + ")}</Button>}
  </div>;
}

function FieldApplicability({ field, busy, patch }) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const notApplicable = isFieldNotApplicable(field);
  const confirmedBinding = !field.review_required && (field.confirmed_roles?.length || 0) > 0;
  const save = async (value) => {
    setError("");
    try {
      await patch({ kind: "field_applicability", feature: field.column, value, reason: reason.trim() });
      setEditing(false);
    } catch (failure) {
      setError(failure.message || "Could not save the applicability decision.");
    }
  };
  return <div className="mt-2 text-xs" data-testid={`field-applicability-${field.column}`}>
    {notApplicable ? <>
      <Badge className="border-slate-300 bg-slate-100 text-slate-700" variant="outline">Not applicable</Badge>
      <p className="mt-1 max-w-xs whitespace-pre-wrap text-slate-600">{field.applicability.reason}</p>
      <p className="mt-1 text-[10px] text-slate-500">Confirmed by {field.applicability.confirmed_by}</p>
      <Button className="mt-2" size="sm" variant="outline" disabled={busy} onClick={() => save("review")}>Reopen review</Button>
    </> : <div className="flex flex-wrap gap-2">
      <button
        type="button" disabled={busy} aria-label={`Mark ${field.column} not applicable`} aria-expanded={editing}
        className="rounded-full border border-slate-300 px-2 py-1 text-slate-600 hover:bg-slate-100 disabled:opacity-50"
        onClick={() => {
          const output = field.adjudication?.status === "proposal_ready" ? field.adjudication.output : null;
          setReason(output?.decision === "NO_CANDIDATE_MATCH" ? output.reason : "");
          setEditing(true);
        }}
      >Not applicable</button>
      {confirmedBinding && <button
        type="button" disabled={busy}
        className="rounded-full border border-slate-300 px-2 py-1 text-slate-600 hover:bg-slate-100 disabled:opacity-50"
        onClick={() => save("review")}
      >Reopen role review</button>}
    </div>}
    {editing && !notApplicable && <div className="mt-2 min-w-56 rounded-lg border border-slate-200 bg-slate-50 p-2">
      <label className="block font-medium">Reason for {field.column}
        <textarea aria-label={`Not applicable reason for ${field.column}`} className="mt-1 min-h-20 w-full rounded border border-slate-300 bg-white p-2 font-normal" value={reason} maxLength={2000} disabled={busy} onChange={(event) => setReason(event.target.value)} />
      </label>
      <p className="mt-1 text-slate-500">Confirm that no role in this diagnostic applies. This removes the field from execution, not from the dataset.</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={busy || !reason.trim()} onClick={() => save("not_applicable")}>Confirm not applicable</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>Cancel</Button>
      </div>
    </div>}
    {error && <p role="alert" className="mt-1 text-red-700">{error}</p>}
  </div>;
}

function RoleBindings({ manifest, busy, patch, onOperationBusy }) {
  const [bulkAiProgress, setBulkAiProgress] = useState(null);
  const [bulkAiNotice, setBulkAiNotice] = useState(null);
  const [singleAiField, setSingleAiField] = useState(null);
  const roles = manifest.role_catalog || [];
  const pendingAiFields = useMemo(
    () => manifest.fields.filter(needsAiRoleReview),
    [manifest.fields],
  );
  const completedAiReviews = useMemo(
    () => manifest.fields.filter(hasCompletedAiRoleReview).length,
    [manifest.fields],
  );
  const workflowBusy = busy || Boolean(bulkAiProgress) || Boolean(singleAiField);

  const askAiForField = async (column) => {
    setSingleAiField(column);
    setBulkAiNotice(null);
    onOperationBusy(true);
    try {
      await patch({ kind: "request_ai_role_review", feature: column });
    } catch (error) {
      setBulkAiNotice({ tone: "warning", message: `AI review could not finish for ${column}: ${error.message}` });
    } finally {
      setSingleAiField(null);
      onOperationBusy(false);
    }
  };

  const askAiForAll = async () => {
    const pending = [...pendingAiFields];
    if (!pending.length) return;
    setBulkAiNotice(null);
    onOperationBusy(true);
    const outcome = await requestAiRoleReviewsSequentially(
      pending,
      (column) => patch({ kind: "request_ai_role_review", feature: column }),
      setBulkAiProgress,
    );
    setBulkAiProgress(null);
    onOperationBusy(false);
    if (outcome.status === "stopped") {
      setBulkAiNotice({
        tone: "warning",
        message: `Batch AI review stopped at ${outcome.failedField}. ${outcome.completed} review${outcome.completed === 1 ? " was" : "s were"} completed and ${outcome.notAttempted} later field${outcome.notAttempted === 1 ? " was" : "s were"} not attempted. Resolve the displayed error, then run the remaining batch again.`,
      });
      return;
    }
    setBulkAiNotice(outcome.unavailable ? {
      tone: "warning",
      message: `AI review completed for ${outcome.completed} of ${outcome.total} fields. ${outcome.unavailable} returned no usable result and still require manual review.`,
    } : {
      tone: "success",
      message: `AI review completed for ${outcome.completed} fields. Confirm suggested roles or mark Not applicable with a reason; AI has not changed field scope or confirmed a decision.`,
    });
  };

  return <section className="grid gap-3" data-testid="value-semantics-role-bindings">
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h3 className="font-semibold text-slate-900">Fields and semantic roles</h3><p className="mt-1 max-w-4xl text-sm text-slate-600">Only selected fields are part of the frozen scope. Exact matches can run directly and do not need AI. Prior validated AI inference is reused for the same snapshot and field without another provider call. Ask AI appears only for a confirmation-required field with no prior completed review. Every AI-proposed role still requires user confirmation.</p></div>
        <Button
          size="sm" variant="outline" disabled={workflowBusy || pendingAiFields.length === 0}
          onClick={askAiForAll}
        ><Bot className="h-3.5 w-3.5" />{bulkAiProgress
          ? `Reviewing ${bulkAiProgress.current} of ${bulkAiProgress.total}`
          : pendingAiFields.length
            ? `Ask AI for all pending fields (${pendingAiFields.length})`
            : "No pending AI reviews"}</Button>
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-600">Unchecked Include means excluded from this run, not confirmed inapplicable. Use Not applicable when no governed role fits, and record why. All field scope decisions are retained in the run, AAR, and report when you freeze and run. Field-level Not applicable is not a cell-level NOT_APPLICABLE tag. Reopen review to change a confirmed decision.</p>
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <Badge variant="secondary">{completedAiReviews} AI review{completedAiReviews === 1 ? "" : "s"} completed</Badge>
        <Badge variant="outline">{pendingAiFields.length} eligible field{pendingAiFields.length === 1 ? "" : "s"} pending</Badge>
        {bulkAiProgress && <span className="text-indigo-700" aria-live="polite">Reviewing {bulkAiProgress.field}</span>}
      </div>
      {bulkAiNotice && <p className={`mt-3 rounded border px-3 py-2 text-xs ${bulkAiNotice.tone === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}>{bulkAiNotice.message}</p>}
    </div>
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white"><div className="max-h-[40rem] overflow-auto"><table className="w-full min-w-[1100px] text-left text-sm"><thead className="sticky top-0 z-10 bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-4 py-3">Include</th><th className="px-4 py-3">Field</th><th className="px-4 py-3">Binding status</th><th className="px-4 py-3">Confirmed semantic role</th><th className="min-w-96 px-4 py-3">Advisory help</th></tr></thead><tbody>{manifest.fields.map((field) => {
      const primary = field.confirmed_roles?.[0] || "";
      const notApplicable = isFieldNotApplicable(field);
      return <tr key={field.column} className="border-t border-slate-100 align-top" data-testid={`role-field-${field.column}`}>
        <td className="px-4 py-3"><input aria-label={`Include ${field.column}`} type="checkbox" checked={Boolean(field.selected)} disabled={workflowBusy || notApplicable} onChange={(event) => patch({ kind: "field_scope", feature: field.column, enabled: event.target.checked })} /></td>
        <td className="px-4 py-3"><strong className="block text-slate-900">{field.column}</strong><span className="block max-w-sm text-xs leading-4 text-slate-500">{field.description || field.data_type || "No dictionary description"}</span></td>
        <td className="px-4 py-3">{!notApplicable && <Badge variant={field.review_required ? "outline" : "secondary"}>{field.review_required ? "Confirmation required" : field.binding_source.replaceAll("_", " ")}</Badge>}<FieldApplicability field={field} busy={workflowBusy} patch={patch} />{!notApplicable && field.unresolved_abbreviations?.length > 0 && <span className="mt-1 block text-xs text-amber-700">Ambiguous: {field.unresolved_abbreviations.map((row) => row.abbreviation).join(", ")}</span>}</td>
        <td className="px-4 py-3">
          <select aria-label={`Role for ${field.column}`} className="h-9 w-full min-w-56 rounded-md border border-slate-200 bg-white px-2 text-xs" value={primary} disabled={workflowBusy || notApplicable} onChange={(event) => event.target.value && patch({ kind: "role_binding", feature: field.column, value: [event.target.value] })}>
            <option value="">{notApplicable ? "No applicable role confirmed" : "Choose and confirm a role"}</option>
            {roles.map((role) => <option key={role.role} value={role.role}>{role.role} — {role.definition}</option>)}
          </select>
          {notApplicable && <p className="mt-1 text-xs text-slate-500">Reopen review to select a role.</p>}
          {field.confirmed_roles?.length > 1 && <span className="mt-1 block text-[11px] text-slate-500">Includes implied roles: {field.confirmed_roles.slice(1).join(", ")}</span>}
        </td>
        <td className="px-4 py-3">
          <div className="flex flex-wrap gap-1">{field.candidates?.slice(0, 3).map((candidate) => <button key={candidate.role} type="button" disabled={workflowBusy || notApplicable} className="rounded-full border border-slate-200 px-2 py-1 text-[11px] text-slate-600 hover:border-teal-400 disabled:opacity-50" title={candidate.definition} onClick={() => patch({ kind: "role_binding", feature: field.column, value: [candidate.role] })}>{candidate.role}</button>)}</div>
          {canRequestAiRoleReview(field) && <Button className="mt-2" size="sm" variant="outline" disabled={workflowBusy} aria-label={`Ask AI for ${field.column}`} onClick={() => askAiForField(field.column)}>
            <Bot className="h-3.5 w-3.5" />{singleAiField === field.column ? "Reviewing…" : "Ask AI"}
          </Button>}
          <AiRoleReview field={field} busy={workflowBusy} patch={patch} />
          {notApplicable && <p className="mt-2 text-xs text-slate-500">Reopen review before requesting AI or selecting another role.</p>}
          {field.review_required && !field.candidates?.length && <p className="mt-2 text-xs leading-5 text-amber-700">No bounded role candidates are available. Review manually or leave this field excluded.</p>}
        </td>
      </tr>;
    })}</tbody></table></div></div>
  </section>;
}

function RulesLaunch({ manifest, busy, patch, runNow }) {
  const [text, setText] = useState(() => JSON.stringify(manifest.runtime_declarations, null, 2));
  const [jsonError, setJsonError] = useState("");
  const coverage = manifest.coverage;
  const save = async () => { try { const value = JSON.parse(text); setJsonError(""); await patch({ kind: "declarations_replace", value }); } catch (error) { setJsonError(error.message); } };
  return <section className="grid gap-4" data-testid="value-semantics-rules-launch">
    <div className="grid gap-3 sm:grid-cols-3"><div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4"><small className="text-emerald-700">Ready rule routes</small><strong className="mt-1 block text-2xl text-emerald-950">{coverage.ready_routes.length}</strong></div><div className="rounded-xl border border-amber-200 bg-amber-50 p-4"><small className="text-amber-700">Unscoped routes</small><strong className="mt-1 block text-2xl text-amber-950">{coverage.unscoped_routes.length}</strong></div><div className="rounded-xl border border-slate-200 bg-white p-4"><small className="text-slate-500">KB entries</small><strong className="mt-1 block text-2xl text-slate-950">{coverage.kb_entries}</strong></div></div>
    <div className="grid gap-4 xl:grid-cols-2"><div className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="font-semibold text-slate-900">Runtime declarations</h3><p className="mt-1 text-xs leading-5 text-slate-500">Safe execution controls are prefilled. Add business declarations only when they are known. Missing prerequisites stay visible as UNSCOPED.</p><textarea className="mt-3 min-h-72 w-full rounded-lg border border-slate-200 bg-slate-950 p-3 font-mono text-xs text-slate-100" value={text} onChange={(event) => setText(event.target.value)} spellCheck={false} />{jsonError && <p className="mt-2 text-xs text-red-600">{jsonError}</p>}<Button className="mt-2" size="sm" variant="outline" disabled={busy} onClick={save}>Validate and save declarations</Button></div><div className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="font-semibold text-slate-900">Coverage preview</h3><p className="mt-1 text-xs text-slate-500">This preview is frozen with the run. A route is ready only when its target role, single-value indicator roles, and declarations are available.</p><div className="mt-3 max-h-80 overflow-auto"><table className="w-full text-left text-xs"><thead className="text-slate-500"><tr><th className="py-2">Field / rule</th><th className="py-2">Status</th><th className="py-2">Missing prerequisites</th></tr></thead><tbody>{coverage.ready_routes.map((row) => <tr key={`${row.entry}:${row.target_column}`} className="border-t border-slate-100"><td className="py-2"><strong>{row.target_column}</strong><span className="block text-[10px] text-slate-500">{row.entry}</span></td><td><Badge variant="secondary">Ready</Badge></td><td>—</td></tr>)}{coverage.unscoped_routes.map((row) => <tr key={`${row.entry}:${row.target_column}`} className="border-t border-slate-100"><td className="py-2"><strong>{row.target_column}</strong><span className="block text-[10px] text-slate-500">{row.entry}</span></td><td><Badge variant="outline">UNSCOPED</Badge></td><td className="max-w-64 text-amber-700">{[...row.missing_roles.map((value) => `role:${value}`), ...row.missing_declarations.map((value) => `declaration:${value}`)].join(", ")}</td></tr>)}</tbody></table></div></div></div>
    {manifest.blockers?.length > 0 && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"><strong>Resolve before launch</strong><ul className="mt-1">{manifest.blockers.map((row) => <li key={row.code}>• {row.message}</li>)}</ul></div>}
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-teal-200 bg-teal-50 p-4"><div><h3 className="font-semibold text-teal-950">Freeze and launch</h3><p className="mt-1 text-xs text-teal-800">Execution is deterministic. Results, bindings, full tags, the assessment ledger, and the report will be retained in the Analytics Artifact Repository.</p></div><Button disabled={busy || !manifest.ready_to_run} onClick={runNow}><Play className="h-4 w-4" /> Freeze and run</Button></div>
  </section>;
}

export default function ValueSemanticsScopeGate({ manifest, busy, patch, runNow }) {
  const initial = manifest.introduction_acknowledged ? 1 : 0;
  const [step, setStep] = useState(initial);
  const [operationBusy, setOperationBusy] = useState(false);
  const completed = useMemo(() => [true, Boolean(manifest.table), !manifest.blockers?.some((row) => row.code === "binding_confirmation_required"), manifest.ready_to_run], [manifest]);
  return <div className="grid gap-4">
    <nav className="flex flex-wrap gap-2 rounded-xl border border-slate-200 bg-white p-2" aria-label="Value Semantics setup steps">{STEP_LABELS.map((label, index) => <button key={label} type="button" disabled={busy || operationBusy} onClick={() => setStep(index)} className={`rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50 ${step === index ? "bg-slate-950 text-white" : "text-slate-600 hover:bg-slate-50"}`}>{completed[index] && index !== step ? "✓ " : ""}{index + 1}. {label}</button>)}</nav>
    {step === 0 && <AboutPage manifest={manifest} busy={busy} patch={patch} onContinue={() => setStep(1)} />}
    {step === 1 && <DataContext manifest={manifest} busy={busy} patch={patch} />}
    {step === 2 && <RoleBindings manifest={manifest} busy={busy} patch={patch} onOperationBusy={setOperationBusy} />}
    {step === 3 && <RulesLaunch manifest={manifest} busy={busy} patch={patch} runNow={runNow} />}
    {step > 0 && <div className="flex justify-between"><Button variant="outline" disabled={busy || operationBusy} onClick={() => setStep((value) => Math.max(0, value - 1))}>Previous</Button>{step < 3 && <Button disabled={busy || operationBusy} onClick={() => setStep((value) => Math.min(3, value + 1))}>Continue</Button>}</div>}
  </div>;
}
