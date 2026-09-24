import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";

import { saveStagedStructureReviewV2 } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  restoreStagedStructureChoices, stagedStructureReviewComplete, stagedStructureSelections,
} from "./stagedStructureReview";

const NONE = "__none__";
const percent = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;
const hasSelection = (selection, field) => Object.prototype.hasOwnProperty.call(selection || {}, field);

function AnalysisWait() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const startedAt = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, []);
  const elapsedLabel = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  return <div className="rounded-lg border border-teal-100 bg-emerald-50/40 p-4" aria-live="polite"><div className="flex flex-wrap items-center justify-between gap-2"><p className="flex items-center gap-2 text-sm text-teal-900"><Loader2 className="h-4 w-4 animate-spin" />Analyzing entity, temporal, row-grain, and cadence evidence…</p><span className="text-sm font-semibold tabular-nums text-teal-800">Elapsed {elapsedLabel}</span></div><div className="mt-3 h-2 overflow-hidden rounded-full bg-teal-100"><div className="h-full w-1/3 animate-pulse rounded-full bg-teal-600" /></div></div>;
}

function FacetSelect({ table, title, hint, field, candidates, selection, onChange }) {
  const selectedId = selection[field];
  const selected = candidates.find((candidate) => candidate.candidate_id === selectedId);
  const recommended = candidates.find((candidate) => candidate.recommended);
  const acknowledgementKey = field === "default_entity_candidate_id" ? "entity_acknowledged"
    : field === "default_temporal_candidate_id" ? "temporal_acknowledged" : "row_grain_acknowledged";
  const limited = selectedId === null || (field === "row_grain_candidate_id" && selected?.quality !== "exact");
  const label = (candidate) => candidate.column || candidate.columns?.join(" + ");
  return <section className="rounded-md border border-slate-200 bg-white p-4">
    <h4 className="font-semibold text-slate-900">{title}</h4><p className="mt-1 text-xs text-slate-500">{hint}</p>
    <select aria-label={`${title} for ${table.table}`} className="mt-3 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm" value={selectedId === null ? NONE : selectedId || ""} onChange={(event) => onChange(field, event.target.value === NONE ? null : event.target.value)}>
      <option value="">Select after reviewing evidence</option>{candidates.map((candidate) => <option key={candidate.candidate_id} value={candidate.candidate_id}>{label(candidate)}{candidate.recommended ? " — Recommended" : ""}</option>)}<option value={NONE}>No applicable selection</option>
    </select>
    {recommended && !hasSelection(selection, field) && <p className="mt-2 text-xs font-medium text-indigo-700">System recommendation: <strong>{label(recommended)}</strong>. It is not selected automatically.</p>}
    {selected && <p className="mt-2 text-xs text-slate-600">{field === "default_entity_candidate_id" ? `${Number(selected.distinct_key_count).toLocaleString()} distinct entities · ${Number(selected.duplicate_excess_rows).toLocaleString()} duplicate excess rows · ${percent(selected.key_coverage)} populated` : field === "default_temporal_candidate_id" ? `${selected.role} · ${Number(selected.distinct_value_count).toLocaleString()} distinct values · ${percent(selected.key_coverage)} populated` : `${Number(selected.distinct_key_count).toLocaleString()} distinct keys across ${Number(selected.usable_rows).toLocaleString()} usable rows · ${percent(selected.uniqueness_ratio)} unique`}</p>}
    {selected?.deduplication_recommended && <p className="mt-2 text-xs font-medium text-amber-800">Repeated keys contain identical rows; deduplication is recommended before final ingestion.</p>}
    {limited && <label className="mt-3 flex items-start gap-2 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-950"><input type="checkbox" checked={Boolean(selection[acknowledgementKey])} onChange={(event) => onChange(acknowledgementKey, event.target.checked)} className="mt-0.5" />I acknowledge that this declaration may limit downstream diagnostics.</label>}
  </section>;
}

function CadenceReview({ table, selection, onChange }) {
  const observed = table.cadences.find((candidate) => candidate.entity_candidate_id === selection.default_entity_candidate_id && candidate.temporal_candidate_id === selection.default_temporal_candidate_id);
  const cadence = selection.expected_cadence;
  const action = cadence?.action || "";
  const chooseAction = (nextAction) => {
    if (!nextAction) return onChange("expected_cadence", undefined);
    if (nextAction === "confirm") onChange("expected_cadence", { action: "confirm", value: observed?.interval || { unit: "month", step: 1 } });
    else onChange("expected_cadence", { action: nextAction, acknowledged: false });
  };
  return <section className="mt-3 rounded-md border border-slate-200 bg-white p-4"><h4 className="font-semibold text-slate-900">Expected cadence</h4><div className="mt-3 grid gap-4 md:grid-cols-2"><div><h5 className="text-sm font-semibold">Observed</h5><p className="mt-1 text-xs text-slate-500">Read-only frequency for the selected entity and temporal feature.</p><p className="mt-2 text-sm text-slate-700">{!selection.default_entity_candidate_id || !selection.default_temporal_candidate_id ? "Select an entity and temporal feature to view cadence evidence." : observed?.cadence === "regular" && observed.interval ? `Regular · every ${observed.interval.step} ${observed.interval.unit}${observed.interval.step === 1 ? "" : "s"}` : observed ? `${observed.cadence.replace("_", " ")} · no regular interval recommendation` : "Cadence evidence is unavailable for this combination."}</p></div><div className="border-t border-slate-200 pt-3 md:border-l md:border-t-0 md:pl-4 md:pt-0"><h5 className="text-sm font-semibold">User declaration</h5><p className="mt-1 text-xs text-slate-500">Recommendations remain unselected until you explicitly declare the expected frequency.</p><select aria-label={`Expected cadence action for ${table.table}`} className="mt-2 h-9 rounded border border-slate-200 bg-white px-2 text-sm" value={action} onChange={(event) => chooseAction(event.target.value)}><option value="">Select expected cadence action</option>{selection.default_temporal_candidate_id && <option value="confirm">Declare expected cadence</option>}<option value="clear">No expected cadence</option><option value="mark_not_applicable">Not applicable</option></select>{action === "confirm" && <div className="mt-2 flex gap-2"><select aria-label={`Expected cadence unit for ${table.table}`} className="h-9 rounded border border-slate-200 px-2 text-sm" value={cadence.value?.unit || "month"} onChange={(event) => onChange("expected_cadence", { ...cadence, value: { ...cadence.value, unit: event.target.value } })}>{["day", "week", "month", "quarter", "year"].map((unit) => <option key={unit}>{unit}</option>)}</select><Input aria-label={`Expected cadence step for ${table.table}`} className="h-9 w-20" type="number" min="1" value={cadence.value?.step || 1} onChange={(event) => onChange("expected_cadence", { ...cadence, value: { ...cadence.value, step: Math.max(1, Number(event.target.value) || 1) } })} /></div>}{["clear", "mark_not_applicable"].includes(action) && <label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={Boolean(cadence.acknowledged)} onChange={(event) => onChange("expected_cadence", { ...cadence, acknowledged: event.target.checked })} />I acknowledge this cadence declaration.</label>}</div></div></section>;
}

export default function StagedStructureReview({ itemId, inventoryRows, precheck, loading, onSaved, onReadyChange }) {
  const [choices, setChoices] = useState(() => restoreStagedStructureChoices(precheck));
  const [revision, setRevision] = useState(() => Number(precheck?.draft?.revision || 0));
  const [dirty, setDirty] = useState(() => Boolean(precheck?.draft?.stale || !precheck?.draft?.revision));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const complete = useMemo(() => stagedStructureReviewComplete(precheck, choices), [choices, precheck]);
  const ready = complete && !dirty && !precheck?.draft?.stale;
  useEffect(() => { onReadyChange?.(ready); }, [onReadyChange, ready]);
  const change = (table, key, value) => {
    setChoices((current) => {
      const next = { ...(current[table] || {}), [key]: value };
      if (["default_entity_candidate_id", "default_temporal_candidate_id", "row_grain_candidate_id"].includes(key)) {
        const acknowledgement = key === "default_entity_candidate_id" ? "entity_acknowledged" : key === "default_temporal_candidate_id" ? "temporal_acknowledged" : "row_grain_acknowledged";
        next[acknowledgement] = false;
      }
      if (["default_entity_candidate_id", "default_temporal_candidate_id"].includes(key)) delete next.expected_cadence;
      return { ...current, [table]: next };
    });
    setDirty(true);
  };
  const save = async () => {
    if (!complete || saving) return;
    setSaving(true); setError("");
    try {
      const result = await saveStagedStructureReviewV2(itemId, { inventory_rows: inventoryRows, evidence_fingerprint: precheck.evidence_fingerprint, selections: { tables: stagedStructureSelections(precheck, choices) }, expected_revision: revision });
      setRevision(Number(result.draft?.revision || revision + 1)); setDirty(false); onSaved?.(result);
    } catch (failure) { setDirty(true); setError(typeof failure.detail === "string" ? failure.detail : failure.message); }
    finally { setSaving(false); }
  };
  if (loading || !precheck) return <AnalysisWait />;
  return <div><div className="rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-950"><strong>Dataset Structure Contract</strong><p className="mt-1">Review Entity, Date/Period, Row Grain, and Expected Cadence together. Row grain is limited to one or two Identifier, Period, or Date columns, and every system recommendation requires an explicit choice.</p></div>{precheck.draft?.stale && <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" role="alert"><AlertTriangle className="mr-1 inline h-4 w-4" /><strong>Evidence changed.</strong> Review all refreshed selections before continuing.</div>}<div className="mt-4 space-y-4">{precheck.tables.map((table) => { const selection = choices[table.table] || {}; return <section key={table.table} className="rounded-lg border border-slate-200 bg-slate-50/50 p-4"><h3 className="font-semibold text-slate-950">{table.table}</h3><div className="mt-3 grid gap-3 xl:grid-cols-3"><FacetSelect table={table} title="Entity identifier" hint="The identifier used to follow an entity across observations." field="default_entity_candidate_id" candidates={table.entities} selection={selection} onChange={(key, value) => change(table.table, key, value)} /><FacetSelect table={table} title="Date or Period feature" hint="The temporal axis that explains when each observation applies." field="default_temporal_candidate_id" candidates={table.temporals} selection={selection} onChange={(key, value) => change(table.table, key, value)} /><FacetSelect table={table} title="Row grain" hint="The one- or two-column combination that identifies a row." field="row_grain_candidate_id" candidates={table.candidates} selection={selection} onChange={(key, value) => change(table.table, key, value)} /></div><CadenceReview table={table} selection={selection} onChange={(key, value) => change(table.table, key, value)} /></section>; })}</div><div className="mt-4 flex flex-wrap items-center gap-3"><Button type="button" onClick={save} disabled={!complete || saving || !dirty}>{saving && <Loader2 className="h-4 w-4 animate-spin" />}{saving ? "Saving structure review…" : ready ? "Structure review saved" : "Save structure review"}</Button>{ready && <span className="flex items-center gap-1 text-sm font-medium text-emerald-700"><CheckCircle2 className="h-4 w-4" />Saved against current evidence</span>}</div>{!complete && <p className="mt-2 text-xs text-slate-500">Make an explicit Entity, Date/Period, Row Grain, and Expected Cadence declaration for every table.</p>}{error && <p className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-sm text-red-800" role="alert">{error}</p>}</div>;
}
