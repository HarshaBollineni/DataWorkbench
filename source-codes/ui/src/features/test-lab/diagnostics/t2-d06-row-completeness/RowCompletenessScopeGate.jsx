import { useState } from "react";
import {
  Bot, CalendarDays, CheckCircle2, Database, Info, KeyRound, Layers3, Lock,
  Play, ShieldCheck, SlidersHorizontal, Table2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  FLOOR_RULES, ROW_COMPLETENESS_RULE_HELP, roleSourceLabel,
  rowCompletenessValidation, tableOptionSummary,
} from "./rowCompletenessWorkflow";

const ROLE_PRESENTATION = {
  facility_id: {
    label: "Facility identifier", required: true, Icon: KeyRound,
    help: "The entity whose reporting timeline will be assessed.",
  },
  period: {
    label: "Reporting period", required: true, Icon: CalendarDays,
    help: "The period attached to each delivered facility row.",
  },
  segment: {
    label: "Segment", required: false, Icon: Layers3,
    help: "Optional. Enables the segment-period rule when each facility has a stable segment.",
  },
};

function Section({ title, description, icon: Icon, children }) {
  return <section className="rounded-lg border border-slate-200 bg-white p-5">
    <div className="mb-4 flex items-start gap-3">
      {Icon && <span className="rounded-md bg-violet-50 p-2 text-dq-purple"><Icon className="h-4 w-4" /></span>}
      <div><h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {description && <p className="mt-1 text-xs text-slate-500">{description}</p>}</div>
    </div>
    {children}
  </section>;
}

function RoleControl({ role, binding, columns, frozen, busy, onChange }) {
  const presentation = ROLE_PRESENTATION[role];
  const { Icon } = presentation;
  return <div className={`rounded-lg border p-4 ${binding ? "border-slate-200 bg-white" : presentation.required ? "border-amber-300 bg-amber-50/50" : "border-slate-200 bg-slate-50/60"}`}>
    <div className="flex items-start justify-between gap-2">
      <div className="flex items-center gap-2"><Icon className="h-4 w-4 text-slate-500" />
        <span className="text-sm font-semibold text-slate-800">{presentation.label}</span></div>
      <Badge variant={presentation.required ? "outline" : "secondary"}>{presentation.required ? "Required" : "Optional"}</Badge>
    </div>
    <p className="mt-2 min-h-8 text-xs text-slate-500">{presentation.help}</p>
    <label className="mt-3 grid gap-1 text-xs text-slate-600">
      <span className="sr-only">{presentation.label}</span>
      <select aria-label={presentation.label} value={binding?.column || ""} disabled={frozen || busy}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900">
        <option value="">{role === "segment" ? "No segment analysis" : `Select ${presentation.label.toLowerCase()}`}</option>
        {columns.map((column) => <option key={column} value={column}>{column}</option>)}
      </select>
    </label>
    <div className="mt-3 border-t border-slate-100 pt-3 text-[11px] text-slate-500">
      <p className="font-medium text-slate-700">{roleSourceLabel(binding)}{binding?.score != null ? ` · ${Math.round(binding.score * 100)}% confidence` : ""}</p>
      <p className="mt-1">{binding?.reason || (role === "segment" ? "Segment-level analysis will be skipped." : "A manual selection is required.")}</p>
    </div>
  </div>;
}

function ContinuityFloor({ spec, label, help, frozen, busy, onSave, onDirtyChange }) {
  const [value, setValue] = useState(String(Math.round(Number(spec?.value ?? 0.95) * 100)));
  const numeric = Number(value);
  const valid = Number.isFinite(numeric) && numeric >= 0 && numeric <= 100;
  const changed = valid && numeric / 100 !== Number(spec?.value);
  return <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-4">
    <label className="grid gap-1 text-xs font-medium text-slate-700" htmlFor="row-continuity-floor">{label || "Required row coverage"}</label>
    <div className="mt-2 flex items-center gap-2">
      <div className="relative w-32"><input id="row-continuity-floor" type="number" min="0" max="100" step="1"
        value={value} disabled={frozen || busy} onChange={(event) => {
          const next = event.target.value;
          setValue(next);
          const parsed = Number(next);
          onDirtyChange(Number.isFinite(parsed) && parsed >= 0 && parsed <= 100
            ? parsed / 100 !== Number(spec?.value) : true);
        }}
        className={`h-10 w-full rounded-md border bg-white px-3 pr-8 text-sm ${valid ? "border-slate-200" : "border-red-400"}`} />
        <span className="pointer-events-none absolute right-3 top-2.5 text-sm text-slate-400">%</span></div>
      {!frozen && <Button size="sm" variant="outline" disabled={busy || !changed}
        onClick={async () => { if (await onSave(numeric / 100)) onDirtyChange(false); }}>Save</Button>}
    </div>
    {!valid && <p className="mt-1 text-xs text-red-600">Enter a value from 0 to 100.</p>}
    <p className="mt-2 text-[11px] text-slate-500">{spec?.source || "default"} · {help || "One floor is shared by all coverage-based rules."}</p>
  </div>;
}

function AdvisoryRoleReview({ manifest, frozen, busy, onPatch }) {
  const review = manifest.role_verification || {};
  const suggestions = review.suggestions || {};
  const [applied, setApplied] = useState({});
  const applySuggestion = async (role, column) => {
    const updated = await onPatch({ kind: "role_override", role, column });
    if (updated) setApplied((value) => ({ ...value, [role]: "applied" }));
  };
  const keepCurrent = async (role, column) => {
    const updated = await onPatch({ kind: "role_override", role, column });
    if (updated) setApplied((value) => ({ ...value, [role]: "kept" }));
  };
  return <Section title="Optional advisory role review"
    description="A governed LLM can review bounded column candidates. It cannot read row values, change a mapping automatically, calculate a result, or determine the verdict." icon={Bot}>
    {review.status !== "completed" ? <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
      <div><p className="text-sm font-medium text-slate-800">{review.status === "failed" ? "Advisory review was unavailable" : "Deterministic mappings remain authoritative"}</p>
        {review.status === "failed" && <p className="mt-1 text-xs text-amber-700">{review.error} The failed call remains recorded in inference provenance.</p>}
        <p className="mt-1 text-xs text-slate-500">Requesting a review creates one documented LLM call. Running the diagnostic does not require it.</p></div>
      {!frozen && <Button size="sm" variant="outline" disabled={busy}
        onClick={() => onPatch({ kind: "role_verification_change", enabled: true })}>
        <Bot className="h-4 w-4" />{busy ? "Requesting review..." : "Request advisory review"}
      </Button>}
    </div> : <div className="grid gap-2">
      <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">
        Review completed with {review.model || "the governed role-review model"}. Suggestions stay advisory until you apply one below.
      </div>
      {Object.keys(ROLE_PRESENTATION).map((role) => {
        const suggestion = suggestions[role];
        const current = manifest.roles?.[role]?.column || null;
        const same = suggestion === current;
        return <div key={role} className="grid items-center gap-2 rounded-md border border-slate-200 p-3 md:grid-cols-[11rem_1fr_auto]">
          <div><p className="text-xs font-semibold text-slate-800">{ROLE_PRESENTATION[role].label}</p><p className="text-[11px] text-slate-500">Current: {current || "Not bound"}</p></div>
          <p className="text-xs text-slate-600">Suggested: <strong className="text-slate-900">{suggestion || "No binding"}</strong>{same ? " (matches current)" : ""}</p>
          <div className="flex items-center gap-2">
            {applied[role] ? <Badge variant={applied[role] === "applied" ? "success" : "secondary"}>{applied[role] === "applied" ? "Applied by user" : "Current mapping kept"}</Badge>
              : !frozen && <><Button size="sm" variant="outline" disabled={busy || (!current && role !== "segment")} onClick={() => keepCurrent(role, current)}>Keep current</Button>
                {!same && <Button size="sm" variant="outline" disabled={busy || (!suggestion && role !== "segment")}
                  onClick={() => applySuggestion(role, suggestion)}>Apply suggestion</Button>}</>}
            {same && frozen && <Badge variant="secondary">Kept</Badge>}
          </div>
        </div>;
      })}
    </div>}
  </Section>;
}

function DatasetStructureAssist({ manifest, frozen, busy, onPatch }) {
  const assist = manifest.dsc_assist || {};
  if (assist.state !== "available") return null;
  const cadence = assist.expected_cadence;
  return <Section title="Dataset Structure suggestions"
    description="Confirmed Dataset Structure selections are editable starting points. D06 keeps ownership of its scope and reporting-grain interpretation." icon={ShieldCheck}>
    <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900">
      <p className="font-semibold">Review these suggested facility and period fields before execution.</p>
      <p className="mt-1">They are not an execution decision and do not change completed runs, D06 calculations, or reporting grain.</p>
      {cadence && <p className="mt-2">Dataset Structure expected cadence: <strong>{cadence.step} {cadence.unit}{cadence.step === 1 ? "" : "s"}</strong>. It is advisory; the selected D06 reporting grain remains unchanged unless you choose a different grain below.</p>}
    </div>
    {!frozen && <label className="mt-3 flex items-start gap-2 rounded-md border border-slate-200 p-3 text-xs text-slate-700">
      <input type="checkbox" checked={Boolean(assist.scope_confirmed)} disabled={busy}
        onChange={(event) => event.target.checked && onPatch({ kind: "dsc_assist_confirmation", confirmed: true })} />
      <span>I reviewed the D06 scope. I accept these suggestions or the manual values currently selected.</span>
    </label>}
    {assist.scope_confirmed && <Badge className="mt-3" variant="success">D06 scope confirmation recorded</Badge>}
  </Section>;
}

export default function RowCompletenessScopeGate({ run, manifest, busy, patch, runNow }) {
  const [floorDirty, setFloorDirty] = useState(false);
  const frozen = run.status !== "draft";
  const validationIssues = rowCompletenessValidation(manifest);
  const blockingIssues = floorDirty ? [...validationIssues, "Save the continuity floor change."] : validationIssues;
  const selectedTable = (manifest.table_options || []).find((option) => option.table === manifest.table);
  const disclosure = manifest.inference_disclosure || {};
  const safePatch = (body) => patch(body).catch(() => null);

  return <div className="grid gap-4" data-testid="row-completeness-scope">
    <div className="flex flex-wrap items-start justify-between gap-4 rounded-lg border border-slate-200 bg-white p-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold text-slate-950">Row Completeness · configuration</h2>
          <Badge variant={frozen ? "secondary" : "outline"} className="gap-1">
            {frozen && <Lock className="h-3 w-3" />}{run.status}
          </Badge>
        </div>
        <p className="mt-1 text-xs text-slate-500">{manifest.item_name} · immutable snapshot {manifest.snapshot?.snapshot_id} · run {manifest.run_id}</p>
      </div>
      <div className={`rounded-md px-3 py-2 text-xs font-medium ${validationIssues.length ? "bg-amber-50 text-amber-800" : "bg-emerald-50 text-emerald-800"}`}>
        {blockingIssues.length ? `${blockingIssues.length} configuration item${blockingIssues.length === 1 ? "" : "s"} to resolve`
          : <span className="flex items-center gap-1"><CheckCircle2 className="h-4 w-4" /> Configuration ready</span>}
        <p className="mt-1 font-normal opacity-80">Configuration review slice · execution has not started.</p>
      </div>
    </div>

    <div className="rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-xs text-blue-900">
      <p className="flex items-center gap-2 font-semibold"><Info className="h-4 w-4" /> Observed-span methodology</p>
      <p className="mt-1">{manifest.kb?.methodology_summary || "Required facility-period rows are inferred only between each facility's first and last observed reporting periods. This setup screen does not assume an external facility population."}</p>
    </div>

    <Section title="1. Select the table" description="The calculation runs against one immutable table. Role suggestions update when the table changes." icon={Table2}>
      <div className="grid gap-3 lg:grid-cols-[minmax(260px,1fr)_2fr]">
        <label className="grid gap-1 text-xs text-slate-600"><span>Table to assess</span>
          <select aria-label="Table to assess" value={manifest.table || ""} disabled={frozen || busy}
            onChange={(event) => safePatch({ kind: "table_selection", table: event.target.value })}
            className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900">
            {(manifest.table_options || []).map((option) => <option key={option.table} value={option.table}>{option.table}</option>)}
          </select></label>
        {selectedTable && <div className="grid grid-cols-2 gap-2 rounded-md border border-slate-100 bg-slate-50 p-3 text-xs sm:grid-cols-4">
          <div><p className="text-slate-400">Rows</p><p className="font-semibold text-slate-800">{selectedTable.row_count ?? "Unknown"}</p></div>
          <div><p className="text-slate-400">Columns</p><p className="font-semibold text-slate-800">{selectedTable.column_count}</p></div>
          <div className="col-span-2"><p className="text-slate-400">Metadata signals</p><p className="font-medium text-slate-700">{tableOptionSummary(selectedTable)}</p></div>
        </div>}
      </div>
    </Section>

    <Section title="2. Confirm semantic roles" description="Facility and period are required. Segment is optional and can be left unbound." icon={KeyRound}>
      <div className="grid gap-3 lg:grid-cols-3">
        {Object.keys(ROLE_PRESENTATION).map((role) => <RoleControl key={role} role={role}
          binding={manifest.roles?.[role]} columns={manifest.available_columns || []}
          frozen={frozen} busy={busy}
          onChange={(column) => safePatch({ kind: "role_override", role, column })} />)}
      </div>
    </Section>

    <DatasetStructureAssist manifest={manifest} frozen={frozen} busy={busy} onPatch={safePatch} />

    <AdvisoryRoleReview manifest={manifest} frozen={frozen} busy={busy} onPatch={safePatch} />

    <Section title="3. Confirm time interpretation and the single floor"
      description="The reporting grain controls period parsing. The continuity floor is used only by coverage rules." icon={SlidersHorizontal}>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-4">
          <label className="grid gap-1 text-xs font-medium text-slate-700"><span>Reporting grain</span>
            <select aria-label="Reporting grain" value={manifest.configuration?.reporting_grain?.value || ""}
              disabled={frozen || busy}
              onChange={(event) => safePatch({ kind: "parameter_tune", key: "reporting_grain", value: event.target.value })}
              className="mt-1 h-10 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900">
              <option value="monthly">Monthly</option><option value="quarterly">Quarterly</option>
              <option value="semiannual">Semiannual</option><option value="annual">Annual</option>
            </select></label>
          <p className="mt-2 text-[11px] text-slate-500">{manifest.configuration?.reporting_grain?.source} · confirm this visibly before execution.</p>
        </div>
        <ContinuityFloor key={manifest.configuration?.continuity_floor?.value} spec={manifest.configuration?.continuity_floor}
          label={manifest.kb?.configuration?.continuity_floor_label}
          help={manifest.kb?.configuration?.continuity_floor_help} frozen={frozen} busy={busy}
          onDirtyChange={setFloorDirty} onSave={(value) => safePatch({ kind: "threshold_tune", key: "continuity_floor", value })} />
      </div>
    </Section>

    <Section title="4. Review the six fixed rules" description="Rules 1–5 always run. Rule 6 is included only when a segment role is bound." icon={ShieldCheck}>
      <div className="grid gap-2 md:grid-cols-2">
        {(manifest.kb?.rules || []).map((rule, index) => {
          const segmentRule = rule.rule_id === "T2D6-06";
          const applicable = !segmentRule || Boolean(manifest.roles?.segment);
          return <div key={rule.rule_id} className="rounded-md border border-slate-200 p-3">
            <div className="flex items-start gap-3"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">{index + 1}</span>
              <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-slate-800">{rule.rule_id} · {rule.title}</p>
                {(typeof rule.uses_continuity_floor === "boolean"
                  ? rule.uses_continuity_floor : FLOOR_RULES.has(rule.rule_id))
                  && <Badge variant="outline">Uses coverage floor</Badge>}
                {segmentRule && <Badge variant={applicable ? "success" : "secondary"}>{applicable ? "Included" : "Not applicable"}</Badge>}
              </div><p className="mt-1 text-xs text-slate-500">{rule.user_help || ROW_COMPLETENESS_RULE_HELP[rule.rule_id]}</p></div>
            </div>
          </div>;
        })}
      </div>
    </Section>

    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="Inference disclosure" icon={ShieldCheck}>
        <p className="text-sm font-semibold text-slate-800">{disclosure.llm_call_count || 0} LLM calls</p>
        <p className="mt-1 text-xs text-slate-500">{disclosure.statement || "No LLM calls were made during this diagnostic journey."}</p>
        <p className="mt-2 text-[11px] text-slate-500">Deterministic role suggestions: {(disclosure.deterministic_inferences || []).length} · LLM output never calculates metrics or determines the verdict.</p>
      </Section>
      <Section title="Analytics Artifact Repository inputs" icon={Database}>
        <p className="text-sm font-semibold text-slate-800">{(manifest.source_artifact_references || []).length} governed source artifacts</p>
        <ul className="mt-2 grid gap-1 text-[11px] text-slate-500">
          {(manifest.source_artifact_references || []).map((reference) => <li key={reference.artifact_id} className="truncate" title={reference.artifact_id}>{reference.role} · {reference.artifact_id}</li>)}
          {!(manifest.source_artifact_references || []).length && <li>No reusable profile artifact was available; normalized Data Sourcing metadata is retained in the manifest.</li>}
        </ul>
      </Section>
    </div>

    {blockingIssues.length > 0 && <div className="rounded-md border border-amber-200 bg-amber-50 p-4">
      <p className="text-sm font-semibold text-amber-900">Complete the configuration</p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-800">{blockingIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
    </div>}
    {!frozen && <div className="sticky bottom-3 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-violet-200 bg-white/95 p-4 shadow-lg backdrop-blur">
      <div><p className="text-sm font-semibold text-slate-900">Ready to assess row completeness?</p>
        <p className="text-xs text-slate-500">The run will freeze this table, role mapping, grain, and floor.</p></div>
      <Button onClick={runNow} disabled={busy || blockingIssues.length > 0}>
        <Play className="h-4 w-4" />{busy ? "Preparing run..." : "Run diagnostic"}
      </Button>
    </div>}
  </div>;
}
