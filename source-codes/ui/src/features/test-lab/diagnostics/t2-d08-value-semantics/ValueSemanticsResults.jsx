import { useMemo, useState } from "react";
import { Download, FileText, FolderArchive, Tags } from "lucide-react";

import { downloadAnalysisArtifactV2, downloadDiagnosticReportV2 } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FindingActions, FindingStateBadge, IssueLifecycleActions } from "@/pages/testlab/FindingWorkflow";
import { matchesFindingFilter } from "@/pages/testlab/findingWorkflowState";

const readable = (value) => String(value || "—")
  .replaceAll("_", " ")
  .toLowerCase()
  .replace(/^./, (letter) => letter.toUpperCase());

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function ActionBanner({ summary }) {
  const action = summary?.executive_action_summary?.overall_user_decision;
  const styles = action === "RAISE_ISSUE_FOR_RCA"
    ? "border-rose-200 bg-rose-50 text-rose-950"
    : action === "REVIEW_CONFIGURATION"
      ? "border-amber-200 bg-amber-50 text-amber-950"
      : "border-emerald-200 bg-emerald-50 text-emerald-950";
  return (
    <div className={`rounded-xl border p-4 ${styles}`}>
      <p className="text-xs font-semibold uppercase tracking-wide">Recommended outcome</p>
      <h2 className="mt-1 text-lg font-semibold">{readable(action)}</h2>
      <p className="mt-1 text-sm">{summary?.executive_action_summary?.plain_language_outcome}</p>
    </div>
  );
}

export default function ValueSemanticsResults({
  results = [],
  onDisposition,
  onCloseIssue,
  workflowFilter = "all",
  workflowSummary,
  runId,
}) {
  const row = results.find((value) => value.metrics_json?.result_kind === "value_semantics_run_summary");
  const summary = row?.metrics_json;
  const [downloadError, setDownloadError] = useState("");
  const findings = useMemo(() => row?.findings || [], [row]);
  const visibleFindings = useMemo(
    () => workflowFilter === "all"
      ? findings
      : findings.filter((finding) => matchesFindingFilter({ findings: [finding] }, workflowFilter)),
    [findings, workflowFilter],
  );

  if (!summary) return null;

  const rollup = summary.rollup;
  const tagCards = [
    ["CENSORED", rollup.censored_cells, "Expected analytical treatment"],
    ["STALE_FROZEN", rollup.stale_frozen_cells, "Review source refresh or sentinel"],
    ["NOT_APPLICABLE", rollup.not_applicable_cells, "Exclude from ordinary missingness treatment"],
  ];

  async function saveReport(fmt) {
    setDownloadError("");
    try {
      const blob = await downloadDiagnosticReportV2(runId, fmt);
      downloadBlob(blob, `value-semantics-${runId}.${fmt === "text" ? "txt" : "pdf"}`);
    } catch (error) {
      setDownloadError(error.message);
    }
  }

  async function saveArtifact(artifact) {
    setDownloadError("");
    try {
      const blob = await downloadAnalysisArtifactV2(artifact.artifact_id);
      downloadBlob(blob, artifact.payload_filename || `${artifact.artifact_type}.parquet`);
    } catch (error) {
      setDownloadError(error.message);
    }
  }

  return (
    <section className="grid gap-4" data-testid="value-semantics-results">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,1fr)]">
        <div className="grid gap-3">
          <ActionBanner summary={summary} />
          <header className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <Tags className="h-4 w-4 text-teal-700" />
                  <h3 className="font-semibold text-slate-900">Value Semantics classifications</h3>
                </div>
                <p className="mt-1 text-sm text-slate-500">Three governed tags, explicit coverage outcomes, and human-controlled issue promotion.</p>
              </div>
              <div className="flex gap-2">
                <Badge>{Number(rollup.distinct_tagged_cells || 0).toLocaleString()} tagged cells</Badge>
                <Badge variant="outline">{rollup.run_verdict}</Badge>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => saveReport("pdf")}>
                <Download className="h-4 w-4" /> Download analysis report
              </Button>
              <Button size="sm" variant="outline" onClick={() => saveReport("text")}>
                <FileText className="h-4 w-4" /> Download text report
              </Button>
              {downloadError && <span className="text-xs text-red-600">{downloadError}</span>}
            </div>
          </header>
        </div>
        {workflowSummary}
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {tagCards.map(([tag, count, note]) => (
          <div key={tag} className="rounded-xl border border-slate-200 bg-white p-4">
            <small className="font-semibold text-slate-500">{tag}</small>
            <strong className="mt-1 block text-2xl text-slate-950">{Number(count || 0).toLocaleString()}</strong>
            <p className="mt-1 text-xs text-slate-500">{note}</p>
          </div>
        ))}
      </div>

      {summary.field_scope_decisions && <section className="overflow-hidden rounded-xl border border-slate-200 bg-white" data-testid="value-semantics-field-decisions">
        <div className="border-b border-slate-200 bg-slate-50 px-4 py-3">
          <h3 className="font-semibold text-slate-800">Field scope and applicability decisions</h3>
          <p className="mt-1 text-xs text-slate-500">Not applicable is a human-confirmed field decision, not a cell tag or a pass. Excluded fields were left outside this run without that confirmation. These decisions are retained in the AAR bindings, structured report, and downloadable reports.</p>
        </div>
        <div className="max-h-80 overflow-auto"><table className="w-full min-w-[720px] text-left text-sm">
          <thead className="sticky top-0 bg-white text-xs uppercase text-slate-500"><tr><th className="px-4 py-2">Field</th><th className="px-4 py-2">Scope decision</th><th className="px-4 py-2">Reason / confirmed roles</th><th className="px-4 py-2">Confirmed by</th></tr></thead>
          <tbody>{summary.field_scope_decisions.map((field) => <tr key={field.column} className="border-t border-slate-100">
            <td className="px-4 py-2 font-semibold">{field.column}</td>
            <td className="px-4 py-2"><Badge variant="outline">{readable(field.status)}</Badge></td>
            <td className="max-w-xl whitespace-pre-wrap px-4 py-2 text-xs text-slate-600">{field.reason || field.confirmed_roles?.join(", ") || "No confirmed role; not assessed"}</td>
            <td className="px-4 py-2 text-xs text-slate-500">{field.confirmed_by || "No human confirmation"}{field.confirmed_at && <span className="block text-[10px]">{field.confirmed_at}</span>}</td>
          </tr>)}</tbody>
        </table></div>
      </section>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(20rem,1fr)]">
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 bg-slate-50 px-4 py-3">
            <h3 className="font-semibold text-slate-800">Field coverage</h3>
            <p className="text-xs text-slate-500">Untagged cells are reported separately from UNSCOPED and UNCLASSIFIED assessments.</p>
          </div>
          <div className="max-h-96 overflow-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="sticky top-0 bg-white text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-2">Field</th><th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2 text-right">Assessed</th><th className="px-4 py-2 text-right">Tagged</th>
                  <th className="px-4 py-2 text-right">Unclassified</th><th className="px-4 py-2 text-right">Unscoped</th>
                </tr>
              </thead>
              <tbody>
                {summary.field_summary.map((field) => (
                  <tr key={field.input_variable} className="border-t border-slate-100">
                    <td className="px-4 py-2"><strong>{field.input_variable}</strong><span className="block text-[11px] text-slate-500">{field.semantic_roles || "No confirmed role"}</span></td>
                    <td className="px-4 py-2"><Badge variant="outline">{readable(field.examination_status)}</Badge></td>
                    <td className="px-4 py-2 text-right">{field.assessed_cells}</td>
                    <td className="px-4 py-2 text-right">{field.tagged_cells}</td>
                    <td className="px-4 py-2 text-right">{field.unclassified_assessments}</td>
                    <td className="px-4 py-2 text-right">{field.unscoped_entries}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex items-center gap-2"><FolderArchive className="h-4 w-4 text-teal-700" /><h3 className="font-semibold text-slate-900">Analytics Artifact Repository</h3></div>
          <p className="mt-1 text-xs leading-5 text-slate-500">Download full sparse cell tags and the exhaustive assessment ledger as Parquet. The bindings and structured report remain JSON artifacts.</p>
          <div className="mt-3 grid gap-2">
            {Object.entries(summary.artifacts || {}).map(([key, artifact]) => (
              <button
                type="button"
                key={key}
                disabled={artifact.payload_media_type === "application/json"}
                onClick={() => saveArtifact(artifact)}
                className="rounded-lg border border-slate-200 p-3 text-left disabled:cursor-default"
              >
                <span className="text-xs font-semibold text-slate-800">{readable(key)}</span>
                <span className="block font-mono text-[10px] text-slate-500">{artifact.artifact_id}</span>
                <span className="mt-1 block text-[10px] text-teal-700">{artifact.payload_media_type === "application/json" ? "View in repository" : "Download Parquet"}</span>
              </button>
            ))}
          </div>
        </aside>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h3 className="font-semibold text-slate-900">Grouped issue candidates</h3>
        <p className="mt-1 text-xs text-slate-500">No issue is created automatically. Confirm or dismiss each grouped anomaly with rationale; a confirmed issue is available to the RCA workflow.</p>
        <div className="mt-3 grid gap-2">
          {visibleFindings.map((finding) => (
            <div key={finding.finding_id} className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
              <div className="min-w-64 flex-1">
                <div className="flex items-center gap-2"><strong className="text-sm text-slate-900">{finding.entity}</strong><FindingStateBadge finding={finding} /></div>
                <p className="mt-1 text-xs text-slate-600">{finding.pattern_detail}</p>
              </div>
              {finding.existing_issue
                ? <IssueLifecycleActions finding={finding} onCloseIssue={onCloseIssue} />
                : finding.review_state === "open" ? <FindingActions finding={finding} onDisposition={onDisposition} /> : null}
            </div>
          ))}
          {!visibleFindings.length && <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-500">No grouped anomaly requires issue review for this filter.</p>}
        </div>
      </div>
    </section>
  );
}
