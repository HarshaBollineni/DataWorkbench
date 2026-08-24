import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, FileJson, RotateCcw, Save, ShieldCheck } from "lucide-react";

import {
  activateDiagnosticKbPackageV3, createDiagnosticKbDraftV3, getDiagnosticKbPackagesV3,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  diagnosticPackageChanges, REPORTING_GRAINS, SEVERITIES,
  validateDiagnosticPackageEditor,
} from "./diagnosticPackageEditor";

const clone = (value) => JSON.parse(JSON.stringify(value));
const labels = {
  name: "Diagnostic name", what_it_computes: "What this diagnostic computes",
  metric: "Metric shown to users", threshold_rule_default: "Default threshold explanation",
  continuity_floor_label: "Coverage-floor label", continuity_floor_help: "Coverage-floor guidance",
};
const guidance = {
  name: "User-facing name shown in Test Lab and reports.",
  what_it_computes: "Explain the diagnostic question in plain language; executable logic is unchanged.",
  metric: "Explain the deterministic metric used by the diagnostic.",
  threshold_rule_default: "Explain how the single coverage floor is interpreted.",
  continuity_floor_label: "Short label displayed beside the configured floor.",
  continuity_floor_help: "Explain what the floor controls and where it applies.",
};

function PackageBadge({ state }) {
  const variant = state === "active" ? "success" : state === "draft" ? "warning" : "secondary";
  return <Badge variant={variant}>{String(state || "unknown").replaceAll("_", " ")}</Badge>;
}

function ChangeList({ changes = [] }) {
  if (!changes.length) return <p className="text-xs text-slate-500">No editable values differ from the active version.</p>;
  return <div className="max-h-72 overflow-auto rounded-md border border-slate-200">{changes.map((change) => <div key={change.path} className="grid gap-1 border-t border-slate-100 p-3 text-xs first:border-t-0 md:grid-cols-[14rem_1fr]"><code className="font-semibold text-dq-purple">{change.path}</code><div className="min-w-0"><p className="break-words text-slate-400 line-through">{String(change.before)}</p><p className="break-words text-slate-800">{String(change.after)}</p></div></div>)}</div>;
}

function Field({ path, label, help, error, changed, children }) {
  return <label className={`block rounded-md border p-3 ${error ? "border-red-300 bg-red-50/40" : changed ? "border-blue-300 bg-blue-50/30" : "border-slate-200 bg-white"}`}><span className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-700"><span>{label}</span>{changed && <Badge variant="outline">Changed</Badge>}</span><span className="mt-2 block">{children}</span><span className={`mt-1 block text-[11px] ${error ? "text-red-700" : "text-slate-500"}`}>{error || help}</span><code className="mt-1 block text-[10px] text-slate-400">{path}</code></label>;
}

const textAreaClass = "min-h-20 w-full rounded-md border border-slate-200 bg-white p-2 text-sm text-slate-800 outline-none focus:border-dq-purple";

export default function DiagnosticPackagesPanel() {
  const [data, setData] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [editor, setEditor] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [reasons, setReasons] = useState({});

  const applyData = (value, resetEditor = false) => {
    setData(value);
    if (resetEditor || !editor) { setBaseline(clone(value.editor_template)); setEditor(clone(value.editor_template)); }
  };
  const reload = async (resetEditor = false) => {
    try { applyData(await getDiagnosticKbPackagesV3(), resetEditor); }
    catch (requestError) { setError(requestError.message); }
  };
  useEffect(() => {
    let current = true;
    getDiagnosticKbPackagesV3().then((value) => { if (current) { setData(value); setBaseline(clone(value.editor_template)); setEditor(clone(value.editor_template)); } }).catch((requestError) => { if (current) setError(requestError.message); });
    return () => { current = false; };
  }, []);

  const errors = useMemo(() => validateDiagnosticPackageEditor(editor), [editor]);
  const changes = useMemo(() => diagnosticPackageChanges(baseline, editor), [baseline, editor]);
  const changedPaths = useMemo(() => new Set(changes.map((change) => change.path)), [changes]);
  const valid = Object.keys(errors).length === 0;
  const setTop = (field, value) => setEditor((current) => ({ ...current, [field]: value }));
  const setSection = (section, field, value) => setEditor((current) => ({ ...current, [section]: { ...current[section], [field]: value } }));
  const setRule = (index, field, value) => setEditor((current) => ({ ...current, rules: current.rules.map((rule, ruleIndex) => ruleIndex === index ? { ...rule, [field]: value } : rule) }));

  const saveDraft = async () => {
    if (!valid || !changes.length) return;
    setBusy("save"); setError(""); setMessage("");
    try { const draft = await createDiagnosticKbDraftV3(editor); setMessage(`${draft.version_id} passed server validation and was saved as a draft. The active version is unchanged.`); await reload(true); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  };
  const activate = async (versionId) => {
    const reason = (reasons[versionId] || "").trim(); if (!reason) return;
    setBusy(versionId); setError(""); setMessage("");
    try { await activateDiagnosticKbPackageV3(versionId, reason); setMessage(`${versionId} is now active. New T2D6 manifests will use it; frozen runs remain unchanged.`); await reload(true); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  };

  if (!editor) return <div className="rounded-md border border-dashed border-slate-300 p-6 text-sm text-slate-500">Loading the governed T2D6 package editor…</div>;
  const drafts = (data?.packages || []).filter((item) => item.lifecycle_state === "draft");
  const history = (data?.packages || []).filter((item) => item.lifecycle_state !== "draft");
  return <div className="grid gap-5">
    {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
    {message && <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</div>}

    <section className="rounded-lg border border-slate-200 bg-white p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-dq-purple" /><h2 className="font-semibold text-slate-950">T2D6 Row Completeness rule-package editor</h2></div><p className="mt-1 text-xs text-slate-500">Editing active version <strong className="text-slate-800">{data?.active?.version_id}</strong>. Saving creates a draft only.</p></div><div className="flex gap-2"><Button variant="outline" disabled={Boolean(busy) || !changes.length} onClick={() => setEditor(clone(baseline))}><RotateCcw className="h-4 w-4" /> Reset edits</Button><Button disabled={Boolean(busy) || !valid || !changes.length} onClick={saveDraft}><Save className="h-4 w-4" />{busy === "save" ? "Validating…" : "Save validated draft"}</Button></div></div><div className={`mt-4 flex gap-2 rounded-md border p-3 text-xs ${valid ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-red-200 bg-red-50 text-red-900"}`}>{valid ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : <AlertTriangle className="h-4 w-4 shrink-0" />}<p><strong>{valid ? "All fields are valid." : `${Object.keys(errors).length} validation issue(s) must be resolved.`}</strong> {changes.length ? `${changes.length} governed value(s) differ from the active version.` : "Make at least one supported change before saving."}</p></div></section>

    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(20rem,1fr)]"><div className="grid gap-5">
      <section className="rounded-lg border border-slate-200 bg-white p-5"><h3 className="text-sm font-semibold text-slate-900">Change record</h3><p className="mt-1 text-xs text-slate-500">Required for reviewer context and the immutable activation audit.</p><div className="mt-3"><Field path="change_summary" label="Change summary" help="Explain what is changing and why. This does not alter diagnostic logic." error={errors.change_summary} changed={Boolean(editor.change_summary)}><textarea className={textAreaClass} value={editor.change_summary} onChange={(event) => setTop("change_summary", event.target.value)} placeholder="Describe the intended package change…" /></Field></div></section>
      <section className="rounded-lg border border-slate-200 bg-white p-5"><h3 className="text-sm font-semibold text-slate-900">Methodology explanation</h3><p className="mt-1 text-xs text-slate-500">Plain-language documentation only. The observed-span engine remains fixed.</p><div className="mt-3"><Field path="methodology" label="Methodology shown to users and reviewers" help="Describe the supported calculation without proposing new executable behavior." error={errors.methodology} changed={changedPaths.has("methodology")}><textarea className="min-h-36 w-full rounded-md border border-slate-200 bg-white p-2 text-sm outline-none focus:border-dq-purple" value={editor.methodology} onChange={(event) => setTop("methodology", event.target.value)} /></Field></div></section>
      <section className="rounded-lg border border-slate-200 bg-white p-5"><h3 className="text-sm font-semibold text-slate-900">Diagnostic presentation</h3><p className="mt-1 text-xs text-slate-500">These fields update future Test Lab and report wording after activation.</p><div className="mt-3 grid gap-3">{Object.keys(labels).filter((field) => field in editor.diagnostic).map((field) => { const path = `diagnostic.${field}`; return <Field key={field} path={path} label={labels[field]} help={guidance[field]} error={errors[path]} changed={changedPaths.has(path)}><textarea className={textAreaClass} value={editor.diagnostic[field]} onChange={(event) => setSection("diagnostic", field, event.target.value)} /></Field>; })}</div></section>
      <section className="rounded-lg border border-slate-200 bg-white p-5"><h3 className="text-sm font-semibold text-slate-900">Configuration defaults</h3><p className="mt-1 text-xs text-slate-500">These become defaults for future manifests after activation.</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><Field path="configuration.default_reporting_grain" label="Default reporting grain" help="Select one supported calendar grain." error={errors["configuration.default_reporting_grain"]} changed={changedPaths.has("configuration.default_reporting_grain")}><select className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={editor.configuration.default_reporting_grain} onChange={(event) => setSection("configuration", "default_reporting_grain", event.target.value)}>{REPORTING_GRAINS.map((grain) => <option key={grain}>{grain}</option>)}</select></Field><Field path="configuration.default_continuity_floor" label="Default required-row coverage" help="A decimal from 0 through 1; 0.95 means 95%." error={errors["configuration.default_continuity_floor"]} changed={changedPaths.has("configuration.default_continuity_floor")}><div className="flex items-center gap-2"><input type="number" min="0" max="1" step="0.01" className="h-10 w-full rounded-md border border-slate-200 px-3 text-sm" value={editor.configuration.default_continuity_floor} onChange={(event) => setSection("configuration", "default_continuity_floor", event.target.value === "" ? "" : Number(event.target.value))} /><span className="text-sm font-semibold text-slate-600">{Number.isFinite(Number(editor.configuration.default_continuity_floor)) ? `${(Number(editor.configuration.default_continuity_floor) * 100).toFixed(0)}%` : "—"}</span></div></Field>{["continuity_floor_label", "continuity_floor_help"].map((field) => { const path = `configuration.${field}`; return <Field key={field} path={path} label={labels[field]} help={guidance[field]} error={errors[path]} changed={changedPaths.has(path)}><textarea className={textAreaClass} value={editor.configuration[field]} onChange={(event) => setSection("configuration", field, event.target.value)} /></Field>; })}</div></section>
      <section><div className="mb-3"><h3 className="text-sm font-semibold text-slate-900">Rule presentation and guidance</h3><p className="mt-1 text-xs text-slate-500">Execution bindings are read-only. Only the labelled fields can be edited.</p></div><div className="grid gap-3">{editor.rules.map((rule, index) => <article key={rule.rule_id} className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><div className="flex items-center gap-2"><strong className="text-sm text-slate-950">{rule.rule_id}</strong><Badge variant="outline">Order {rule.display_order}</Badge><Badge variant={rule.optional ? "secondary" : "outline"}>{rule.optional ? "Optional" : "Required"}</Badge></div><p className="mt-1 text-[11px] text-slate-500">Primitive: <code>{rule.primitive}</code> · roles: {rule.required_roles.join(", ")} · coverage floor: {rule.uses_continuity_floor ? "used" : "not used"}</p></div></div><div className="mt-3 grid gap-3 sm:grid-cols-2">{[["title", "Rule title", "Short user-facing name."], ["user_help", "What this rule checks", "Explain the check in plain language."], ["next_step", "Recommended next step", "Tell users what to investigate or correct."]].map(([field, label, help]) => { const path = `rules.${rule.rule_id}.${field}`; return <Field key={field} path={path} label={label} help={help} error={errors[path]} changed={changedPaths.has(path)}><textarea className={textAreaClass} value={rule[field]} onChange={(event) => setRule(index, field, event.target.value)} /></Field>; })}<Field path={`rules.${rule.rule_id}.severity`} label="Severity" help="Severity affects issue prioritization, not the deterministic verdict." error={errors[`rules.${rule.rule_id}.severity`]} changed={changedPaths.has(`rules.${rule.rule_id}.severity`)}><select className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={rule.severity} onChange={(event) => setRule(index, "severity", event.target.value)}>{SEVERITIES.map((severity) => <option key={severity}>{severity}</option>)}</select></Field></div></article>)}</div></section>
    </div><aside className="sticky top-4 grid gap-4"><section className="rounded-lg border border-slate-200 bg-white p-4"><h3 className="text-sm font-semibold text-slate-900">Live change preview ({changes.length})</h3><p className="mb-3 mt-1 text-xs text-slate-500">Only these values will differ in the draft.</p><ChangeList changes={changes} /></section><section className="rounded-lg border border-amber-200 bg-amber-50 p-4"><div className="flex gap-2"><AlertTriangle className="h-4 w-4 shrink-0 text-amber-700" /><div><h3 className="text-sm font-semibold text-amber-950">Fixed engine guardrails</h3><p className="mt-1 text-xs text-amber-900">Rule IDs, order, primitives, semantic roles, optionality, floor bindings, diagnostic identity and engine contract cannot be edited here.</p></div></div></section></aside></div>

    <section><h2 className="mb-3 text-sm font-semibold text-slate-900">Drafts awaiting review ({drafts.length})</h2><div className="grid gap-3">{drafts.map((item) => <article key={item.package_id} className="rounded-lg border border-amber-200 bg-white p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><div className="flex items-center gap-2"><FileJson className="h-4 w-4 text-amber-700" /><strong className="text-sm text-slate-900">{item.version_id}</strong><PackageBadge state={item.lifecycle_state} /></div><p className="mt-1 text-xs text-slate-500">Based on {item.based_on_version_id} · created by {item.created_by}</p></div>{item.validation_json?.valid && <span className="flex items-center gap-1 text-xs font-medium text-emerald-700"><CheckCircle2 className="h-4 w-4" /> Server contract valid</span>}</div><p className="my-3 rounded-md bg-slate-50 p-3 text-xs text-slate-700"><strong>Change summary:</strong> {item.change_summary}</p><ChangeList changes={item.changes} /><div className="mt-3 flex flex-wrap gap-2"><input value={reasons[item.version_id] || ""} onChange={(event) => setReasons((current) => ({ ...current, [item.version_id]: event.target.value }))} placeholder="Activation rationale (required)" className="h-9 min-w-72 flex-1 rounded-md border border-slate-200 px-3 text-xs" /><Button size="sm" disabled={busy === item.version_id || !(reasons[item.version_id] || "").trim()} onClick={() => activate(item.version_id)}><ShieldCheck className="h-4 w-4" />{busy === item.version_id ? "Activating…" : "Review and activate"}</Button></div></article>)}{!drafts.length && <div className="rounded-md border border-dashed border-slate-200 p-6 text-center text-xs text-slate-500">No drafts are awaiting review.</div>}</div></section>
    <section><h2 className="mb-3 text-sm font-semibold text-slate-900">Version history</h2><div className="overflow-hidden rounded-lg border border-slate-200 bg-white">{history.map((item) => <div key={item.package_id} className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-3 first:border-t-0"><div><div className="flex items-center gap-2"><strong className="text-sm text-slate-800">{item.version_id}</strong><PackageBadge state={item.lifecycle_state} /></div><p className="mt-1 text-xs text-slate-500">{item.change_summary} · hash {item.package_hash?.slice(0, 12)}…</p></div><span className="text-xs text-slate-400">{item.activated_at || item.created_at}</span></div>)}</div></section>
  </div>;
}
