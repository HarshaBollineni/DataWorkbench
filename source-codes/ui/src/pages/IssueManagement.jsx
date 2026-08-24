import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, Download, Eye, EyeOff, RefreshCw, Ticket } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  downloadReportV2, getIssueRegisterV2, getItemIssuesV2, getItemsV2, getResultsV2,
} from "@/api/client";

// Issue Management (spec 9): failed Test Lab output as trackable rows, one per
// failed test per table. Tracking only — nothing here re-runs a test.

const STATUSES = ["Open", "In RCA", "Closed", "Escalated"];
const CRITICALITIES = ["Critical", "High", "Medium"];

export function StatusChip({ status }) {
  const variant =
    status === "Closed" ? "success" :
    status === "Escalated" ? "destructive" :
    status === "In RCA" ? "warning" : "secondary";
  return <Badge variant={variant}>{status === "In RCA" ? "RCA in progress" : status}</Badge>;
}

export function CritBadge({ criticality }) {
  if (!criticality) return <span className="text-xs text-slate-400">—</span>;
  const variant =
    criticality === "Critical" ? "destructive" :
    criticality === "High" ? "warning" : "secondary";
  return <Badge variant={variant}>{criticality}</Badge>;
}

function thresholdSummary(row) {
  const observed = row.metric == null ? "Not available" : Number(row.metric).toFixed(4);
  const threshold = row.thresholds;
  if (threshold == null) return <span><strong>{observed}</strong><small className="block text-slate-400">No applicable boundary retained</small></span>;
  if (typeof threshold !== "object") return <span><strong>{observed}</strong><small className="block text-slate-500">Boundary {String(threshold)}</small></span>;
  const bands = Object.entries(threshold).filter(([, value]) => value != null && typeof value !== "object");
  return <span><strong>{observed}</strong><small className="block max-w-64 text-slate-500">{bands.map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`).join(" · ") || "Configured bands retained"}</small></span>;
}

export default function IssueManagement() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [items, setItems] = useState([]);
  const [itemId, setItemId] = useState(() => searchParams.get("item") || "");
  const [statusFilter, setStatusFilter] = useState("");
  const [critFilter, setCritFilter] = useState("");
  const [tableFilter, setTableFilter] = useState("");
  const [payload, setPayload] = useState(null); // per-item {issues, could_not_assess, all_passed}
  const [register, setRegister] = useState([]);
  const [passed, setPassed] = useState([]);
  const [showPassed, setShowPassed] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => { getItemsV2().then(setItems); }, []);

  const load = () => {
    if (itemId) {
      getItemIssuesV2(itemId)
        .then((data) => { setPayload(data); setMessage(""); })
        .catch((e) => setMessage(e.message));
      getResultsV2(itemId, "all")
        .then((rows) => setPassed(rows.filter((r) => r.status === "pass")))
        .catch(() => setPassed([]));
    } else {
      // Direct navigation must never dead-end on an API error (feedback 5.1):
      // fall back to an empty register with a friendly hint instead.
      getIssueRegisterV2()
        .then((rows) => { setRegister(rows); setPayload(null); setPassed([]); setMessage(""); })
        .catch(() => {
          setRegister([]);
          setPayload(null);
          setPassed([]);
          setMessage("No issue register available yet — run tests in the Test Lab first.");
        });
    }
  };
  useEffect(load, [itemId]);

  const item = items.find((r) => r.item_id === itemId);
  const reportReady = item && ["testlab_step4", "complete"].includes(item.status);

  const rows = useMemo(() => {
    const source = itemId ? payload?.issues || [] : register;
    return source.filter((r) =>
      (!statusFilter || r.status === statusFilter) &&
      (!critFilter || r.criticality === critFilter) &&
      (!tableFilter || r.table_name === tableFilter));
  }, [itemId, payload, register, statusFilter, critFilter, tableFilter]);

  const tables = useMemo(() => {
    const source = itemId ? payload?.issues || [] : register;
    return [...new Set(source.map((r) => r.table_name))];
  }, [itemId, payload, register]);

  const report = async () => {
    try { await downloadReportV2(itemId); } catch (e) { setMessage(e.message); }
  };

  return (
    <main className="min-h-screen bg-slate-50 p-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-950">Issue Management</h1>
          <p className="mt-1 text-sm text-slate-500">
            Failed tests from the Test Lab, tracked one row per failed test per table. Closing or
            raising an issue records a decision — it never changes the data or re-runs a test.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm" value={itemId} onChange={(e) => setItemId(e.target.value)}>
            <option value="">All items (register)</option>
            {items.map((row) => <option key={row.item_id} value={row.item_id}>{row.name}</option>)}
          </select>
          {itemId && (
            <Button variant="outline" onClick={report} disabled={!reportReady}
              title={reportReady ? "Download the DQ report (PDF)" : "Available once Test Lab is complete"}>
              <Download className="h-4 w-4" /> Report
            </Button>
          )}
          <Button variant="outline" onClick={load}><RefreshCw className="h-4 w-4" /> Refresh</Button>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">Any status</option>
          {STATUSES.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm" value={critFilter} onChange={(e) => setCritFilter(e.target.value)}>
          <option value="">Any criticality</option>
          {CRITICALITIES.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm" value={tableFilter} onChange={(e) => setTableFilter(e.target.value)}>
          <option value="">Any table</option>
          {tables.map((t) => <option key={t}>{t}</option>)}
        </select>
        {message && <span className="text-sm text-red-600">{message}</span>}
      </div>

      {itemId && payload?.all_passed ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-8 text-center">
          <CheckCircle2 className="mx-auto h-8 w-8 text-emerald-600" />
          <div className="mt-2 font-semibold text-emerald-900">No issues — all tests passed.</div>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2">Test</th>
                {!itemId && <th className="px-3 py-2">Item</th>}
                <th className="px-3 py-2">Table / dataset</th>
                <th className="px-3 py-2">Affected columns</th>
                <th className="px-3 py-2">Criticality</th>
                <th className="px-3 py-2">Observed vs threshold</th>
                <th className="px-3 py-2">Affected rows</th>
                <th className="px-3 py-2">Managed issue</th>
                <th className="px-3 py-2">RCA stage</th>
                <th className="px-3 py-2">Remediation</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.issue_row_id}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                  onClick={() => navigate(`/issues/${r.issue_row_id}`)}>
                  <td className="px-3 py-2 font-medium text-slate-900">{r.test_name}</td>
                  {!itemId && <td className="px-3 py-2 text-slate-600">{r.item_name}</td>}
                  <td className="px-3 py-2 text-slate-600">{r.table_name}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">{(r.columns || []).join(", ") || "—"}</td>
                  <td className="px-3 py-2"><CritBadge criticality={r.criticality} /></td>
                  <td className="px-3 py-2 text-xs text-slate-600">{thresholdSummary(r)}</td>
                  <td className="px-3 py-2">{r.violation_applicability === "aggregate_metric" ? <span className="text-xs text-slate-500">N/A — aggregate metric</span> : Number(r.violation_count || 0).toLocaleString()}</td>
                  <td className="px-3 py-2"><StatusChip status={r.status} /></td>
                  <td className="px-3 py-2 text-xs text-slate-600">{r.rca_stage || "Not started"}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">
                    {r.tracked ? (
                      <span className="inline-flex items-center gap-1">
                        <Ticket className="h-3.5 w-3.5" /> {r.tracked.issue_id}
                        {r.tracked.owner ? ` · ${r.tracked.owner}` : ""}
                        {r.tracked.target_date ? ` · due ${r.tracked.target_date}` : ""}
                        {r.tracked.status ? ` · ${r.tracked.status}` : ""}
                      </span>
                    ) : "Not created"}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={itemId ? 9 : 10} className="px-4 py-6 text-center text-sm text-slate-500">
                  No issue rows match the current filters.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {itemId && (payload?.could_not_assess || []).length > 0 && (
        <details className="mt-5 rounded-lg border border-slate-200 bg-white p-4">
          <summary className="cursor-pointer text-sm font-semibold text-slate-700">
            Could not assess ({payload.could_not_assess.length}) — shown for visibility, not counted
            as failures and not part of the score
          </summary>
          <ul className="mt-3 space-y-1 text-sm text-slate-600">
            {payload.could_not_assess.map((r, i) => (
              <li key={i}>
                <span className="font-medium">{r.test_name}</span>
                <span className="text-slate-400"> · {r.table_name} · </span>{r.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      {itemId && passed.length > 0 && (
        <div className="mt-5">
          <Button variant="outline" size="sm" onClick={() => setShowPassed((v) => !v)}>
            {showPassed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            {showPassed ? "Hide" : "Show"} Passed Tests ({passed.length})
          </Button>
          {showPassed && (
            <div className="mt-3 overflow-hidden rounded-lg border border-slate-200 bg-white">
              <table className="w-full text-sm">
                <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500">
                  <tr><th className="px-3 py-2">Test</th><th className="px-3 py-2">Table</th><th className="px-3 py-2">Metric</th></tr>
                </thead>
                <tbody>
                  {passed.map((r) => (
                    <tr key={r.result_id} className="border-t border-slate-100">
                      <td className="px-3 py-2">{r.test_name}</td>
                      <td className="px-3 py-2 text-slate-600">{r.table_name}</td>
                      <td className="px-3 py-2 text-slate-500">{r.metric == null ? "—" : Number(r.metric).toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
