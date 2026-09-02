import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { RotateCcw, Save, Search, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { getInventoryV2, getItemTablesV2, putInventoryV2 } from "@/api/client";
import {
  parseSpecialValueInput, specialValueIssue, specialValueIssues, specialValuePlaceholder,
  specialValuesFor,
} from "@/lib/specialValueValidation";

const FORMATS = ["numerical", "datetime", "text"];
const FORMAT_LABEL = { numerical: "Numeric", datetime: "Date", text: "String" };
const ROLES = ["Feature", "Target", "Period", "Date", "Weight", "Group", "Identifier", "Ignore"];

const formatFor = (row, observed = false) => {
  const classification = String(observed ? row.inferred_type : row.classification || "").toLowerCase();
  const dataType = String(row.data_type || "").toLowerCase();
  if (["datetime", "date", "timestamp"].includes(classification)) return "datetime";
  if (["numerical", "numeric"].includes(classification)) return "numerical";
  if (/int|float|double|decimal|number/.test(dataType) && classification !== "datetime") return "numerical";
  return "text";
};

const validValuesFor = (row) => {
  const profile = row.profile_json || {};
  const values = profile.top_values || profile.top_k;
  if (values && Object.keys(values).length <= 12) return Object.keys(values).join(", ");
  if (profile.min != null || profile.max != null) return `${profile.min ?? "—"} — ${profile.max ?? "—"}`;
  return "—";
};

const distinctCountFor = (row) => {
  const profile = row.profile_json || {};
  const candidates = [row.distinct_count, profile.distinct_count, profile.cardinality, profile.unique_count]
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value > 0);
  return candidates[0] ?? 0;
};

const applyProfileIntelligence = (rows) => rows.map((row) => {
  const dictionaryRole = String(row.dictionary_role || "").toLowerCase();
  const normalizedRole = dictionaryRole === "period" ? "Period" : ROLES.includes(row.role) ? row.role : "Feature";
  const constant = distinctCountFor(row) === 1;
  if (constant) return { ...row, role: "Ignore", profile_recommendation: "Constant column: one observed non-missing level; role changed to Ignore." };
  return { ...row, role: normalizedRole };
});

const scrollWorkflow = (element, deltaY) => {
  let parent = element.parentElement;
  while (parent) {
    const canScroll = parent.scrollHeight > parent.clientHeight;
    if (canScroll && getComputedStyle(parent).overflowY !== "visible") {
      parent.scrollBy({ top: deltaY, behavior: "auto" });
      return;
    }
    parent = parent.parentElement;
  }
};

const VariableInventory = forwardRef(function VariableInventory({ item, readOnly = false, mapping = [], onSaved, deferSave = false, onRowsLoaded }, ref) {
  const [tables, setTables] = useState([]);
  const [table, setTable] = useState("");
  const [rows, setRows] = useState([]);
  const [original, setOriginal] = useState([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [specialValueDrafts, setSpecialValueDrafts] = useState({});
  const inventoryScrollRef = useRef(null);
  const specialInputRefs = useRef(new Map());

  useEffect(() => {
    if (!item?.item_id) return;
    getItemTablesV2(item.item_id).then((next) => { setTables(next); setTable(next[0]?.table_name || ""); });
  }, [item?.item_id]);

  useEffect(() => {
    if (!item?.item_id || !table) return;
    getInventoryV2(item.item_id, { table }).then((next) => {
      const intelligent = applyProfileIntelligence(next).map((row) => (
        specialValueIssue(row) ? { ...row, missing_codes_confirmed: false } : row
      ));
      // Keep the profiling-intelligence result as the reset baseline. User
      // edits are layered on top of this state and should be the only changes
      // discarded by Reset.
      setRows(intelligent); setOriginal(intelligent); onRowsLoaded?.(intelligent);
      setSpecialValueDrafts({});
    });
  }, [item?.item_id, table, onRowsLoaded]);

  const update = (idx, key, value) => {
    setStatus("");
    setRows((current) => {
      const next = current.map((row, rowIndex) => rowIndex === idx ? { ...row, [key]: value } : row);
      onRowsLoaded?.(next);
      return next;
    });
  };

  const updateSpecialCodes = (idx, values) => {
    setStatus("");
    setRows((current) => {
      const next = current.map((row, rowIndex) => rowIndex === idx ? {
        ...row, missing_value_codes_json: values, missing_codes_confirmed: false,
      } : row);
      onRowsLoaded?.(next);
      return next;
    });
  };

  const editSpecialCodes = (idx, key, value) => {
    setSpecialValueDrafts((current) => ({ ...current, [key]: value }));
    updateSpecialCodes(idx, parseSpecialValueInput(value));
  };

  const finishSpecialCodeEdit = (key) => {
    setSpecialValueDrafts((current) => {
      if (!(key in current)) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
    // The next render uses the normalized comma-separated representation.
  };

  const issues = useMemo(() => specialValueIssues(rows), [rows]);
  const focusIssue = (target = issues[0]) => {
    if (!target) return;
    setQuery("");
    setTimeout(() => {
      const input = specialInputRefs.current.get(`${target.row.table_name}.${target.row.column_name}`);
      input?.scrollIntoView({ behavior: "smooth", block: "center" });
      input?.focus();
    }, 0);
  };

  const save = async () => {
    if (!item?.item_id || !table) return rows;
    if (issues.length) {
      setStatus(`Correct ${issues.length} special-value ${issues.length === 1 ? "issue" : "issues"} before saving.`);
      focusIssue();
      return null;
    }
    setSaving(true); setStatus("");
    try {
      const saved = await putInventoryV2(item.item_id, rows, { table });
      setRows(saved); setOriginal(saved); setStatus("Definitions saved"); onRowsLoaded?.(saved); onSaved?.();
      return saved;
    } catch (error) {
      setStatus(typeof error.detail === "string" ? error.detail : error.message);
      throw error;
    } finally { setSaving(false); }
  };

  useImperativeHandle(ref, () => ({ save, hasRows: () => rows.length > 0, validationIssues: () => issues }));

  const filtered = useMemo(() => rows.map((row, index) => ({ row, index })).filter(({ row }) => {
    const text = `${row.column_name} ${row.classification} ${row.role} ${row.description || ""}`.toLowerCase();
    return text.includes(query.trim().toLowerCase());
  }), [query, rows]);
  const dirty = JSON.stringify(rows) !== JSON.stringify(original);

  return <section className="rounded-lg border border-slate-200 bg-white p-5">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-700">Canonical dictionary</p><h2 className="mt-1 text-xl font-semibold text-slate-950">Normalized column definitions</h2><p className="mt-1 text-sm text-slate-500">Review metadata before promoting the snapshot to Test Lab.</p></div>
      <div className="flex flex-wrap gap-2">
        <label className="flex h-10 items-center gap-2 rounded-md border border-slate-200 bg-white px-3"><Search className="h-4 w-4 text-slate-500" /><input className="w-52 text-sm outline-none" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search columns" /></label>
        <Button type="button" variant="outline" disabled={!dirty || saving} onClick={() => { setRows(original); setSpecialValueDrafts({}); onRowsLoaded?.(original); }}><RotateCcw className="h-4 w-4" />Reset</Button>
        {!readOnly && !deferSave && <Button type="button" disabled={!dirty || saving} onClick={save}><Save className="h-4 w-4" />{saving ? "Saving…" : "Apply metadata"}</Button>}
        {item?.kind === "database" && <select aria-label="Table" className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm" value={table} onChange={(event) => setTable(event.target.value)}>{tables.map((entry) => <option key={entry.table_name}>{entry.table_name}</option>)}</select>}
      </div>
    </div>
    <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><strong>{dirty ? "Metadata edits awaiting confirmation" : "Auto-detected metadata ready for confirmation"}</strong><span className="ml-2">{deferSave ? "The final Save and Proceed action will persist these definitions." : "Apply edits before continuing."}</span></div>
    {!!issues.length && <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800" role="alert"><span><strong>{issues.length} blocking special-value {issues.length === 1 ? "issue" : "issues"} in this step.</strong> Correct the highlighted {issues.length === 1 ? "column" : "columns"} before confirming or proceeding.</span><Button type="button" size="sm" variant="outline" onClick={() => focusIssue()}>Review first issue</Button></div>}
    <div
      ref={inventoryScrollRef}
      onWheel={(event) => {
        const element = inventoryScrollRef.current;
        if (!element || !event.deltaY) return;
        const atTop = element.scrollTop <= 0;
        const atBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 1;
        if ((event.deltaY < 0 && atTop) || (event.deltaY > 0 && atBottom)) {
          event.preventDefault();
          scrollWorkflow(element, event.deltaY);
        }
      }}
      className="mt-4 max-h-[32rem] overflow-auto overscroll-y-auto rounded-md border border-slate-200"
    >
      <table className="w-full min-w-[1180px] text-sm">
        <thead className="sticky top-0 z-10 bg-slate-100 text-left text-xs uppercase text-slate-500"><tr><th className="w-36 max-w-36 px-3 py-3">Column</th><th className="px-3 py-3">Format</th><th className="px-3 py-3">Detected format</th><th className="px-3 py-3">Role</th><th className="px-3 py-3">Valid values</th><th className="px-3 py-3">Special / missing</th><th className="px-3 py-3">Source</th><th className="px-3 py-3">Description</th></tr></thead>
        <tbody>{filtered.map(({ row, index }) => {
          const suggestion = mapping.find((entry) => entry.source_column === row.column_name && entry.tier === "fuzzy" && entry.status === "confirm_suggestion");
          const codes = specialValuesFor(row);
          const codeIssue = specialValueIssue(row);
          const codeInputId = `special-values-${index}`;
          const codeKey = `${row.table_name}.${row.column_name}`;
          const observedSpecialCounts = row.profile_json?.proposed_special_value_counts || row.profile_json?.special_value_counts || {};
          const observedSpecialRows = row.profile_json?.proposed_special_value_row_count ?? row.profile_json?.special_value_row_count;
          return <tr key={`${row.table_name}.${row.column_name}`} className={`border-t align-top ${codeIssue ? "border-red-200 bg-red-50/30" : "border-slate-100"}`}>
            <td className="w-36 max-w-36 whitespace-normal break-words px-3 py-3 font-semibold text-slate-900 [overflow-wrap:anywhere]">{row.column_name}{suggestion && <button type="button" className="mt-2 block max-w-full whitespace-normal break-words text-left text-[11px] font-normal text-dq-purple [overflow-wrap:anywhere]" onClick={() => update(index, "mapping_confirmed", true)}><Sparkles className="mr-1 inline h-3 w-3" />Accept link to {suggestion.canonical_field}</button>}</td>
            <td className="px-3 py-3">{readOnly ? FORMAT_LABEL[formatFor(row)] : <select aria-label={`Format for ${row.column_name}`} className="h-10 rounded-md border border-slate-200 bg-white px-3" value={formatFor(row)} onChange={(event) => update(index, "classification", event.target.value)}>{FORMATS.map((format) => <option key={format} value={format}>{FORMAT_LABEL[format]}</option>)}</select>}</td>
            <td className="px-3 py-3 text-slate-600">{FORMAT_LABEL[formatFor(row, true)]}</td>
            <td className="px-3 py-3">{readOnly ? row.role : <select aria-label={`Role for ${row.column_name}`} className="h-10 rounded-md border border-slate-200 bg-white px-3" value={row.role || "Feature"} onChange={(event) => update(index, "role", event.target.value)}>{ROLES.map((role) => <option key={role}>{role}</option>)}</select>}</td>
            <td className="max-w-56 px-3 py-3 text-slate-600">{validValuesFor(row)}</td>
            <td className="min-w-64 px-3 py-3">{readOnly ? codes.join(", ") || "—" : <><input id={codeInputId} ref={(node) => { if (node) specialInputRefs.current.set(codeKey, node); else specialInputRefs.current.delete(codeKey); }} aria-label={`Special or missing values for ${row.column_name}`} aria-invalid={Boolean(codeIssue)} aria-describedby={codeIssue ? `${codeInputId}-error` : undefined} className={`h-10 w-full rounded-md border px-3 outline-none ${codeIssue ? "border-red-500 bg-red-50 focus:ring-2 focus:ring-red-200" : "border-slate-200 focus:border-teal-500"}`} value={specialValueDrafts[codeKey] ?? codes.join(", ")} placeholder={specialValuePlaceholder(row)} onChange={(event) => editSpecialCodes(index, codeKey, event.target.value)} onBlur={() => finishSpecialCodeEdit(codeKey)} />{codeIssue && <div id={`${codeInputId}-error`} className="mt-2 rounded-md border border-red-200 bg-red-50 px-2 py-2 text-xs text-red-700"><p>{codeIssue.message}</p>{!!codeIssue.suggestedValues.length && <button type="button" className="mt-1 font-semibold underline" onClick={() => { setSpecialValueDrafts((current) => ({ ...current, [codeKey]: codeIssue.suggestedValues.join(", ") })); updateSpecialCodes(index, codeIssue.suggestedValues); }}>Use suggested value: {codeIssue.suggestedValues.join(", ")}</button>}</div>}{!!codes.length && <><div className="mt-1 text-[11px] text-slate-500">Observed rows: {observedSpecialRows ?? "apply to calculate"}{Object.keys(observedSpecialCounts).length ? ` · ${Object.entries(observedSpecialCounts).map(([code, count]) => `${code}: ${count}`).join(", ")}` : ""}</div><label className={`mt-1 flex gap-1 text-[11px] ${codeIssue ? "text-slate-400" : "text-slate-500"}`}><input type="checkbox" disabled={Boolean(codeIssue)} checked={Boolean(row.missing_codes_confirmed) && !codeIssue} onChange={(event) => update(index, "missing_codes_confirmed", event.target.checked)} />Confirmed missing codes</label></>}</>}</td>
            <td className="px-3 py-3 text-slate-600">{row.dictionary_role ? "dictionary" : row.provisional ? "inferred" : "profile"}</td>
            <td className="max-w-sm px-3 py-3 text-slate-600"><span>{row.description || "—"}</span>{row.business_context && <small className="mt-1 block text-slate-400">{row.business_context}</small>}{row.profile_recommendation && <small className="mt-1 block font-medium text-amber-700">{row.profile_recommendation}</small>}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    <div className="mt-3 flex items-center justify-between text-xs text-slate-500"><span>Showing {filtered.length} of {rows.length} definitions{dirty ? " · unsaved edits" : ""}</span><span className={status.includes("saved") ? "text-emerald-700" : "text-red-600"}>{status}</span></div>
  </section>;
});

export default VariableInventory;
