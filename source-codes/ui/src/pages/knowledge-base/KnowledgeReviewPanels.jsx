import { useState } from "react";
import { ClipboardList } from "lucide-react";

import { getKbParseReportV3 } from "@/api/client";
import { Badge } from "@/components/ui/badge";

export function BindingBadge({ status }) {
  const variant = status === "bound" ? "success" : status === "reference-only" ? "warning" : "outline";
  const title = status === "bound"
    ? "Executable — matched a registered primitive."
    : status === "reference-only"
      ? "Parsed and displayable — this rule's phrasing didn't match a registered primitive's signature."
      : "No structured rule type was recovered — not executable.";
  return <Badge variant={variant} title={title}>{status || "unparsed"}</Badge>;
}

export function PlaybackSummary({ playback }) {
  if (!playback) return null;
  const { rules_total, by_framework, by_type, by_severity, roles_referenced, binding, unparsed, hazards } = playback;
  const breakdown = (title, values) => (
    <div>
      <div className="text-xs font-semibold uppercase text-slate-500">{title}</div>
      {Object.keys(values || {}).length === 0
        ? <div className="text-xs text-slate-400">—</div>
        : Object.entries(values).map(([key, value]) => (
          <div key={key} className="text-xs text-slate-700">{key}: <span className="font-medium">{value}</span></div>
        ))}
    </div>
  );
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
        <ClipboardList className="h-4 w-4 text-dq-purple" /> Playback summary
      </div>
      <p className="text-sm text-slate-700">
        <span className="font-semibold">{rules_total}</span> rule{rules_total === 1 ? "" : "s"} found.
      </p>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        {breakdown("By framework", by_framework)}
        {breakdown("By type", by_type)}
        {breakdown("By severity", by_severity)}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold uppercase text-slate-500">Binding</span>
        <Badge variant="success">bound {binding?.bound ?? 0}</Badge>
        <Badge variant="warning">reference-only {binding?.reference_only ?? 0}</Badge>
        <Badge variant="outline">unparsed {binding?.unparsed ?? 0}</Badge>
      </div>
      {roles_referenced?.length > 0 && (
        <div className="mt-3 text-xs text-slate-600">
          <span className="font-semibold uppercase text-slate-500">Roles referenced ({roles_referenced.length}) — </span>
          {roles_referenced.join(", ")}
        </div>
      )}
      {hazards?.length > 0 && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
          <div className="mb-1 font-semibold">Extraction hazards ({hazards.length}) — confirm before use</div>
          <ul className="list-disc space-y-0.5 pl-4">
            {hazards.slice(0, 8).map((hazard, index) => <li key={index}>{hazard.role_text} — {hazard.note}</li>)}
          </ul>
          {hazards.length > 8 && <div className="mt-1 text-amber-700">…and {hazards.length - 8} more.</div>}
        </div>
      )}
      {unparsed?.length > 0 && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600">
          <div className="mb-1 font-semibold">Unparsed ({unparsed.length})</div>
          <ul className="list-disc space-y-0.5 pl-4">
            {unparsed.slice(0, 8).map((entry, index) => <li key={index}>{entry.source_ref} — {entry.reason}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

export function ParseReportPanel({ versionId }) {
  const [report, setReport] = useState(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const load = () => { getKbParseReportV3(versionId).then(setReport).catch((reason) => setError(reason.message)); };

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4">
      <button type="button"
        className="flex w-full items-center justify-between text-sm font-semibold text-slate-900"
        onClick={() => { const next = !open; setOpen(next); if (next && !report) load(); }}>
        <span>Parse report {report ? `(${report.rules_total})` : ""}</span>
        <span className="text-xs font-normal text-slate-400">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <div className="mt-3 max-h-72 overflow-auto">
          {error && <p className="text-xs text-red-600">{error}</p>}
          {report && (
            <table className="w-full text-left text-xs">
              <thead><tr className="text-slate-500"><th className="pb-1 pr-2">Rule</th><th className="pb-1 pr-2">Status</th><th className="pb-1">Reason</th></tr></thead>
              <tbody>
                {report.rules.map((rule) => (
                  <tr key={rule.rule_id} className="border-t border-slate-100 align-top">
                    <td className="py-1 pr-2 font-medium text-slate-800 whitespace-nowrap">{rule.source_ref}</td>
                    <td className="py-1 pr-2"><BindingBadge status={rule.status} /></td>
                    <td className="py-1 text-slate-500">{rule.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
