import { useState } from "react";
import { Info, Lock, Play, ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

// testlab-redesign-0.4.0.md §3 Step 2 — the scope gate: the SINGLE pre-run
// decision surface (design principle #2/#3). What the user approves is the
// manifest, never code. Every edit here writes ONE decision record
// (CFR-12) server-side; Run freezes it. Once the run leaves `draft` the
// manifest is immutable provenance — this view goes read-only.

function Section({ title, children }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-800">{title}</h3>
      {children}
    </section>
  );
}

function SourceTag({ source }) {
  if (!source) return null;
  const tuned = /user-set|tuned/i.test(source);
  return (
    <span className={`ml-2 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${tuned ? "bg-dq-purple/10 text-dq-purple" : "bg-slate-100 text-slate-500"}`}>
      {source}
    </span>
  );
}

export default function CrossFieldScopeGate({
  runId, onRunStarted, run, manifest, decisions, error, busy, patch, runNow,
}) {
  const [excludingTable, setExcludingTable] = useState(null); // table_name awaiting a reason
  const [excludeReason, setExcludeReason] = useState("");
  const [roleEdit, setRoleEdit] = useState({});

  if (error && (!manifest || !run)) return <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>;
  if (!manifest || !run) return <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">Loading scope gate…</div>;

  const frozen = run.status !== "draft";

  const columnsByTable = Object.fromEntries((manifest.scope?.tables || []).map((t) => [t.table_name, t.columns || []]));
  const allColumns = (manifest.scope?.tables || []).flatMap((t) => (t.columns || []).map((c) => `${t.table_name}.${c}`));

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <h2 className="font-semibold text-slate-950">{manifest.diagnostic?.name} · scope gate</h2>
          <p className="text-xs text-slate-500">
            {manifest.item_name} · run {manifest.run_id} · use case {manifest.use_case?.value}
            <SourceTag source={manifest.use_case?.source} />
          </p>
        </div>
        {frozen ? (
          <Badge variant="secondary" className="gap-1"><Lock className="h-3 w-3" /> {run.status}</Badge>
        ) : (
          <Button onClick={runNow} disabled={busy}>
            <Play className="h-4 w-4" /> Run diagnostic
          </Button>
        )}
      </div>

      {frozen && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
          <span className="flex items-center gap-2"><Info className="h-3.5 w-3.5" /> This manifest is frozen — it is the immutable record of what ran. Edits are disabled.</span>
          <Button size="sm" variant="outline" onClick={() => onRunStarted(runId)}>
            {run.status === "done" ? "View run console" : "Watch run console"}
          </Button>
        </div>
      )}
      {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      <Section title={`Rules in scope (${manifest.rules_summary.total})`}>
        <div className="mb-3 flex flex-wrap gap-2 text-xs text-slate-600">
          {Object.entries(manifest.rules_summary.by_severity).map(([k, v]) => <Badge key={k} variant="outline">{k}: {v}</Badge>)}
          {Object.entries(manifest.rules_summary.by_type).map(([k, v]) => <Badge key={k} variant="secondary">{k}: {v}</Badge>)}
        </div>
        <p className="text-xs text-slate-500">
          KB source: {(manifest.kb.documents || []).map((d) => `${d.title} (v${d.version_seq})`).join(", ") || "published knowledge base"}
          {" — "}{manifest.kb.counts.published_bound} published+bound, {manifest.kb.counts.in_scope} in scope for {manifest.use_case?.value}
          {manifest.kb.counts.filtered_out_by_framework ? `, ${manifest.kb.counts.filtered_out_by_framework} filtered out by framework` : ""}.
        </p>
      </Section>

      <Section title={`Roles resolved: ${manifest.roles_summary.resolved}/${manifest.roles_summary.total} (${manifest.roles_summary.override} override, ${manifest.roles_summary.auto} auto)`}>
        {manifest.roles_summary.unresolved.length > 0 && (
          <p className="mb-2 text-xs font-medium text-amber-700">
            Unresolved: {manifest.roles_summary.unresolved.join(", ")}
          </p>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left uppercase text-slate-400">
              <tr>
                <th className="py-1 pr-3">Role</th>
                <th className="py-1 pr-3">Column</th>
                <th className="py-1 pr-3">Score</th>
                <th className="py-1 pr-3">Provenance</th>
                {!frozen && <th className="py-1">Override</th>}
              </tr>
            </thead>
            <tbody>
              {Object.entries(manifest.roles).map(([role, spec]) => (
                <tr key={role} className="border-t border-slate-100 align-top">
                  <td className="py-1.5 pr-3 font-medium text-slate-800">{role}</td>
                  <td className="py-1.5 pr-3 text-slate-700">{spec.table ? `${spec.table}.${spec.column}` : <span className="text-amber-700">unresolved</span>}</td>
                  <td className="py-1.5 pr-3 text-slate-600">{spec.score?.toFixed?.(2) ?? spec.score}</td>
                  <td className="py-1.5 pr-3 text-slate-500">{spec.via} — {spec.reason}</td>
                  {!frozen && (
                    <td className="py-1.5">
                      <div className="flex items-center gap-1">
                        <select className="h-7 rounded-md border border-slate-200 bg-white px-1 text-xs"
                          value={roleEdit[role] ?? ""}
                          onChange={(e) => setRoleEdit((prev) => ({ ...prev, [role]: e.target.value }))}>
                          <option value="">— choose column —</option>
                          {allColumns.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                        <Button size="sm" variant="outline" disabled={busy || !roleEdit[role]}
                          onClick={() => {
                            const [table, ...rest] = (roleEdit[role] || "").split(".");
                            patch({ kind: "role_override", role, column: rest.join("."), table });
                          }}>
                          Set
                        </Button>
                        {spec.via === "override" && (
                          <Button size="sm" variant="ghost" disabled={busy} onClick={() => patch({ kind: "role_override", role, column: null })}>
                            Clear
                          </Button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Thresholds / parameters">
        <div className="grid gap-2 sm:grid-cols-2">
          {Object.entries(manifest.thresholds).map(([key, spec]) => (
            <div key={key} className="flex items-center justify-between gap-2 rounded-md border border-slate-100 bg-slate-50/60 px-2 py-1.5 text-xs">
              <span className="font-medium text-slate-700">{key}<SourceTag source={spec.source} /></span>
              {frozen ? (
                <span className="font-mono text-slate-800">{String(spec.value)}</span>
              ) : (
                <input className="h-7 w-28 rounded-md border border-slate-200 bg-white px-2 font-mono text-xs" defaultValue={String(spec.value)}
                  onBlur={(e) => {
                    if (e.target.value === String(spec.value)) return;
                    let value = e.target.value;
                    const num = Number(value);
                    if (value !== "" && !Number.isNaN(num)) value = num;
                    patch({ kind: "threshold_tune", key, value });
                  }} />
              )}
            </div>
          ))}
        </div>
      </Section>

      <Section title="Scope preview">
        <ul className="grid gap-1.5 text-xs text-slate-600">
          {(manifest.scope.tables || []).map((t) => (
            <li key={t.table_name} className="rounded-md border border-slate-100 px-2 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <span><span className="font-medium text-slate-800">{t.table_name}</span> — {t.row_count ?? "?"} rows, {(columnsByTable[t.table_name] || []).length} columns</span>
                {!frozen && excludingTable !== t.table_name && (
                  <Button size="sm" variant="outline" onClick={() => { setExcludingTable(t.table_name); setExcludeReason(""); }}>
                    Exclude
                  </Button>
                )}
              </div>
              {excludingTable === t.table_name && (
                <div className="mt-2 flex items-center gap-2">
                  <input autoFocus placeholder="Reason for excluding this table (required)"
                    className="h-7 flex-1 rounded-md border border-slate-200 px-2 text-xs"
                    value={excludeReason} onChange={(e) => setExcludeReason(e.target.value)} />
                  <Button size="sm" disabled={busy || !excludeReason.trim()}
                    onClick={() => { patch({ kind: "scope_exclusion", table: t.table_name, reason: excludeReason.trim() }); setExcludingTable(null); }}>
                    Confirm
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setExcludingTable(null)}>Cancel</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
        {(manifest.scope.excluded_tables || []).length > 0 && (
          <div className="mt-2 text-xs text-slate-500">
            Excluded: {manifest.scope.excluded_tables.map((t) => `${t.table_name} (${t.reason})`).join("; ")}
          </div>
        )}
      </Section>

      <Section title="Role mapping verification (optional, off by default — D-08)">
        <div className="flex items-center gap-2 text-xs text-slate-600">
          <ShieldAlert className="h-3.5 w-3.5 text-slate-400" />
          <span>{manifest.role_verification.enabled ? "Enabled" : "Off"} — {manifest.role_verification.reason}</span>
          {!frozen && (
            <Button size="sm" variant="outline" disabled={busy}
              onClick={() => patch({ kind: "role_verification_change", enabled: true })}>
              Request verification
            </Button>
          )}
        </div>
      </Section>

      {decisions.length > 0 && (
        <Section title={`Decision record (${decisions.length})`}>
          <ul className="grid gap-1 text-xs text-slate-500">
            {decisions.map((d) => (
              <li key={d.id}>
                <span className="font-medium text-slate-700">{d.kind}</span> — {JSON.stringify(d.payload)} · {d.actor}, {d.ts}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
