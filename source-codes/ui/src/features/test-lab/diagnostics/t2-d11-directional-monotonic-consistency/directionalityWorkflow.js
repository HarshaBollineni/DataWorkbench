export const EXPECTED_COLUMNS = [
  ["NEEDS_REVIEW", "Needs review"],
  ["INCREASING", "Increasing"],
  ["DECREASING", "Decreasing"],
  ["NON_MONOTONIC", "Non-monotonic"],
  ["NO_CLEAR_DIRECTION", "No clear direction"],
  ["NOT_APPLICABLE", "Not applicable"],
  ["EXCLUDED", "Excluded"],
];

export const OBSERVED_COLUMNS = [
  ["INCREASING", "Increasing"],
  ["DECREASING", "Decreasing"],
  ["NON_MONOTONIC", "Non-monotonic"],
  ["WEAK_OR_NO_RELATIONSHIP", "Weak / no relationship"],
  ["CONFLICTING_EVIDENCE", "Conflicting evidence"],
  ["INSUFFICIENT_DATA", "Insufficient data"],
  ["NOT_APPLICABLE", "Not applicable"],
];

export const REFERENCE_DIRECTION_LABELS = {
  HIGHER_IS_WORSE: "Higher value → Higher risk",
  HIGHER_IS_BETTER: "Higher value → Lower risk",
};

export const BULK_EXCLUDED_ROLES = new Set(["target", "date", "period", "ignore", "weight"]);

const GOVERNED_EXACT_DECISION_KEYS = [
  "canonical_feature", "representation_orientation", "expected_direction",
];

export function governedExactDecisionState(governedDecision, localDecision) {
  const completeBaseline = governedDecision && GOVERNED_EXACT_DECISION_KEYS.every(
    (key) => typeof governedDecision[key] === "string" && governedDecision[key].trim(),
  );
  const completeLocalTuple = localDecision && GOVERNED_EXACT_DECISION_KEYS.every(
    (key) => Object.hasOwn(localDecision, key),
  );
  if (!completeBaseline || !completeLocalTuple) return "unavailable";
  const normalized = (value) => typeof value === "string" ? value.trim() : value;
  return GOVERNED_EXACT_DECISION_KEYS.every(
    (key) => normalized(governedDecision[key]) === normalized(localDecision[key]),
  ) ? "unchanged" : "changed";
}

export function expectedBucket(feature) {
  return feature.review_required || !feature.expected_direction
    ? "NEEDS_REVIEW" : feature.expected_direction;
}

export function groupExpected(features = []) {
  return Object.fromEntries(EXPECTED_COLUMNS.map(([key]) => [
    key, features.filter((feature) => expectedBucket(feature) === key),
  ]));
}

export function groupObserved(results = []) {
  return Object.fromEntries(OBSERVED_COLUMNS.map(([key]) => [key, results.filter(
    (row) => row.metrics_json?.evidence?.observed_direction === key,
  )]));
}

export function visibleCandidates(feature) {
  return (feature.candidates || []).slice(0, feature.candidate_display_limit || 3);
}

export function candidateSelectionDraft(candidate) {
  const orientation = candidate?.representation_evidence === "inverse" ? "INVERSE" : "SAME";
  let direction = String(candidate?.expected_direction || "NO_CLEAR_DIRECTION").toUpperCase();
  if (orientation === "INVERSE") {
    direction = direction === "INCREASING" ? "DECREASING"
      : direction === "DECREASING" ? "INCREASING" : direction;
  }
  return {
    candidate: candidate?.canonical_feature || "",
    direction,
    orientation,
    rationale: candidate?.rationale || "",
  };
}

export function aiSuggestionDraft(feature) {
  if (!feature?.classification_source?.startsWith("LLM_")
      || !feature?.adjudication?.result?.output) return null;
  const candidate = feature.canonical_feature || "";
  return {
    candidate,
    direction: feature.expected_direction || "NO_CLEAR_DIRECTION",
    orientation: candidate ? feature.representation_orientation || "SAME" : "SAME",
    rationale: feature.rationale || "",
  };
}

export function quickAcceptanceDraft(feature) {
  const output = feature?.adjudication?.result?.output;
  if (!feature?.classification_source?.startsWith("LLM_")
      || !String(feature?.rationale || "").trim()
      || !String(output?.reason || "").trim()) return null;

  if (output.decision === "NOT_DIRECTIONAL") {
    if (feature.expected_direction !== "NOT_APPLICABLE") return null;
    return {
      expected_direction: "NOT_APPLICABLE",
      rationale: feature.rationale,
      canonical_feature: null,
      representation_orientation: null,
      include_in_kb: false,
    };
  }

  if (output.decision !== "MATCH"
      || !["SAME", "INVERSE"].includes(output.representation_orientation)
      || feature.representation_orientation !== output.representation_orientation
      || feature.canonical_feature !== output.selected_candidate
      || !feature.expected_direction
      || ["NOT_APPLICABLE", "EXCLUDED"].includes(feature.expected_direction)) return null;

  const candidate = (feature.candidates || []).find(
    (item) => item.canonical_feature === output.selected_candidate,
  );
  if (!candidate) return null;
  const derived = candidateSelectionDraft({
    ...candidate,
    representation_evidence: output.representation_orientation === "INVERSE"
      ? "inverse" : "normal",
  });
  if (derived.direction !== feature.expected_direction) return null;

  return {
    expected_direction: feature.expected_direction,
    rationale: feature.rationale,
    canonical_feature: feature.canonical_feature,
    representation_orientation: feature.representation_orientation,
    include_in_kb: false,
  };
}

export function readableConceptName(value) {
  const acronyms = new Set(["cltv", "dpd", "dscr", "dti", "ead", "ifrs", "lgd", "ltv", "noi", "pd"]);
  const words = String(value || "").trim().split(/[_\-\s]+/).filter(Boolean);
  return words.map((word, index) => {
    const normalized = word.toLowerCase();
    if (acronyms.has(normalized)) return normalized.toUpperCase();
    return index === 0 ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : normalized;
  }).join(" ");
}

export function reviewSuggestionSummary(feature) {
  const output = feature?.classification_source?.startsWith("LLM_")
    ? feature?.adjudication?.result?.output : null;
  if (output?.decision === "MATCH") {
    return {
      label: "Suggested KB concept",
      concept: readableConceptName(output.selected_candidate),
      conceptId: output.selected_candidate,
      reasonLabel: "Why AI linked it",
      reason: output.reason,
    };
  }
  if (output?.decision === "NOT_DIRECTIONAL") {
    return {
      label: "Suggested classification",
      concept: "Not directional",
      reasonLabel: "Why AI suggested it",
      reason: output.reason,
    };
  }
  if (output?.decision === "NO_CANDIDATE_MATCH") {
    return {
      label: "AI review result",
      concept: "No suitable KB concept",
      reasonLabel: "Why no match was proposed",
      reason: output.reason,
    };
  }
  if (output?.decision === "INSUFFICIENT_CONTEXT") {
    return {
      label: "AI review result",
      concept: "More context needed",
      reasonLabel: "What is missing",
      reason: output.reason,
    };
  }
  if (feature?.classification_source === "KB_V0_3_EXACT") {
    const candidate = (feature.candidates || []).find(
      (item) => item.canonical_feature === feature.canonical_feature,
    );
    return {
      label: "Matched KB concept",
      concept: readableConceptName(feature.canonical_feature),
      conceptId: feature.canonical_feature,
      reasonLabel: "Knowledge Base rationale",
      reason: candidate?.rationale || feature.rationale || "Exact name or saved representation match in KB v0.3.",
    };
  }
  if (feature?.classification_source === "USER_CONFIRMED") {
    return {
      label: feature.canonical_feature ? "Selected KB concept" : "Confirmed classification",
      concept: feature.canonical_feature ? readableConceptName(feature.canonical_feature) : "Manual decision",
      conceptId: feature.canonical_feature || null,
      reasonLabel: "Decision rationale",
      reason: feature.rationale || "Confirmed by the user.",
    };
  }
  if (feature?.classification_source === "SYSTEM_TYPE_GATE") {
    return {
      label: "Analysis classification",
      concept: "Not applicable",
      reasonLabel: "Why it is not analysed",
      reason: feature.rationale || "This variable does not have a meaningful ordered numeric direction.",
    };
  }
  return {
    label: "KB concept",
    concept: "No suggestion yet",
    reasonLabel: "Next action",
    reason: (feature?.candidates?.length || 0) > 0
      ? "Ask AI or review the possible KB concepts."
      : "Review the variable and classify its expected risk direction manually.",
  };
}

export function hasUsableAiSuggestion(feature) {
  const output = feature?.adjudication?.result?.output;
  const decisions = new Set([
    "MATCH", "NOT_DIRECTIONAL", "NO_CANDIDATE_MATCH", "INSUFFICIENT_CONTEXT",
  ]);
  if (!decisions.has(output?.decision) || !String(output?.reason || "").trim()) return false;
  if (output.decision !== "MATCH") return true;
  const candidateNames = new Set((feature?.candidates || []).map(
    (candidate) => candidate.canonical_feature,
  ));
  return candidateNames.has(output.selected_candidate)
    && ["SAME", "INVERSE", "UNDETERMINED"].includes(output.representation_orientation);
}

export function needsAiSuggestion(feature) {
  return !feature?.reused_decision?.reused_from_completed_run
    && expectedBucket(feature) === "NEEDS_REVIEW"
    && (feature?.candidates?.length || 0) > 0
    && !hasUsableAiSuggestion(feature);
}

export function initialRelationshipReviewOpen(manifest = {}) {
  if (manifest?.prior_run_reuse?.source_run_id) return false;
  const scoped = manifest.scope_features || (manifest.features || [])
    .filter((feature) => feature.scope_selected)
    .map((feature) => feature.feature);
  return scoped.length > 0;
}

export async function requestAiSuggestionsSequentially(
  features, requestSuggestion, onProgress = () => {},
) {
  const pending = [...(features || [])];
  let unavailable = 0;
  for (let index = 0; index < pending.length; index += 1) {
    const feature = pending[index];
    onProgress({ current: index + 1, total: pending.length, feature: feature.feature });
    let updated;
    try {
      updated = await requestSuggestion(feature.feature);
    } catch (error) {
      return {
        status: "stopped", completed: index, failedFeature: feature.feature,
        notAttempted: pending.length - index - 1, error,
      };
    }
    const updatedFeature = updated?.features?.find((item) => item.feature === feature.feature);
    if (!updatedFeature || updatedFeature.adjudication?.status === "unavailable") unavailable += 1;
  }
  return {
    status: "completed", total: pending.length,
    added: pending.length - unavailable, unavailable,
  };
}

export function scopeCandidates(features = [], referenceColumn = null, segmentColumn = null) {
  return features.filter((feature) => feature.numeric
    && feature.feature !== referenceColumn && feature.feature !== segmentColumn);
}

export function isBulkEligibleRole(role) {
  return !BULK_EXCLUDED_ROLES.has(String(role || "").trim().toLowerCase());
}

export function bulkEligibleScope(features = [], referenceColumn = null, segmentColumn = null) {
  return scopeCandidates(features, referenceColumn, segmentColumn)
    .filter((feature) => isBulkEligibleRole(feature.role));
}

export function suggestedScope(features = [], referenceColumn = null, segmentColumn = null) {
  return bulkEligibleScope(features, referenceColumn, segmentColumn)
    .filter((feature) => feature.classification_source === "KB_V0_3_EXACT")
    .map((feature) => feature.feature);
}

export function fmt(value, digits = 3) {
  return value == null || Number.isNaN(Number(value)) ? "—" : Number(value).toFixed(digits);
}
