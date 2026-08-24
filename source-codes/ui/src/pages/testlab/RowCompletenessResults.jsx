import { useState } from "react";
import {
  AlertTriangle, BarChart3, CheckCircle2, ChevronDown, ChevronRight,
  Database, Download, FileText, Info, ShieldCheck,
} from "lucide-react";

import { downloadDiagnosticReportV2 } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FindingStateBadge, IssueLifecycleActions } from "./FindingWorkflow";
import { rowCompletenessReviewCount } from "./rowCompletenessWorkflow";

const number = (value) => value == null ? "—" : Number(value).toLocaleString();
const percent = (value, digits = 1) => value == null ? "—" : `${(Number(value) * 100).toFixed(digits)}%`;
const titleCase = (value) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());

function OutcomeBadge({ outcome }) {
  if (outcome === "PASS") return <Badge variant="success">Pass</Badge>;
  if (outcome === "VIOLATION") return <Badge variant="destructive">Violation</Badge>;
  if (outcome === "NOT-ASSESSABLE") return <Badge className="border-transparent bg-amber-100 text-amber-800">Not assessable</Badge>;
  return <Badge variant="secondary">Not applicable</Badge>;
}

function VerdictBanner({ payload }) {
  const verdict = payload.overall_verdict;
  const passing = verdict === "pass";
  const inconclusive = verdict === "inconclusive";
  const notApplicable = verdict === "not_applicable";
  const Icon = passing ? CheckCircle2 : notApplicable ? Info : AlertTriangle;
  const tone = passing ? "border-emerald-200 bg-emerald-50 text-emerald-950"
    : inconclusive ? "border-amber-200 bg-amber-50 text-amber-950"
      : notApplicable ? "border-slate-200 bg-slate-50 text-slate-950" : "border-red-200 bg-red-50 text-red-950";
  return <section className={`rounded-lg border p-5 ${tone}`}>
    <div className="flex items-start gap-3"><Icon className="mt-0.5 h-5 w-5 shrink-0" />
      <div><p className="text-xs font-semibold uppercase tracking-wide opacity-70">Overall verdict</p>
        <h2 className="mt-1 text-xl font-semibold">{titleCase(verdict)}</h2>
        <p className="mt-1 text-xs opacity-80">Based only on the six deterministic Row Completeness rules and the frozen run configuration. A failed rule creates a finding for review, not an Issue Management record.</p></div>
    </div>
  </section>;
}

function MetricCard({ label, value, detail }) {
  return <div className="rounded-lg border border-slate-200 bg-white p-4"><p className="text-xs text-slate-500">{label}</p>
    <strong className="mt-1 block text-2xl text-slate-950">{value}</strong>{detail && <p className="mt-1 text-[11px] text-slate-500">{detail}</p>}</div>;
}

function EvidenceTable({ evidence = [], total = 0, truncated = false }) {
  if (!evidence.length) return <p className="text-xs text-slate-500">No example exceptions were retained for this rule.</p>;
  return <div className="overflow-x-auto rounded-md border border-slate-200"><table className="w-full text-left text-xs">
    <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2">Type</th><th className="px-3 py-2">Facility</th><th className="px-3 py-2">Period</th><th className="px-3 py-2">Segment</th><th className="px-3 py-2">Reason</th></tr></thead>
    <tbody>{evidence.map((row, index) => <tr key={`${row.finding_type}-${row.row_reference || index}`} className="border-t border-slate-100">
      <td className="px-3 py-2 capitalize">{String(row.finding_type).replaceAll("_", " ")}</td><td className="px-3 py-2">{row.facility_id ?? "—"}</td>
      <td className="px-3 py-2">{row.period || "—"}</td><td className="px-3 py-2">{row.segment || "—"}</td><td className="max-w-xl px-3 py-2 text-slate-600">{row.reason}</td>
    </tr>)}</tbody>
  </table>{truncated && <p className="border-t border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-slate-500">Showing {evidence.length} governed examples from {number(total)} findings. The total remains authoritative.</p>}</div>;
}

function CoverageRows({ rule }) {
  const rows = rule.rule_id === "T2D6-03" ? rule.metrics?.period_results : rule.rule_id === "T2D6-06" ? rule.metrics?.cell_results : null;
  if (!rows?.length) return null;
  return <div className="mt-3"><p className="mb-2 text-xs font-semibold text-slate-700">Coverage detail</p>
    <div className="max-h-64 overflow-auto rounded-md border border-slate-200"><table className="w-full text-left text-xs">
      <thead className="sticky top-0 bg-slate-50 text-slate-500"><tr>{rule.rule_id === "T2D6-06" && <th className="px-3 py-2">Segment</th>}<th className="px-3 py-2">Period</th><th className="px-3 py-2 text-right">Required</th><th className="px-3 py-2 text-right">Received</th><th className="px-3 py-2 text-right">Coverage</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={`${row.segment || "all"}-${row.period}-${index}`} className={`border-t border-slate-100 ${row.coverage < rule.continuity_floor ? "bg-red-50/60" : ""}`}>
        {rule.rule_id === "T2D6-06" && <td className="px-3 py-2">{row.segment}</td>}<td className="px-3 py-2">{row.period}</td>
        <td className="px-3 py-2 text-right">{number(row.required_facilities)}</td><td className="px-3 py-2 text-right">{number(row.received_facilities)}</td><td className="px-3 py-2 text-right font-medium">{percent(row.coverage)}</td>
      </tr>)}</tbody>
    </table></div></div>;
}

function FindingDecision({ finding, onDisposition }) {
  const [action, setAction] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!finding || finding.outcome !== "VIOLATION" || finding.existing_issue
      || finding.review_state !== "open" || !onDisposition) return null;
  const submit = async () => {
    setBusy(true); setError("");
    try { await onDisposition(finding.finding_id, action, reason.trim()); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };
  return <div className="mt-3 border-t border-slate-100 pt-3">
    <p className="mb-2 text-xs text-slate-600">Review this finding. No issue will be created unless you explicitly confirm it.</p>
    {!action ? <div className="flex flex-wrap gap-2"><Button size="sm" variant="success" onClick={() => setAction("confirm_issue")}>Create issue...</Button><Button size="sm" variant="outline" onClick={() => setAction("dismiss")}>Dismiss finding...</Button></div>
      : <div className="flex flex-wrap gap-2"><input autoFocus value={reason} onChange={(event) => setReason(event.target.value)} placeholder={`${action === "confirm_issue" ? "Issue creation" : "Dismissal"} rationale (required)`} className="h-8 min-w-72 flex-1 rounded-md border border-slate-200 px-2 text-xs" /><Button size="sm" variant={action === "confirm_issue" ? "success" : "outline"} disabled={busy || !reason.trim()} onClick={submit}>{busy ? "Saving..." : action === "confirm_issue" ? "Confirm and create issue" : "Confirm dismissal"}</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => { setAction(""); setReason(""); }}>Cancel</Button></div>}
    {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
  </div>;
}

function RuleCard({ rule, finding, onDisposition, onCloseIssue }) {
  const [open, setOpen] = useState(rule.outcome === "VIOLATION");
  return <article className="overflow-hidden rounded-lg border border-slate-200 bg-white">
    <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open} className="flex w-full items-start gap-3 p-4 text-left hover:bg-slate-50">
      {open ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" /> : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />}
      <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold text-slate-500">{rule.rule_id}</span><h3 className="text-sm font-semibold text-slate-900">{rule.title}</h3><OutcomeBadge outcome={rule.outcome} />{finding && <FindingStateBadge finding={finding} />}</div>
        <p className="mt-1 text-xs text-slate-600">{rule.explanation}</p></div>
      <div className="shrink-0 text-right"><strong className="block text-lg text-slate-900">{number(rule.issue_count)}</strong><span className="text-[10px] text-slate-500">gap instances</span></div>
    </button>
    {open && <div className="border-t border-slate-200 p-4">
      <div className="mb-3 grid gap-2 sm:grid-cols-3"><div className="rounded-md bg-slate-50 px-3 py-2 text-xs"><span className="text-slate-500">Measured coverage</span><strong className="block text-slate-900">{percent(rule.measure)}</strong></div><div className="rounded-md bg-slate-50 px-3 py-2 text-xs"><span className="text-slate-500">Minimum</span><strong className="block text-slate-900">{percent(rule.continuity_floor)}</strong></div><div className="rounded-md bg-slate-50 px-3 py-2 text-xs"><span className="text-slate-500">Detailed findings</span><strong className="block text-slate-900">{number(rule.total_findings)}</strong></div></div>
      {rule.na_reason && <p className="mb-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">{rule.na_reason}</p>}
      <EvidenceTable evidence={rule.evidence} total={rule.total_findings} truncated={rule.evidence_truncated} />
      <CoverageRows rule={rule} />
      {rule.outcome === "VIOLATION" && <FindingDecision finding={finding} onDisposition={onDisposition} />}
      {finding?.existing_issue && <div className="mt-3 border-t border-slate-100 pt-1"><IssueLifecycleActions finding={finding} onCloseIssue={onCloseIssue} /></div>}
    </div>}
  </article>;
}

function InferenceDisclosure({ metrics }) {
  const journey = metrics.current_journey_inference_disclosure || {};
  const calculation = metrics.source_calculation_inference_disclosure || {};
  return <section className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-dq-purple" /><h3 className="text-sm font-semibold text-slate-900">LLM and inference disclosure</h3></div>
    <div className="mt-3 grid gap-3 md:grid-cols-2"><div className="rounded-md border border-slate-200 bg-slate-50 p-3"><p className="text-xs font-semibold text-slate-800">Current diagnostic journey</p><strong className="mt-1 block text-xl text-slate-950">{number(journey.llm_call_count || 0)} LLM calls</strong><p className="mt-1 text-[11px] text-slate-500">{journey.statement || "No LLM call was made."}</p></div>
      <div className="rounded-md border border-slate-200 bg-slate-50 p-3"><p className="text-xs font-semibold text-slate-800">Source calculation</p><strong className="mt-1 block text-xl text-slate-950">{number(calculation.llm_call_count || 0)} LLM calls</strong><p className="mt-1 text-[11px] text-slate-500">Verdict influenced by LLM: {calculation.verdict_influenced_by_llm ? "Yes" : "No"}</p></div></div>
    {(journey.events || []).length > 0 && <details className="mt-3 text-xs"><summary className="cursor-pointer font-medium text-slate-700">View documented inference events</summary><div className="mt-2 grid gap-2">{journey.events.map((event, index) => <div key={event.event_id || index} className="rounded-md border border-slate-200 p-3"><p className="font-medium text-slate-800">{titleCase(event.purpose || event.inference_type || "Inference event")} · {event.status || event.disposition}</p><p className="mt-1 text-slate-500">Model: {event.model || "Deterministic"} · Row-level data included: {event.row_level_data_included ? "Yes" : "No"}</p></div>)}</div></details>}
  </section>;
}

export default function RowCompletenessResults({ results, onDisposition, onCloseIssue }) {
  const summary = results.find((result) => result.metrics_json?.structured_result?.diagnostic_id === 6);
  const metrics = summary?.metrics_json || {};
  const payload = metrics.structured_result;
  const [reportBusy, setReportBusy] = useState(false);
  const [reportError, setReportError] = useState("");
  if (!payload) return <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">The Row Completeness result contract is unavailable for this run.</div>;
  const findings = Object.fromEntries((summary.findings || []).map((finding) => [finding.rule_id, finding]));
  const reviewRequired = rowCompletenessReviewCount(summary.findings || []);
  const artifact = metrics.reconciliation_artifact || {};
  const knowledge = metrics.knowledge_provenance || {};
  const downloadReport = async () => {
    setReportBusy(true); setReportError("");
    try {
      const blob = await downloadDiagnosticReportV2(payload.origin_run_id);
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `row-completeness-${payload.origin_run_id}.pdf`; anchor.click(); URL.revokeObjectURL(url);
    } catch (error) { setReportError(error.message); } finally { setReportBusy(false); }
  };
  return <div className="grid gap-4" data-testid="row-completeness-results">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-lg font-semibold text-slate-950">Row Completeness results</h1><p className="mt-1 text-xs text-slate-500">Observed-span reconciliation · run {payload.origin_run_id}</p></div><Button onClick={downloadReport} disabled={reportBusy}><Download className="h-4 w-4" />{reportBusy ? "Preparing PDF..." : "Download external PDF"}</Button></div>
    {reportError && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{reportError}</div>}
    <VerdictBanner payload={payload} />
    {reviewRequired > 0 && <section className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"><strong>{number(reviewRequired)} failed {reviewRequired === 1 ? "rule requires" : "rules require"} review.</strong><p className="mt-1 text-xs text-amber-800">Review the evidence for each finding, then either create an issue with a rationale or dismiss the finding with a rationale.</p></section>}
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5"><MetricCard label="Continuity coverage" value={percent(payload.continuity_coverage)} detail={`${percent(payload.scope.continuity_floor, 0)} minimum`} /><MetricCard label="Required facility-period rows" value={number(payload.required_facility_period_pairs)} detail="Inside observed spans" /><MetricCard label="Received required rows" value={number(payload.received_required_pairs)} /><MetricCard label="Detected gap instances" value={number(payload.issue_summary?.primary_issue_instances)} /><MetricCard label="Scope" value={`${number(payload.facilities_assessed)} / ${number(payload.periods_assessed)}`} detail="Facilities / periods" /></section>
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]"><section><div className="mb-2 flex items-center gap-2"><BarChart3 className="h-4 w-4 text-dq-purple" /><h2 className="text-sm font-semibold text-slate-900">Rule-level results</h2></div><div className="grid gap-2">{payload.rules.map((rule) => <RuleCard key={rule.rule_id} rule={rule} finding={findings[rule.rule_id]} onDisposition={onDisposition} onCloseIssue={onCloseIssue} />)}</div></section>
      <aside className="grid content-start gap-4"><section className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex items-center gap-2"><Database className="h-4 w-4 text-dq-purple" /><h3 className="text-sm font-semibold text-slate-900">Analytics Artifact Repository</h3></div><p className="mt-3 text-xs text-slate-600">The governed reconciliation artifact is the source for these results and the external report.</p><dl className="mt-3 grid gap-2 text-xs"><div><dt className="text-slate-400">Artifact ID</dt><dd className="break-all font-medium text-slate-800">{artifact.artifact_id}</dd></div><div><dt className="text-slate-400">Payload hash</dt><dd className="break-all font-mono text-[10px] text-slate-700">{artifact.payload_hash}</dd></div><div><dt className="text-slate-400">Repository action</dt><dd className="font-medium text-slate-800">{artifact.reused ? "Reused existing artifact" : "Created new artifact"}</dd></div></dl></section>
      <section className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-dq-purple" /><h3 className="text-sm font-semibold text-slate-900">Knowledge version</h3></div><dl className="mt-3 grid gap-2 text-xs"><div><dt className="text-slate-400">Version</dt><dd className="break-all font-medium text-slate-800">{knowledge.version_id || "Legacy manifest"}</dd></div><div><dt className="text-slate-400">Package hash</dt><dd className="break-all font-mono text-[10px] text-slate-700">{knowledge.package_hash || "Not recorded"}</dd></div><div><dt className="text-slate-400">Retrieval record</dt><dd className="break-all font-medium text-slate-800">{knowledge.retrieval_manifest_id || "Not recorded"}</dd></div></dl></section>
      <section className="rounded-lg border border-blue-200 bg-blue-50 p-4"><div className="flex gap-2"><Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-700" /><div><h3 className="text-sm font-semibold text-blue-950">How to read “required”</h3><p className="mt-1 text-xs text-blue-900">A facility-period row is required only between that facility's first and last observed periods. It does not mean an externally supplied population.</p></div></div></section>
      <section className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex items-center gap-2"><FileText className="h-4 w-4 text-dq-purple" /><h3 className="text-sm font-semibold text-slate-900">Recommended next steps</h3></div><ol className="mt-3 list-decimal space-y-2 pl-4 text-xs text-slate-600"><li>Review each failed rule and its retained examples.</li><li>Create an issue only for findings that require ownership and remediation tracking.</li><li>Dismiss findings that do not require action, recording the rationale.</li><li>Download the external report for governed sharing.</li><li>Resolve source gaps and rerun against a new immutable snapshot.</li></ol></section></aside></div>
    <InferenceDisclosure metrics={metrics} />
  </div>;
}
