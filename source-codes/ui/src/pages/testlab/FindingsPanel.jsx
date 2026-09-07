import { useState } from "react";
import { AlertOctagon } from "lucide-react";

import FeatureTargetResults from "@/features/test-lab/diagnostics/t1-d02-feature-target-separation/FeatureTargetResults";
import CrossFieldResults from "@/features/test-lab/diagnostics/t2-d04-cross-field-business-rule/CrossFieldResults";
import RowCompletenessResults from "@/features/test-lab/diagnostics/t2-d06-row-completeness/RowCompletenessResults";
import PopulationStabilityResults from "@/features/test-lab/diagnostics/t4-d14-population-stability/PopulationStabilityResults";
import DirectionalityResults from "@/features/test-lab/diagnostics/t2-d11-directional-monotonic-consistency/DirectionalityResults";
import ValueSemanticsResults from "@/features/test-lab/diagnostics/t2-d08-value-semantics/ValueSemanticsResults";
import { FindingStatusCards } from "./FindingWorkflow";

export default function FindingsPanel({
  results = [], onDisposition, onPromote, onCloseIssue, onRecompute, loading, error, runId,
}) {
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
    return <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">
      <AlertOctagon className="h-4 w-4 text-slate-400" /> No results yet for this run.
    </div>;
  }
  if (results.some((result) => result.metrics_json?.result_kind === "psi_run_summary")) {
    return <PopulationStabilityResults results={results} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} workflowFilter={workflowFilter}
      diagnosticFilter={diagnosticFilter} onDiagnosticFilter={selectDiagnosticFilter} runId={runId}
      workflowSummary={<FindingStatusCards results={results} filter={workflowFilter} onFilter={selectWorkflowFilter} compact />} />;
  }
  if (results.some((result) => result.metrics_json?.result_kind === "directionality_run_summary")) {
    return <DirectionalityResults results={results} onDisposition={onDisposition} onPromote={onPromote}
      onCloseIssue={onCloseIssue} workflowFilter={workflowFilter}
      diagnosticFilter={diagnosticFilter} onDiagnosticFilter={selectDiagnosticFilter} runId={runId}
      workflowSummary={<FindingStatusCards results={results} filter={workflowFilter} onFilter={selectWorkflowFilter} compact />} />;
  }
  if (results.some((result) => result.metrics_json?.result_kind === "value_semantics_run_summary")) {
    return <ValueSemanticsResults results={results} onDisposition={onDisposition}
      onCloseIssue={onCloseIssue} workflowFilter={workflowFilter} runId={runId}
      workflowSummary={<FindingStatusCards results={results} filter={workflowFilter} onFilter={selectWorkflowFilter} compact />} />;
  }
  if (results.some((result) => result.metrics_json?.structured_result?.diagnostic_id === 6)) {
    return <RowCompletenessResults results={results} onDisposition={onDisposition} onCloseIssue={onCloseIssue} />;
  }
  if (results.some((result) => result.metrics_json?.result_kind === "run_summary")) {
    return <FeatureTargetResults results={results} onDisposition={onDisposition} onPromote={onPromote} onCloseIssue={onCloseIssue} workflowFilter={workflowFilter} runId={runId}
      diagnosticFilter={diagnosticFilter} onDiagnosticFilter={selectDiagnosticFilter}
      workflowSummary={<FindingStatusCards results={results} filter={workflowFilter} onFilter={selectWorkflowFilter} compact />} />;
  }
  return <CrossFieldResults results={results} onDisposition={onDisposition} onPromote={onPromote}
    onCloseIssue={onCloseIssue} onRecompute={onRecompute} />;
}
