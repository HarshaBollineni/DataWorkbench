import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, CheckCircle2, Download, Ticket,
} from "lucide-react";

import AgentConsole from "@/components/AgentConsole";
import RcaCase from "@/features/rca/components/RcaCase";
import { TagChips } from "@/components/TagPicker";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  closeIssueV2, downloadReportV2,
  getIssueTagsV3, getIssueV2,
  raiseIssueV2, rcaStreamUrlV2,
} from "@/api/client";
import TrackedEditor from "@/features/rca/components/TrackedIssueEditor";
import { useAgentStream } from "@/pages/testlab/stream";
import { CritBadge, StatusChip } from "@/pages/IssueManagement";

// RCA screen (spec 9.2): AI root-cause analysis per issue row, regenerated
// fresh on every open.
//
// Phase 6 (0.4.0) note: the legacy "additional analyses" panel used to reuse
// the Test Lab snippet-generation pipeline (scope 'rca:{issueRowId}' plan_v2
// rows). That pipeline retired with the wizard (D-16) — plan/finalize/execute/
// snippet all 404 server-side now — so the panel is removed here too rather
// than left calling dead endpoints. It only ever rendered for pre-RCA-Stage-3
// ("legacy-v1") issues; every issue created since carries workflow_version
// "rca" and renders through <RcaCase/> below instead.

export default function IssueRca() {
  const { issueRowId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const returnSearch = new URLSearchParams(location.search).get("return") || "";
  const returnToIssues = `/issues${returnSearch.startsWith("?") ? returnSearch : ""}`;

  const [issue, setIssue] = useState(null);
  const [tags, setTags] = useState([]);
  const [rca, setRca] = useState(null);
  const [rationale, setRationale] = useState("");
  const [raiseOpen, setRaiseOpen] = useState(false);
  const [raiseForm, setRaiseForm] = useState({ title: "", description: "", owner: "", priority: "Medium", target_date: "" });
  const [message, setMessage] = useState("");
  const rcaStream = useAgentStream();

  useEffect(() => {
    getIssueV2(issueRowId).then((row) => {
      setIssue(row);
      getIssueTagsV3(issueRowId).then(setTags).catch(() => setTags([]));
      // RCA Stage 3: cases on the new workflow use the staged RcaCase view instead of
      // the legacy heuristic stream below (second-module guard — same
      // route/screen, not a separate page — docs/rca/00-contracts.md §3).
      if (row.workflow_version === "rca") return;
      // RCA is regenerated fresh each time the row is opened (not persisted).
      rcaStream.run(rcaStreamUrlV2(issueRowId), (event) => {
        if (event.phase === "done") {
          setRca(event);
          setRaiseForm((f) => ({ ...f, title: `${row.test_name} failure in ${row.table_name}`, description: event.likely_cause || "" }));
        }
      });
    }).catch((e) => setMessage(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueRowId]);

  const close = async () => {
    try {
      await closeIssueV2(issueRowId, rationale);
      navigate(returnToIssues);
    } catch (e) { setMessage(e.message); }
  };

  // Feedback 5.3: stay on the workflow after creation — the tracked issue panel
  // appears right here with an editor to update its details.
  const raiseTracked = async () => {
    try {
      const updated = await raiseIssueV2(issueRowId, raiseForm);
      // Merge: the raise response has no item_name/use_case enrichment.
      setIssue((prev) => ({ ...prev, ...updated }));
      setRaiseOpen(false);
      setMessage("");
    } catch (e) { setMessage(e.message); }
  };

  const resolved = issue && ["Closed", "Escalated"].includes(issue.status);

  if (!issue) {
    return <main className="min-h-screen bg-slate-50 p-8 text-sm text-slate-500">{message || "Loading issue…"}</main>;
  }

  return (
    <main className="min-h-screen bg-slate-50 p-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <button type="button" onClick={() => navigate(returnToIssues)}
            className="mb-2 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800">
            <ArrowLeft className="h-4 w-4" /> Back to issues
          </button>
          {issue.workflow_version !== "rca" && <><h1 className="text-2xl font-bold text-slate-950">{issue.test_name}</h1>
          <p className="mt-1 text-sm text-slate-500">
            {issue.item_name} · {issue.table_name} · {(issue.columns || []).join(", ") || "no columns"}
          </p></>}
        </div>
        <div className="flex items-center gap-2">
          {issue.workflow_version !== "rca" && <><CritBadge criticality={issue.criticality} /><StatusChip status={issue.status} /></>}
          <Button variant="outline" size="sm"
            onClick={() => downloadReportV2(issue.item_id).catch((e) => setMessage(e.message))}>
            <Download className="h-4 w-4" /> Report
          </Button>
        </div>
      </div>

      {issue.workflow_version !== "rca" && <><section className="mb-5 grid grid-cols-2 gap-3 rounded-lg border border-slate-200 bg-white p-5 sm:grid-cols-4">
        <Fact label="Metric" value={issue.metric == null ? "—" : Number(issue.metric).toFixed(4)} />
        <Fact label="Threshold" value={issue.thresholds == null ? "—" : typeof issue.thresholds === "object" ? JSON.stringify(issue.thresholds) : String(issue.thresholds)} />
        <Fact label="Violations" value={String(issue.violation_count ?? 0)} />
        <Fact label="Use case" value={issue.use_case || (issue.item_kind === "database" ? "Systemic" : "—")} />
      </section>

      {/* RCA Stage 1 — business tags inherited from the source item, snapshotted
          at issue creation (docs/rca/00-contracts.md §7). Read-only here. */}
      <section className="mb-5 rounded-lg border border-slate-200 bg-white p-4">
        <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Business tags</div>
        <TagChips tags={tags} empty="No business tags inherited from the source item." />
      </section>

      {/* Feedback 10-07 5.1: every failing column with its own metric,
          threshold, violations and example rows — never just the worst. */}
      {(issue.column_details || []).length > 0 && (
        <section className="mb-5 rounded-lg border border-slate-200 bg-white p-5">
          <h2 className="font-semibold text-slate-950">Per-column results</h2>
          <p className="text-sm text-slate-500">
            One independent execution per column — each failing column carries its own values.
          </p>
          <div className="mt-3 overflow-hidden rounded-md border border-slate-200">
            <table className="w-full text-sm">
              <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2">Column(s)</th>
                  <th className="px-3 py-2">Metric</th>
                  <th className="px-3 py-2">Threshold</th>
                  <th className="px-3 py-2">Violations</th>
                  <th className="px-3 py-2">Example rows</th>
                </tr>
              </thead>
              <tbody>
                {issue.column_details.map((d, i) => (
                  <tr key={i} className="border-t border-slate-100 align-top">
                    <td className="px-3 py-2 font-medium text-slate-900">{(d.columns || []).join(", ") || "combined"}</td>
                    <td className="px-3 py-2">{d.metric == null ? "—" : Number(d.metric).toFixed(4)}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      {d.threshold == null ? "—" : typeof d.threshold === "object" ? JSON.stringify(d.threshold) : String(d.threshold)}
                    </td>
                    <td className="px-3 py-2">{d.violation_count ?? 0}</td>
                    <td className="px-3 py-2">
                      {(d.evidence?.sample || []).length ? (
                        <details>
                          <summary className="cursor-pointer text-xs font-medium text-dq-purple">
                            Show ({d.evidence?.sample?.length || 0})
                          </summary>
                          <pre className="mt-2 max-h-48 max-w-xl overflow-auto rounded-md bg-slate-50 p-2 text-xs text-slate-700">
                            {JSON.stringify(d.evidence?.sample?.slice(0, 8), null, 2)}
                          </pre>
                        </details>
                      ) : <span className="text-xs text-slate-400">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {(issue.column_details || []).length === 0 && (issue.evidence?.sample || []).length > 0 && (
        <details className="mb-5 rounded-lg border border-slate-200 bg-white p-4">
          <summary className="cursor-pointer text-sm font-semibold text-slate-700">
            Evidence sample ({issue.evidence?.sample?.length || 0})
          </summary>
          <pre className="mt-3 max-h-56 overflow-auto rounded-md bg-slate-50 p-3 text-xs text-slate-700">
            {JSON.stringify(issue.evidence?.sample?.slice(0, 8), null, 2)}
          </pre>
        </details>
      )}
      </>}

      {issue.workflow_version === "rca" ? (
        <section className="mb-5">
          <RcaCase issueRowId={issueRowId} issue={issue} />
        </section>
      ) : (
      <section className="mb-5 rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="font-semibold text-slate-950">Root-cause analysis</h2>
        <p className="text-sm text-slate-500">RCA Agent — regenerated fresh each time this row is opened.</p>
        <div className="mt-3"><AgentConsole events={rcaStream.events} running={rcaStream.running} /></div>

        {rca && (
          <>
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-4">
              <div className="text-xs font-semibold uppercase text-amber-800">Likely cause</div>
              <p className="mt-1 text-sm text-amber-950">{rca.likely_cause}</p>
            </div>

            <div className="mt-4">
              <h3 className="text-sm font-semibold text-slate-700">Possible solutions</h3>
              <ul className="mt-2 space-y-2">
                {(rca.solutions || []).map((s, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                    <Badge variant={s.source === "framework" ? "secondary" : "warning"}>
                      {s.source === "framework" ? "Framework guidance" : "AI-generated"}
                    </Badge>
                    <span>{s.text}</span>
                  </li>
                ))}
              </ul>
            </div>

            {(rca.suggested_analyses || []).length > 0 && (
              <div className="mt-5">
                <h3 className="text-sm font-semibold text-slate-700">Suggested additional analyses</h3>
                <p className="text-xs text-slate-500">Informational only — the snippet-execution pipeline these used to route to retired with the Test Lab rebuild (D-16).</p>
                <ul className="mt-2 space-y-2">
                  {(rca.suggested_analyses || []).map((a, i) => (
                    <li key={i} className="rounded-md border border-slate-200 p-3 text-sm">
                      <span className="font-medium text-slate-900">{a.title}</span>
                      <span className="block text-xs text-slate-500">{a.rationale}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </section>
      )}

      {issue.workflow_version !== "rca" && <section className="rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="font-semibold text-slate-950">Resolution</h2>
        <p className="text-sm text-slate-500">
          Acts on the whole row. Recording a decision never changes the data or re-runs a test.
        </p>
        {message && <p className="mt-2 text-sm text-red-600">{message}</p>}
        {resolved ? (
          <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
            <StatusChip status={issue.status} />
            {issue.status === "Closed" && issue.resolution_rationale && (
              <p className="mt-2">Rationale: {issue.resolution_rationale}</p>
            )}
            {issue.tracked && (
              <TrackedEditor tracked={issue.tracked}
                onSaved={(t) => { setIssue((prev) => ({ ...prev, tracked: t })); }}
                onError={(m) => setMessage(m)} />
            )}
          </div>
        ) : (
          <div className="mt-4 grid gap-5 lg:grid-cols-2">
            <div className="rounded-md border border-slate-200 p-4">
              <h3 className="text-sm font-semibold text-slate-800">Close</h3>
              <p className="text-xs text-slate-500">Record a resolution rationale and mark the row Closed.</p>
              <textarea
                className="mt-2 min-h-24 w-full rounded-md border border-slate-200 p-2 text-sm"
                placeholder="Resolution rationale (required)"
                value={rationale} onChange={(e) => setRationale(e.target.value)} />
              <Button className="mt-2" size="sm" onClick={close} disabled={!rationale.trim()}>
                <CheckCircle2 className="h-4 w-4" /> Close Issue
              </Button>
            </div>
            <div className="rounded-md border border-slate-200 p-4">
              <h3 className="text-sm font-semibold text-slate-800">Raise a tracked issue</h3>
              <p className="text-xs text-slate-500">For escalation</p>
              {!raiseOpen ? (
                <Button className="mt-2" size="sm" variant="outline" onClick={() => setRaiseOpen(true)}>
                  <Ticket className="h-4 w-4" /> Raise Issue…
                </Button>
              ) : (
                <div className="mt-2 space-y-2">
                  <Field label="Title">
                    <input className="w-full rounded-md border border-slate-200 p-2 text-sm" value={raiseForm.title}
                      onChange={(e) => setRaiseForm({ ...raiseForm, title: e.target.value })} />
                  </Field>
                  <Field label="Description (pre-filled from the RCA)">
                    <textarea className="min-h-20 w-full rounded-md border border-slate-200 p-2 text-sm" value={raiseForm.description}
                      onChange={(e) => setRaiseForm({ ...raiseForm, description: e.target.value })} />
                  </Field>
                  <div className="grid grid-cols-3 gap-2">
                    <Field label="Owner">
                      <input className="w-full rounded-md border border-slate-200 p-2 text-sm" value={raiseForm.owner}
                        onChange={(e) => setRaiseForm({ ...raiseForm, owner: e.target.value })} />
                    </Field>
                    <Field label="Priority">
                      <select className="w-full rounded-md border border-slate-200 p-2 text-sm" value={raiseForm.priority}
                        onChange={(e) => setRaiseForm({ ...raiseForm, priority: e.target.value })}>
                        {["Critical", "High", "Medium", "Low"].map((p) => <option key={p}>{p}</option>)}
                      </select>
                    </Field>
                    <Field label="Target date">
                      <input type="date" className="w-full rounded-md border border-slate-200 p-2 text-sm" value={raiseForm.target_date}
                        onChange={(e) => setRaiseForm({ ...raiseForm, target_date: e.target.value })} />
                    </Field>
                  </div>
                  <Button size="sm" onClick={raiseTracked} disabled={!raiseForm.title.trim()}>
                    <Ticket className="h-4 w-4" /> Create Tracked Issue
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}
      </section>}
    </main>
  );
}

function Fact({ label, value }) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase text-slate-500">{label}</div>
      <div className="mt-0.5 truncate text-sm font-medium text-slate-900" title={value}>{value}</div>
    </div>
  );
}

function Field({ label, children }) {
  return <label className="block text-xs font-semibold text-slate-600">{label}<div className="mt-1 font-normal">{children}</div></label>;
}
