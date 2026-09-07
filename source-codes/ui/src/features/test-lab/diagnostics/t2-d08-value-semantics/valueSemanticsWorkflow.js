const COMPLETED_AI_DECISIONS = new Set([
  "MATCH",
  "MULTI_ROLE_MATCH",
  "NO_CANDIDATE_MATCH",
  "INSUFFICIENT_CONTEXT",
  "AMBIGUOUS_ROLE",
  "CANDIDATE_SET_INCOMPLETE",
]);

export function aiRoleOutput(field) {
  return field?.adjudication?.output || null;
}

export function aiSuggestedRoles(field) {
  const output = aiRoleOutput(field);
  if (!output || !["MATCH", "MULTI_ROLE_MATCH"].includes(output.decision)) return [];
  return [output.primary_role, ...(output.secondary_roles || [])].filter(Boolean);
}

export function hasCompletedAiRoleReview(field) {
  const output = aiRoleOutput(field);
  if (field?.adjudication?.status !== "proposal_ready"
    || !COMPLETED_AI_DECISIONS.has(output?.decision)
    || !String(output?.reason || "").trim()) return false;
  const roles = aiSuggestedRoles(field);
  if (output.decision === "MATCH") return roles.length === 1;
  if (output.decision === "MULTI_ROLE_MATCH") return roles.length > 1;
  if (output.decision === "CANDIDATE_SET_INCOMPLETE") {
    return (output.requested_expansion_roles || []).length > 0;
  }
  return roles.length === 0;
}

export function needsAiRoleReview(field) {
  return !isFieldNotApplicable(field) && Boolean(field?.review_required)
    && (field?.candidates?.length || 0) > 0
    && !hasCompletedAiRoleReview(field);
}

export function isFieldNotApplicable(field) {
  return field?.applicability?.status === "not_applicable";
}

export function canRequestAiRoleReview(field) {
  return Boolean(field?.review_required)
    && !isFieldNotApplicable(field)
    && !hasCompletedAiRoleReview(field);
}

export function aiRoleReviewSummary(field) {
  if (field?.adjudication?.status === "failed") {
    return {
      tone: "review",
      title: "AI review unavailable",
      recommendation: "Retry Ask AI for this field or the pending batch; no AI role was applied.",
      reason: `The provider did not return a validated review (${field.adjudication.error || "unavailable"}).`,
      roles: [],
      confirmable: false,
    };
  }
  if (!hasCompletedAiRoleReview(field)) return null;
  const output = aiRoleOutput(field);
  const roles = aiSuggestedRoles(field);
  if (output.decision === "MATCH" || output.decision === "MULTI_ROLE_MATCH") {
    return {
      tone: "match",
      title: output.decision === "MULTI_ROLE_MATCH" ? "AI suggests multiple roles" : "AI role suggestion",
      recommendation: `Confirm ${roles.join(" + ")} if it matches the field's business meaning.`,
      reason: output.reason,
      roles,
      confirmable: true,
    };
  }
  if (output.decision === "NO_CANDIDATE_MATCH") {
    return {
      tone: "no_match",
      title: "No applicable Value Semantics role",
      recommendation: "Confirm Not applicable with a reason if no governed role fits; otherwise review the roles manually.",
      reason: output.reason,
      roles: [],
      confirmable: false,
    };
  }
  if (output.decision === "INSUFFICIENT_CONTEXT") {
    return {
      tone: "review",
      title: "More business context is needed",
      recommendation: "Verify the data-dictionary description, then select a role manually or leave the field excluded.",
      reason: output.reason,
      roles: [],
      confirmable: false,
    };
  }
  if (output.decision === "AMBIGUOUS_ROLE") {
    return {
      tone: "review",
      title: "Role remains ambiguous",
      recommendation: "Review the candidate roles manually; AI could not select one defensibly.",
      reason: output.reason,
      roles: [],
      confirmable: false,
    };
  }
  return {
    tone: "review",
    title: "Broader Knowledge Base review needed",
    recommendation: `Do not confirm yet. Review requested catalog roles: ${(output.requested_expansion_roles || []).join(", ")}.`,
    reason: output.reason,
    roles: [],
    confirmable: false,
  };
}

export async function requestAiRoleReviewsSequentially(
  fields, requestReview, onProgress = () => {},
) {
  const pending = [...(fields || [])];
  let completed = 0;
  let unavailable = 0;
  for (let index = 0; index < pending.length; index += 1) {
    const field = pending[index];
    onProgress({ current: index + 1, total: pending.length, field: field.column });
    let updated;
    try {
      updated = await requestReview(field.column);
    } catch (error) {
      return {
        status: "stopped",
        completed,
        failedField: field.column,
        notAttempted: pending.length - index - 1,
        error,
      };
    }
    const updatedField = updated?.fields?.find((item) => item.column === field.column);
    if (hasCompletedAiRoleReview(updatedField)) completed += 1;
    else unavailable += 1;
  }
  return { status: "completed", total: pending.length, completed, unavailable };
}
