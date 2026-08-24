import { useState } from "react";
import { AlertOctagon, Check, Eye, HelpCircle, MinusCircle, ShieldCheck, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { staleSourceText } from "@/lib/staleSources";
import FeatureTargetResults from "./FeatureTargetResults";
import { FindingStateBadge, FindingStatusCards, IssueLifecycleActions } from "./FindingWorkflow";
import { findingWorkflowState, matchesFindingFilter } from "./findingWorkflowState";
import PopulationStabilityResults from "./PopulationStabilityResults";
import RowCompletenessResults from "./RowCompletenessResults";

// testlab-redesign-0.4.0.md §3 Step 4 — findings, grouped area -> diagnostic
// -> finding, rendered BY decision_type (FWK-07). This is the module's most
// requirement-bearing surface:
//
//  * A candidate_flag / contextual / classification_output / sme_gate
//    finding NEVER uses the VIOLATION visual treatment (no red/destructive
//    badge, no "failure" icon) — FWK-07 / 6-T14. It renders as a distinct
//    "for review" card with a confirm/dismiss disposition instead of a
//    verdict.
//  * NOT-APPLICABLE always states its reason — never blank, never silent
//    (CFR-04).
//  * Severity orders findings (CRITICAL -> MATERIAL -> MINOR); this panel
//    never re-sorts — the API already returns findings in that order
//    (persisted in `seq` order from the structured result) — it only
//    renders the order it is given (CFR-08: a display concern, counts are
//    never recomputed client-side).
//
// Only `decision_type: "verdict"` has real data in slice 1 (diagnostic #4).
// The other branches render defensively so the contract is provably correct
// before any statistical/candidate-flag diagnostic ships (6-T14).

const SEVERITY_BADGE = { CRITICAL: "destructive", MATERIAL: "warning", MINOR: "secondary" };

function StaleBadge({ artefact, onRecompute }) {
  if (!artefact?.stale) return null;
  const source = artefact.stale_sources?.[0];
  return <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="stale-badge">
    <span>{staleSourceText(source)}</span>
    <Button size="sm" variant="outline" onClick={onRecompute}>Recompute</Button>
  </div>;
}

function SeverityBadge({ severity }) {
  if (!severity) return null;
  return <Badge variant={SEVERITY_BADGE[severity] || "outline"}>{severity}</Badge>;
}

function PatternBadge({ pattern, detail }) {
  if (!pattern) return null;
  const implies = pattern === "CLUSTERED" ? "feed/segment fault" : "capture error";
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700"
      title={detail || `${pattern} — suggests a ${implies}`}>
      {pattern} <span className="text-indigo-400">→ {implies}</span>
    </span>
  );
}

function EvidenceTable({ rows }) {
  if (!rows?.length) return null;
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  return (
    <div className="mt-2 overflow-x-auto rounded-md border border-slate-100">
      <table className="w-full text-[11px]">
        <thead className="bg-slate-50 text-left uppercase text-slate-400">
          <tr>{cols.map((c) => <th key={c} className="px-2 py-1">{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t border-slate-100">
              {cols.map((c) => <td key={c} className="px-2 py-1 text-slate-700">{String(row[c] ?? "")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Verdict findings (PASS / VIOLATION / NOT-APPLICABLE) --------------------

function ViolationCard({ f, onCloseIssue }) {
  return (
    <div data-testid="finding-violation" className="rounded-lg border border-red-200 bg-red-50/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={f.severity} />
        <Badge variant="destructive">VIOLATION</Badge>
        <span className="font-medium text-slate-900">{f.rule_id}</span>
        {f.regulatory_ref && <span className="text-xs text-slate-500">{f.regulatory_ref}</span>}
        <PatternBadge pattern={f.pattern} detail={f.pattern_detail} />
      </div>
      <p className="mt-1 text-xs text-slate-600">{f.rule_text}</p>
      <p className="mt-1 text-xs text-slate-500">
        {f.violation_count ?? 0} violation(s), rate {f.rate != null ? `${(f.rate * 100).toFixed(2)}%` : "—"} vs tolerance {f.tolerance ?? 0}
        {" · "}{f.scope_rows_evaluated ?? 0} rows evaluated
        {f.scope_rows_skipped ? `, ${f.scope_rows_skipped} skipped` : ""}
      </p>
      {!!(f.exceptions_json?.count) && (
        <div className="mt-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-900">
          <span className="font-semibold">{f.exceptions_json.count} exception(s) applied:</span>{" "}
          {(f.exceptions_json.notes || []).join("; ") || "(no reason recorded)"}
        </div>
      )}
      <EvidenceTable rows={f.evidence_json} />
      <IssueLifecycleActions finding={f} onCloseIssue={onCloseIssue} />
    </div>
  );
}

function PassRow({ f }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-emerald-100 bg-emerald-50/40 px-3 py-1.5 text-xs">
      <SeverityBadge severity={f.severity} />
      <Badge variant="success">PASS</Badge>
      <span className="font-medium text-slate-800">{f.rule_id}</span>
      <span className="text-slate-500">{f.rule_text}</span>
      <span className="ml-auto text-slate-400">{f.scope_rows_evaluated ?? 0} rows evaluated</span>
    </div>
  );
}

function NotApplicableRow({ f }) {
  return (
    <div data-testid="finding-na" className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs">
      <Badge variant="secondary"><MinusCircle className="mr-1 h-3 w-3" /> NOT-APPLICABLE</Badge>
      <span className="font-medium text-slate-700">{f.rule_id}</span>
      {/* CFR-04 — the reason is mandatory and never hidden. */}
      <span data-testid="finding-na-reason" className="text-slate-600">{f.na_reason || "no reason recorded"}</span>
    </div>
  );
}

function VerdictResult({ result, onCloseIssue }) {
  const findings = result.findings || [];
  const violations = findings.filter((f) => f.outcome === "VIOLATION");
  const passes = findings.filter((f) => f.outcome === "PASS");
  const na = findings.filter((f) => f.outcome === "NOT-APPLICABLE");
  const verdictVariant = result.verdict === "violation" ? "destructive" : result.verdict === "pass" ? "success" : "secondary";
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge data-testid="result-verdict" data-verdict={result.verdict} variant={verdictVariant} className="uppercase">{result.verdict}</Badge>
        <span className="text-sm font-medium text-slate-800">{result.entity_or_table}</span>
        {result.verdict === "not_applicable" && result.na_reason && (
          <span data-testid="result-na-reason" className="text-xs text-slate-500">{result.na_reason}</span>
        )}
      </div>
      {violations.length > 0 && (
        <div data-testid="violations-section" className="grid gap-2">
          <h4 className="text-xs font-semibold uppercase text-slate-500">Violations, severity-ordered ({violations.length})</h4>
          {violations.map((f) => <ViolationCard key={f.finding_id} f={f} onCloseIssue={onCloseIssue} />)}
        </div>
      )}
      {passes.length > 0 && (
        <details data-testid="passes-details">
          <summary className="cursor-pointer text-xs font-semibold uppercase text-slate-500">Passes ({passes.length})</summary>
          <div className="mt-2 grid gap-1">
            {passes.map((f) => <PassRow key={f.finding_id} f={f} />)}
          </div>
        </details>
      )}
      {na.length > 0 && (
        <details data-testid="na-details">
          <summary className="cursor-pointer text-xs font-semibold uppercase text-slate-500">Not applicable ({na.length})</summary>
          <div className="mt-2 grid gap-1">
            {na.map((f) => <NotApplicableRow key={f.finding_id} f={f} />)}
          </div>
        </details>
      )}
      {findings.length === 0 && (
        <p className="text-xs text-slate-500">No per-rule findings recorded for this result.</p>
      )}
    </div>
  );
}

// --- Review findings (candidate_flag / contextual / sme_gate) ---------------
// FWK-07 / 6-T14: never the VIOLATION treatment — a distinct indigo "for
// review" badge/border, a HelpCircle icon (never AlertOctagon/destructive),
// and the disposition (confirm/dismiss) IS the decision here, not a verdict.

function ReviewCard({ f, onDisposition, onCloseIssue }) {
  const [dismissing, setDismissing] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const disposed = ["promoted", "closed", "dismissed"].includes(findingWorkflowState(f));

  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      await onDisposition(f.finding_id, "confirm_issue", reason.trim());
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const dismiss = async () => {
    if (!reason.trim()) { setError("a reason is required to dismiss a finding"); return; }
    setBusy(true);
    setError("");
    try {
      await onDisposition(f.finding_id, "dismiss", reason.trim());
      setDismissing(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="review-card" className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge data-testid="review-badge" className="border-transparent bg-indigo-100 text-indigo-800">
          <HelpCircle className="mr-1 h-3 w-3" /> FOR REVIEW
        </Badge>
        {f.rule_id && <span className="font-medium text-slate-900">{f.rule_id}</span>}
        {disposed && <FindingStateBadge finding={f} />}
      </div>
      <p className="mt-1 text-xs text-slate-600">{f.rule_text || f.description || f.rationale || "Candidate flag — SME review required."}</p>
      {!disposed && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {!promoting ? <Button size="sm" variant="success" disabled={busy} onClick={() => setPromoting(true)}><Check className="h-3.5 w-3.5" /> Promote to issue…</Button>
            : <><input autoFocus placeholder="Promotion rationale (required)" value={reason} onChange={(e) => setReason(e.target.value)} className="h-7 flex-1 rounded-md border border-slate-200 px-2 text-xs" /><Button size="sm" variant="success" disabled={busy || !reason.trim()} onClick={confirm}>Confirm promotion</Button></>}
          {!dismissing ? (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setDismissing(true)}>
              <X className="h-3.5 w-3.5" /> Dismiss…
            </Button>
          ) : (
            <>
              <input autoFocus placeholder="Reason (required)" value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="h-7 flex-1 rounded-md border border-slate-200 px-2 text-xs" />
              <Button size="sm" variant="outline" disabled={busy || !reason.trim()} onClick={dismiss}>Dismiss</Button>
            </>
          )}
        </div>
      )}
      <IssueLifecycleActions finding={f} onCloseIssue={onCloseIssue} />
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}

function ReviewResult({ result, onDisposition, onCloseIssue }) {
  const findings = result.findings || [];
  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-2">
        <Badge className="border-transparent bg-indigo-100 text-indigo-800"><Eye className="mr-1 h-3 w-3" /> {result.decision_type}</Badge>
        <span className="text-sm font-medium text-slate-800">{result.entity_or_table}</span>
      </div>
      {findings.length > 0 ? (
        findings.map((f) => <ReviewCard key={f.finding_id} f={f} onDisposition={onDisposition} onCloseIssue={onCloseIssue} />)
      ) : (
        <p className="text-xs text-slate-500">No candidate flags on this result.</p>
      )}
    </div>
  );
}

function ClassificationResult({ result }) {
  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-2">
        <Badge variant="outline"><ShieldCheck className="mr-1 h-3 w-3" /> classification_output — no pass/fail</Badge>
        <span className="text-sm font-medium text-slate-800">{result.entity_or_table}</span>
      </div>
      <ul className="grid gap-1 text-xs text-slate-600">
        {(result.findings || []).map((f, i) => (
          <li key={f.finding_id || i} className="rounded-md border border-slate-100 px-2 py-1">
            {f.rule_text || f.tag || JSON.stringify(f)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ResultPromotionControl({ result, onPromote, onCloseIssue }) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const findings = result.findings || [];
  if (["run_summary", "psi_run_summary"].includes(result.metrics_json?.result_kind)) return null;
  const promoted = findings.some((finding) => finding.review_state === "confirmed");
  const hasReviewAction = ["candidate_flag", "contextual", "sme_gate"].includes(result.decision_type)
    && findings.some((finding) => finding.review_state === "open");
  const recommended = result.verdict === "violation" || findings.some((finding) =>
    ["VIOLATION", "CANDIDATE", "CONTEXTUAL"].includes(finding.outcome) && finding.review_state !== "dismissed");
  if (!onPromote || hasReviewAction) return null;
  if (promoted) return <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900"><strong>Disposition:</strong> <FindingStateBadge finding={findings.find((finding) => findingWorkflowState(finding) === "promoted" || findingWorkflowState(finding) === "closed")} /><IssueLifecycleActions finding={findings.find((finding) => finding.existing_issue)} onCloseIssue={onCloseIssue} /></div>;
  const promote = async () => {
    setBusy(true); setError("");
    try { await onPromote(result.result_id, reason.trim() || undefined); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };
  return <div className={`rounded-md border px-3 py-2 text-xs ${recommended ? "border-indigo-200 bg-indigo-50/40" : "border-slate-200 bg-slate-50"}`}>
    <div className="flex flex-wrap items-center gap-2"><span><strong>Product recommendation:</strong> {recommended ? "promotion is recommended based on this result." : "promotion is not recommended by the automated result."}</span>
      {!editing && <Button size="sm" variant={recommended ? "success" : "outline"} onClick={() => setEditing(true)}>{recommended ? "Promote to issue…" : "Override and promote…"}</Button>}
    </div>
    {editing && <div className="mt-2 flex flex-wrap gap-2"><input autoFocus value={reason} placeholder="Promotion rationale (required)" onChange={(event) => setReason(event.target.value)} className="h-8 min-w-64 flex-1 rounded-md border border-slate-200 bg-white px-2" /><Button size="sm" disabled={busy || !reason.trim()} onClick={promote}>{busy ? "Promoting…" : "Confirm promotion"}</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing(false)}>Cancel</Button></div>}
    {error && <p className="mt-1 text-red-600">{error}</p>}
  </div>;
}

const DECISION_RENDERERS = {
  verdict: VerdictResult,
  candidate_flag: ReviewResult,
  contextual: ReviewResult,
  sme_gate: ReviewResult,
  classification_output: ClassificationResult,
};

function DiagnosticResultsSummary({ results }) {
  const findings = results.flatMap((result) => result.findings || []);
  const values = [
    { label: "Results", value: results.length },
    { label: "Findings", value: findings.length },
    { label: "Violations", value: findings.filter((finding) => finding.outcome === "VIOLATION").length },
    { label: "Passed", value: findings.filter((finding) => finding.outcome === "PASS").length },
  ];
  const decisionTypes = [...new Set(results.map((result) => result.decision_type).filter(Boolean))]
    .map((value) => value.replaceAll("_", " ")).join(" · ");
  return <section className="h-full rounded-lg border border-slate-200 bg-white p-4" aria-labelledby="diagnostic-results-summary-title">
    <h3 id="diagnostic-results-summary-title" className="text-sm font-semibold text-slate-900">Diagnostic results summary</h3>
    <p className="mt-0.5 text-xs capitalize text-slate-500">{decisionTypes || "Completed diagnostic results"}</p>
    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {values.map((item) => <div key={item.label} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
        <strong className="block text-xl text-slate-950">{item.value}</strong>
        <span className="text-[11px] text-slate-500">{item.label}</span>
      </div>)}
    </div>
  </section>;
}

export default function FindingsPanel({ results = [], onDisposition, onPromote, onCloseIssue, onRecompute, loading, error }) {
  const [workflowFilter, setWorkflowFilter] = useState("all");
  const [diagnosticFilter, setDiagnosticFilter] = useState("all");
  const selectWorkflowFilter = (nextFilter) => {
    setDiagnosticFilter("all");
    setWorkflowFilter(nextFilter);
  };
  const selectDiagnosticFilter = (nextFilter) => {
    setWorkflowFilter("all");
    setDiagnosticFilter(nextFilter);
  };
  if (loading) return <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">Loading findings…</div>;
  if (error) return <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-700">{error}</div>;
  if (!results.length) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">
        <AlertOctagon className="h-4 w-4 text-slate-400" /> No results yet for this run.
      </div>
    );
  }
  if (results.some((result) => result.metrics_json?.result_kind === "psi_run_summary")) {
    return <PopulationStabilityResults results={results} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} workflowFilter={workflowFilter}
      diagnosticFilter={diagnosticFilter} onDiagnosticFilter={selectDiagnosticFilter}
      workflowSummary={<FindingStatusCards results={results} filter={workflowFilter} onFilter={selectWorkflowFilter} compact />} />;
  }
  if (results.some((result) => result.metrics_json?.structured_result?.diagnostic_id === 6)) {
    return <RowCompletenessResults results={results} onDisposition={onDisposition} onCloseIssue={onCloseIssue} />;
  }
  if (results.some((result) => result.metrics_json?.result_kind === "run_summary")) {
    return <FeatureTargetResults results={results} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} workflowFilter={workflowFilter}
      diagnosticFilter={diagnosticFilter} onDiagnosticFilter={selectDiagnosticFilter}
      workflowSummary={<FindingStatusCards results={results} filter={workflowFilter} onFilter={selectWorkflowFilter} compact />} />;
  }
  const visibleResults = results.map((result) => workflowFilter === "all" ? result : {
    ...result, findings: (result.findings || []).filter((finding) => matchesFindingFilter({ findings: [finding] }, workflowFilter)),
  }).filter((result) => workflowFilter === "all" || result.findings.length);
  return (
    <div className="grid gap-4">
      <div className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,1fr)]">
        <DiagnosticResultsSummary results={results} />
        <FindingStatusCards results={results} filter={workflowFilter} onFilter={selectWorkflowFilter} compact />
      </div>
      {visibleResults.map((result) => {
        const Renderer = DECISION_RENDERERS[result.decision_type];
        return (
          <section key={result.result_id} className="rounded-lg border border-slate-200 bg-white p-4">
            <StaleBadge artefact={result} onRecompute={onRecompute} />
            {Renderer
              ? <Renderer result={result} onDisposition={onDisposition} onCloseIssue={onCloseIssue} />
              : (
                // An unrecognized decision_type NEVER falls back to verdict
                // styling — a neutral, honest "unrenderable" note instead.
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <HelpCircle className="h-4 w-4" /> Unrecognized decision type “{result.decision_type}” — rendered generically.
                </div>
              )}
            <div className="mt-3"><ResultPromotionControl result={result} onPromote={onPromote} onCloseIssue={onCloseIssue} /></div>
          </section>
        );
      })}
      {!visibleResults.length && <div className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">No findings match this workflow status.</div>}
    </div>
  );
}
