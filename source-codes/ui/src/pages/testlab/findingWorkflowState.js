export function findingWorkflowState(finding) {
  if (!finding) return "other";
  if (finding.existing_issue?.status === "Closed") return "closed";
  if (finding.existing_issue || finding.review_state === "confirmed") return "promoted";
  if (finding.review_state === "open") return "review_needed";
  if (finding.review_state === "dismissed") return "dismissed";
  return "other";
}

export function matchesFindingFilter(row, filter) {
  if (filter === "all") return true;
  return (row.findings || []).some((finding) => findingWorkflowState(finding) === filter);
}
