import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, FileSpreadsheet, Loader2, UploadCloud } from "lucide-react";

import { Label } from "@/components/ui/label";
import { SOURCING_STAGES } from "./constants";

const WARNING_LABEL = {
  column_all_null: "Fully null", column_mixed_type: "Mixed type",
  column_parse_failure_rate: "Parse failures", dict_var_not_in_dataset: "Declared, not in dataset",
  dict_field_unused: "Preserved, not used", type_conflict: "Type conflict", unsupported_value: "Unsupported declared type",
};

export function FilePicker({
  label = "Input dataset", hint = "Excel, CSV, TSV, pipe or text",
  accept = ".csv,.tsv,.txt,.xlsx,.xls", file, onChange, onRemove, disabled, required = false,
}) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const acceptFile = (files) => { if (files?.[0]) onChange(files[0]); };
  return (
    <div>
      <div className="flex items-center justify-between"><Label>{label}</Label><span className={`text-[10px] font-semibold uppercase tracking-wide ${required ? "text-dq-purple" : "text-slate-400"}`}>{required ? "Required" : "Optional"}</span></div>
      <p className="text-xs text-slate-400">{hint}</p>
      <input ref={inputRef} type="file" accept={accept} className="hidden"
        onChange={(event) => { acceptFile(event.target.files); event.target.value = ""; }} />
      <button type="button" disabled={disabled} onClick={() => inputRef.current?.click()}
        onDragOver={(event) => { event.preventDefault(); if (!disabled) setDragging(true); }} onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); if (!disabled) acceptFile(event.dataTransfer.files); }}
        className={`mt-2 flex min-h-28 w-full items-center gap-4 rounded-lg border border-dashed px-5 text-left transition ${dragging ? "border-dq-purple bg-dq-purple/5" : "border-slate-300 bg-slate-50/50"} disabled:opacity-60`}>
        <span className="rounded-lg bg-emerald-50 p-3 text-teal-700">{file ? <FileSpreadsheet className="h-5 w-5" /> : <UploadCloud className="h-5 w-5" />}</span>
        <span className="min-w-0 flex-1"><strong className="block truncate text-sm text-slate-900">{file?.name || "Drop a file here"}</strong><small className="text-slate-500">{file ? (file.retained ? "Retained source · no re-upload required" : `${(file.size / 1024 / 1024).toFixed(2)} MB`) : hint}</small></span>
        {!file && <span className="rounded-md border border-teal-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-teal-800">Browse files</span>}
        {file && !disabled && onRemove && <span role="button" tabIndex={0} className="text-xs font-medium text-slate-500 hover:underline" onClick={(event) => { event.stopPropagation(); onRemove(); }}>Remove</span>}
      </button>
    </div>
  );
}

export function WarningsPanel({ warnings }) {
  const [open, setOpen] = useState(false);
  if (!warnings?.length) return null;
  return (
    <div className="mt-4 rounded-md border border-amber-200 bg-amber-50">
      <button type="button" onClick={() => setOpen((current) => !current)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-amber-800">
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}<AlertTriangle className="h-4 w-4" />
        {warnings.length} review {warnings.length === 1 ? "warning" : "warnings"} — none block Ready
      </button>
      {open && <ul className="space-y-1 border-t border-amber-200 px-3 py-2 text-xs text-amber-900">
        {warnings.map((warning, index) => <li key={`${warning.column}-${warning.code}-${index}`}><span className="mr-2 rounded bg-amber-200/60 px-1.5 py-0.5 font-semibold uppercase tracking-wide">{WARNING_LABEL[warning.code] || warning.code}</span><strong>{warning.column}</strong>: {warning.message}</li>)}
      </ul>}
    </div>
  );
}

export function Summary({ kind, summaries }) {
  if (!summaries?.length) return null;
  const database = kind === "database";
  const rows = summaries.reduce((total, summary) => total + (summary.rows || 0), 0);
  const columns = summaries.reduce((total, summary) => total + (summary.columns || 0), 0);
  if (!database && summaries.length === 1) return <section className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-white px-4 py-3" aria-label="Dataset Summary"><div><h3 className="font-semibold text-slate-950">Dataset Summary</h3><p className="text-xs text-slate-500">{summaries[0].tab}</p></div><div className="flex gap-2 text-xs"><span className="rounded-full bg-slate-100 px-3 py-1.5"><b>{Number(summaries[0].rows || 0).toLocaleString()}</b> rows</span><span className="rounded-full bg-slate-100 px-3 py-1.5"><b>{summaries[0].columns ?? 0}</b> columns</span></div></section>;
  return (
    <section className="mt-4 rounded-md border border-slate-200 bg-white px-4 py-3" aria-label={`${database ? "Database" : "Dataset"} Summary`}>
      <h3 className="font-semibold text-slate-950">{database ? "Database Summary" : "Dataset Summary"}</h3>
      <table className="mt-3 w-full text-sm"><thead className="text-left text-xs uppercase text-slate-400"><tr><th className="py-1 pr-3">{database ? "Table" : "File"}</th><th className="py-1 pr-3">Rows</th><th className="py-1">Columns</th></tr></thead>
        <tbody>{summaries.map((summary) => <tr key={summary.tab} className="border-t border-slate-100 text-slate-600"><td className="py-2 pr-3">{summary.tab}</td><td className="py-2 pr-3">{summary.rows?.toLocaleString() || "—"}</td><td className="py-2">{summary.columns ?? "—"}</td></tr>)}
          {database && <tr className="border-t-2 border-slate-200 font-semibold text-slate-800"><td className="py-2 pr-3">Total</td><td className="py-2 pr-3">{rows.toLocaleString()}</td><td className="py-2">{columns}</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

export function StepCard({ step, title, subtitle, children, testId, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  return <details data-testid={testId} open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="mt-5 overflow-hidden rounded-lg border border-slate-200 bg-white">
    <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-5 hover:bg-slate-50"><span><strong className="block text-slate-950">STEP {step} — {title}</strong>{subtitle && <small className="mt-1 block text-slate-500">{subtitle}</small>}</span><ChevronDown className={`h-5 w-5 text-slate-500 transition-transform ${open ? "rotate-180" : ""}`} /></summary>
    <div className="border-t border-slate-200 p-5">{children}</div>
  </details>;
}

export function SourcingProgress({ progress, fileName }) {
  const [elapsed, setElapsed] = useState(0);
  const running = progress && !["done", "error"].includes(progress.state);
  useEffect(() => {
    if (!progress?.startedAt) return undefined;
    const update = () => setElapsed(Math.max(0, Math.floor((Date.now() - progress.startedAt) / 1000)));
    update();
    if (!running) return undefined;
    const timer = setInterval(update, 500);
    return () => clearInterval(timer);
  }, [progress?.startedAt, running]);
  if (!progress) return null;
  const percent = Math.max(0, Math.min(100, Math.round(progress.percent || 0)));
  const completed = Math.max(0, Math.min(SOURCING_STAGES.length, progress.completed || 0));
  const currentIndex = SOURCING_STAGES.findIndex(([key]) => key === progress.stage);
  const elapsedLabel = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  return <section className={`mt-5 rounded-xl border p-5 ${progress.state === "error" ? "border-red-200 bg-red-50" : "border-teal-100 bg-emerald-50/40"}`} aria-live="polite">
    <div className="flex items-center justify-between gap-4"><span className="flex items-center gap-2"><Loader2 className={`h-5 w-5 text-teal-700 ${running ? "animate-spin" : ""}`} /><strong className="text-slate-900">Preparing the data foundation</strong><span className="text-sm text-slate-500">{fileName}</span></span><strong className="text-2xl text-teal-800">{percent}%</strong></div>
    <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-teal-100"><div className={`h-full rounded-full transition-all duration-300 ${progress.state === "error" ? "bg-red-500" : "bg-teal-600"}`} style={{ width: `${percent}%` }} /></div>
    <div className="mt-3 flex flex-wrap items-start justify-between gap-2 text-sm"><p className="text-slate-600">{progress.message}</p><p className="font-medium text-slate-600">{completed} stages completed · {SOURCING_STAGES.length - completed} remaining · elapsed {elapsedLabel}</p></div>
    <div className="mt-4 flex flex-wrap gap-2">{SOURCING_STAGES.map(([key, label], index) => {
      const done = index < completed || progress.stage === "complete";
      const current = index === currentIndex && !done;
      return <span key={key} className={`rounded-full border px-3 py-1 text-xs ${done ? "border-teal-200 bg-white text-teal-700" : current ? "border-dq-purple bg-white font-semibold text-dq-purple" : "border-slate-200 bg-white/60 text-slate-500"}`}>{label}</span>;
    })}</div>
  </section>;
}
