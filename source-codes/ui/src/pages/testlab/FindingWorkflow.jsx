import { useState } from "react";
import { AlertCircle, CheckCircle2, ExternalLink, XCircle } from "lucide-react";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { findingWorkflowState } from "./findingWorkflowState";

function workflowCounts(results) {
  const findings = results.flatMap((result) => result.findings || []);
  const reviewNeeded = findings.filter((finding) => findingWorkflowState(finding) === "review_needed").length;
  const promoted = new Set();
  const closed = new Set();
  findings.forEach((finding) => {
    const state = findingWorkflowState(finding);
    const key = finding.existing_issue?.issue_row_id || finding.finding_id;
    if (state === "promoted") promoted.add(key);
    if (state === "closed") closed.add(key);
  });
  return { review_needed: reviewNeeded, promoted: promoted.size, closed: closed.size };
}

const CARDS = [
  { key: "review_needed", label: "Review needed", detail: "Awaiting a decision", Icon: AlertCircle },
  { key: "promoted", label: "Promoted issues", detail: "Open or in review", Icon: CheckCircle2 },
  { key: "closed", label: "Closed issues", detail: "Resolution recorded", Icon: XCircle },
];

export function FindingStatusCards({ results, filter, onFilter, compact = false }) {
  const counts = workflowCounts(results);
  return <section className={`h-full rounded-lg border border-slate-200 bg-white ${compact ? "p-3" : "p-4"}`} aria-labelledby="finding-workflow-title">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><h3 id="finding-workflow-title" className="text-sm font-semibold text-slate-900">Finding workflow</h3><p className="mt-0.5 text-xs text-slate-500">Choose a status to narrow the findings from this diagnostic run.</p></div>
      {filter !== "all" && <Button size="sm" variant="ghost" onClick={() => onFilter("all")}>Show all</Button>}
    </div>
    <div className={`grid gap-2 ${compact ? "mt-3" : "mt-3 sm:grid-cols-3"}`}>
      {CARDS.map(({ key, label, detail, Icon }) => <button key={key} type="button"
        onClick={() => onFilter(filter === key ? "all" : key)} aria-pressed={filter === key}
        className={`rounded-md border text-left transition-colors ${compact ? "flex min-h-12 items-center justify-between gap-3 px-3 py-2.5" : "px-3 py-3"} ${filter === key ? "border-dq-purple bg-dq-purple/5" : "border-slate-200 bg-slate-50 hover:bg-white"}`}>
        <span className={`flex items-center font-medium text-slate-600 ${compact ? "gap-2 text-xs" : "gap-1.5 text-[11px]"}`}><Icon className={`${compact ? "h-4 w-4" : "h-3.5 w-3.5"} shrink-0`} />{label}</span>
        <strong className={`text-slate-950 ${compact ? "text-xl" : "mt-1 block text-2xl"}`}>{counts[key]}</strong>
        {!compact && <small className="text-[11px] text-slate-500">{detail}</small>}
      </button>)}
    </div>
  </section>;
}

export function FindingStateBadge({ finding, fallback = null }) {
  const state = findingWorkflowState(finding);
  const issueId = finding?.existing_issue?.issue_row_id;
  if (state === "closed") return <Badge variant="secondary">Closed{issueId ? ` · ${issueId}` : ""}</Badge>;
  if (state === "promoted") return <Badge variant="success">Promoted{issueId ? ` · ${issueId}` : ""}</Badge>;
  if (state === "review_needed") return <Badge className="border-transparent bg-amber-100 text-amber-800">Review needed</Badge>;
  if (state === "dismissed") return <Badge variant="secondary">Dismissed</Badge>;
  return fallback;
}

export function FindingActions({ finding, onDisposition }) {
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  if (!finding || finding.review_state !== "open" || !onDisposition) return null;
  const submit = async () => {
    setBusy(true); setError("");
    try {
      await onDisposition(finding.finding_id, action, reason.trim());
      setAction(""); setReason("");
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };
  if (!action) return <div className="flex flex-wrap gap-2"><Button size="sm" variant="success" onClick={() => setAction("confirm_issue")}>Promote to issue...</Button><Button size="sm" variant="outline" onClick={() => setAction("dismiss")}>Dismiss...</Button></div>;
  return <div className="flex flex-wrap items-center gap-2"><input autoFocus value={reason} onChange={(event) => setReason(event.target.value)} placeholder={action === "confirm_issue" ? "Promotion rationale (required)" : "Dismissal rationale (required)"} className="h-8 min-w-64 flex-1 rounded-md border border-slate-200 bg-white px-2 text-xs" /><Button size="sm" variant={action === "confirm_issue" ? "success" : "outline"} disabled={busy || !reason.trim()} onClick={submit}>{busy ? "Saving..." : action === "confirm_issue" ? "Confirm promotion" : "Confirm dismissal"}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => { setAction(""); setReason(""); }}>Cancel</Button>{error && <p className="w-full text-xs text-red-600">{error}</p>}</div>;
}

export function OverrideIssueAction({ resultId, onPromote }) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!onPromote) return null;
  const promote = async () => {
    setBusy(true); setError("");
    try { await onPromote(resultId, reason.trim()); setEditing(false); setReason(""); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };
  if (!editing) return <Button size="sm" variant="outline" onClick={() => setEditing(true)}>Raise issue...</Button>;
  return <div className="flex flex-wrap items-center gap-2"><input autoFocus value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Override rationale (required)" className="h-8 min-w-64 flex-1 rounded-md border border-slate-200 bg-white px-2 text-xs" /><Button size="sm" variant="success" disabled={busy || !reason.trim()} onClick={promote}>{busy ? "Raising issue..." : "Confirm issue"}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => { setEditing(false); setReason(""); }}>Cancel</Button>{error && <p className="w-full text-xs text-red-600">{error}</p>}</div>;
}

export function IssueLifecycleActions({ finding, onCloseIssue }) {
  const issue = finding?.existing_issue;
  const [closing, setClosing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!issue) return null;
  const close = async () => {
    setBusy(true); setError("");
    try { await onCloseIssue(issue.issue_row_id, reason.trim()); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };
  return <div className="mt-3 flex flex-wrap items-center gap-2">
    <Button asChild size="sm" variant="outline"><Link to={`/issues/${encodeURIComponent(issue.issue_row_id)}`}><ExternalLink /> View issue</Link></Button>
    {issue.status !== "Closed" && onCloseIssue && (!closing
      ? <Button size="sm" variant="outline" onClick={() => setClosing(true)}>Close issue…</Button>
      : <><input autoFocus value={reason} placeholder="Closure rationale (required)" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-64 rounded-md border border-slate-200 bg-white px-2 text-xs" /><Button size="sm" variant="success" disabled={busy || !reason.trim()} onClick={close}>{busy ? "Closing…" : "Confirm close"}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => setClosing(false)}>Cancel</Button></>)}
    {error && <p className="w-full text-xs text-red-600">{error}</p>}
  </div>;
}
