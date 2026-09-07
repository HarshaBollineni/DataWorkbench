import test from "node:test";
import assert from "node:assert/strict";

import {
  aiSuggestionDraft, bulkEligibleScope, candidateSelectionDraft, expectedBucket,
  governedExactDecisionState, groupExpected, groupObserved, hasUsableAiSuggestion, isBulkEligibleRole,
  initialRelationshipReviewOpen, needsAiSuggestion, quickAcceptanceDraft, readableConceptName, REFERENCE_DIRECTION_LABELS,
  reviewSuggestionSummary, scopeCandidates, suggestedScope,
  requestAiSuggestionsSequentially, visibleCandidates,
} from "../../../../../src/features/test-lab/diagnostics/t2-d11-directional-monotonic-consistency/directionalityWorkflow.js";

test("exact KB proposal eligibility compares only the governed decision tuple", () => {
  const governed = {
    canonical_feature: "loan_to_value",
    representation_orientation: "SAME",
    expected_direction: "INCREASING",
    kb_version: "0.3",
    rationale: "Governed wording is not part of tuple identity.",
  };
  assert.equal(governedExactDecisionState(governed, {
    canonical_feature: "loan_to_value",
    representation_orientation: "SAME",
    expected_direction: "INCREASING",
    rationale: "A different run rationale does not create a KB change.",
  }), "unchanged");
  for (const local of [
    { canonical_feature: "debt_to_income", representation_orientation: "SAME",
      expected_direction: "INCREASING" },
    { canonical_feature: "loan_to_value", representation_orientation: "INVERSE",
      expected_direction: "INCREASING" },
    { canonical_feature: "loan_to_value", representation_orientation: "SAME",
      expected_direction: "DECREASING" },
    { canonical_feature: null, representation_orientation: null,
      expected_direction: "NO_CLEAR_DIRECTION" },
  ]) assert.equal(governedExactDecisionState(governed, local), "changed");
});

test("missing or incomplete exact KB baselines remain proposal eligible", () => {
  const local = {
    canonical_feature: "loan_to_value",
    representation_orientation: "SAME",
    expected_direction: "INCREASING",
  };
  assert.equal(governedExactDecisionState(null, local), "unavailable");
  assert.equal(governedExactDecisionState({
    canonical_feature: "loan_to_value",
    representation_orientation: null,
    expected_direction: "INCREASING",
  }, local), "unavailable");
});

test("unclassified variables remain in needs review", () => {
  assert.equal(expectedBucket({ expected_direction: null }), "NEEDS_REVIEW");
  const grouped = groupExpected([{ feature: "a", expected_direction: null },
    { feature: "b", expected_direction: "INCREASING" }]);
  assert.deepEqual(grouped.NEEDS_REVIEW.map((row) => row.feature), ["a"]);
  assert.deepEqual(grouped.INCREASING.map((row) => row.feature), ["b"]);
  assert.equal(expectedBucket({ expected_direction: "DECREASING", review_required: true }), "NEEDS_REVIEW");
});

test("fuzzy candidates are disclosed progressively", () => {
  const candidates = Array.from({ length: 10 }, (_, index) => ({ canonical_feature: `c${index}` }));
  assert.equal(visibleCandidates({ candidates, candidate_display_limit: 3 }).length, 3);
  assert.equal(visibleCandidates({ candidates, candidate_display_limit: 6 }).length, 6);
});

test("selecting a KB concept loads its rationale and resets its representation", () => {
  assert.deepEqual(candidateSelectionDraft({
    canonical_feature: "loan_to_value",
    expected_direction: "increasing",
    representation_evidence: "normal",
    rationale: "Greater leverage reduces the collateral cushion.",
  }), {
    candidate: "loan_to_value",
    direction: "INCREASING",
    orientation: "SAME",
    rationale: "Greater leverage reduces the collateral cushion.",
  });
  assert.equal(candidateSelectionDraft({
    canonical_feature: "coverage",
    expected_direction: "DECREASING",
    representation_evidence: "inverse",
  }).direction, "INCREASING");
});

test("the persisted AI proposal can restore all editable decision fields", () => {
  assert.deepEqual(aiSuggestionDraft({
    classification_source: "LLM_ADJUDICATED_KB_V0_3",
    canonical_feature: "loan_to_value",
    expected_direction: "INCREASING",
    representation_orientation: "SAME",
    rationale: "KB rationale. AI match rationale.",
    adjudication: { result: { output: { decision: "MATCH" } } },
  }), {
    candidate: "loan_to_value",
    direction: "INCREASING",
    orientation: "SAME",
    rationale: "KB rationale. AI match rationale.",
  });
});

test("a complete AI match can be explicitly accepted without opening the editor", () => {
  const feature = {
    classification_source: "LLM_ADJUDICATED_KB_V0_3",
    canonical_feature: "loan_to_value",
    expected_direction: "INCREASING",
    representation_orientation: "SAME",
    rationale: "Greater leverage reduces collateral protection. AI match rationale: CLTV measures leverage.",
    candidates: [{ canonical_feature: "loan_to_value", expected_direction: "INCREASING" }],
    adjudication: { result: { output: {
      decision: "MATCH",
      selected_candidate: "loan_to_value",
      representation_orientation: "SAME",
      reason: "CLTV is a direct loan-to-value measure.",
    } } },
  };
  assert.deepEqual(quickAcceptanceDraft(feature), {
    expected_direction: "INCREASING",
    rationale: feature.rationale,
    canonical_feature: "loan_to_value",
    representation_orientation: "SAME",
    include_in_kb: false,
  });
  assert.deepEqual(reviewSuggestionSummary(feature), {
    label: "Suggested KB concept",
    concept: "Loan to value",
    conceptId: "loan_to_value",
    reasonLabel: "Why AI linked it",
    reason: "CLTV is a direct loan-to-value measure.",
  });
});

test("quick acceptance rejects ambiguous or stale AI matches", () => {
  const base = {
    classification_source: "LLM_ADJUDICATED_KB_V0_3",
    canonical_feature: "loan_to_value",
    expected_direction: "INCREASING",
    representation_orientation: "SAME",
    rationale: "A retained rationale.",
    candidates: [{ canonical_feature: "loan_to_value", expected_direction: "INCREASING" }],
    adjudication: { result: { output: {
      decision: "MATCH", selected_candidate: "loan_to_value",
      representation_orientation: "SAME", reason: "A bounded reason.",
    } } },
  };
  assert.equal(quickAcceptanceDraft({ ...base,
    representation_orientation: "UNDETERMINED",
    adjudication: { result: { output: { ...base.adjudication.result.output,
      representation_orientation: "UNDETERMINED" } } },
  }), null);
  assert.equal(quickAcceptanceDraft({ ...base, canonical_feature: "coverage" }), null);
  assert.equal(quickAcceptanceDraft({ ...base, expected_direction: "DECREASING" }), null);
  assert.equal(quickAcceptanceDraft({ ...base, rationale: "" }), null);
});

test("inverse AI matches derive and retain the flipped risk direction", () => {
  const feature = {
    classification_source: "LLM_ADJUDICATED_KB_V0_3",
    canonical_feature: "debt_service_coverage",
    expected_direction: "INCREASING",
    representation_orientation: "INVERSE",
    rationale: "A retained rationale.",
    candidates: [{ canonical_feature: "debt_service_coverage", expected_direction: "DECREASING" }],
    adjudication: { result: { output: {
      decision: "MATCH", selected_candidate: "debt_service_coverage",
      representation_orientation: "INVERSE", reason: "The representation is inverted.",
    } } },
  };
  assert.equal(quickAcceptanceDraft(feature).expected_direction, "INCREASING");
});

test("KB concept names are readable without changing their stored identifiers", () => {
  assert.equal(readableConceptName("probability_of_default"), "Probability of default");
  assert.equal(readableConceptName("debt_service_coverage"), "Debt service coverage");
  assert.equal(readableConceptName("pd_midpoint_pct"), "PD midpoint pct");
});

test("not-directional AI suggestions support an explicit one-click decision", () => {
  const feature = {
    classification_source: "LLM_ADJUDICATED_NOT_DIRECTIONAL",
    expected_direction: "NOT_APPLICABLE",
    rationale: "AI rationale: An identifier has no ordered economic direction.",
    adjudication: { result: { output: {
      decision: "NOT_DIRECTIONAL", selected_candidate: null,
      representation_orientation: null, reason: "This is an identifier.",
    } } },
  };
  assert.deepEqual(quickAcceptanceDraft(feature), {
    expected_direction: "NOT_APPLICABLE",
    rationale: feature.rationale,
    canonical_feature: null,
    representation_orientation: null,
    include_in_kb: false,
  });
  assert.equal(reviewSuggestionSummary(feature).concept, "Not directional");
});

test("collapsed summaries explain no-match and manual-review states", () => {
  assert.deepEqual(reviewSuggestionSummary({
    classification_source: "LLM_NO_KB_MATCH",
    adjudication: { result: { output: {
      decision: "NO_CANDIDATE_MATCH", reason: "None describes the feature.",
    } } },
  }), {
    label: "AI review result",
    concept: "No suitable KB concept",
    reasonLabel: "Why no match was proposed",
    reason: "None describes the feature.",
  });
  assert.equal(reviewSuggestionSummary({ candidates: [{}] }).reason,
    "Ask AI or review the possible KB concepts.");
});

test("bulk AI targets only pending variables without a usable AI result", () => {
  const base = { expected_direction: null, review_required: true, candidates: [{}] };
  assert.equal(needsAiSuggestion(base), true);
  for (const decision of ["NOT_DIRECTIONAL", "NO_CANDIDATE_MATCH", "INSUFFICIENT_CONTEXT"]) {
    const feature = { ...base, adjudication: { status: "succeeded", result: { output: {
      decision, reason: "A bounded rationale.", selected_candidate: null,
      representation_orientation: null,
    } } } };
    assert.equal(hasUsableAiSuggestion(feature), true);
    assert.equal(needsAiSuggestion(feature), false);
  }
  const match = { ...base, candidates: [{ canonical_feature: "loan_to_value" }],
    adjudication: { status: "unavailable", result: { output: {
      decision: "MATCH", reason: "A bounded rationale.",
      selected_candidate: "loan_to_value", representation_orientation: "SAME",
    } } } };
  assert.equal(hasUsableAiSuggestion(match), true);
  assert.equal(needsAiSuggestion(match), false);
  assert.equal(needsAiSuggestion({ ...base,
    adjudication: { status: "unavailable", result: null },
  }), true);
  assert.equal(needsAiSuggestion({ ...base, review_required: false,
    expected_direction: "INCREASING" }), false);
  assert.equal(needsAiSuggestion({ ...base, reused_decision: {
    source_run_id: "drun_prior", reused_from_completed_run: true,
  } }), false);
});

test("a rerun starts at the full setup workflow even when prior scope is retained", () => {
  const rerun = {
    scope_features: ["CURRENT_LTV"],
    ready_to_run: true,
    prior_run_reuse: { source_run_id: "drun_prior", reused_feature_count: 1 },
    features: [{ feature: "CURRENT_LTV", scope_selected: true }],
  };
  assert.equal(initialRelationshipReviewOpen(rerun), false);
  assert.equal(initialRelationshipReviewOpen({
    scope_features: ["CURRENT_LTV"],
    features: [{ feature: "CURRENT_LTV", scope_selected: true }],
  }), true);
});

test("bulk AI requests are sequential and count unavailable provider results", async () => {
  let active = 0;
  let maxActive = 0;
  const order = [];
  const progress = [];
  const outcome = await requestAiSuggestionsSequentially(
    [{ feature: "a" }, { feature: "b" }, { feature: "c" }],
    async (feature) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      order.push(feature);
      await new Promise((resolve) => setTimeout(resolve, 1));
      active -= 1;
      return { features: [{ feature, adjudication: {
        status: feature === "b" ? "unavailable" : "succeeded",
      } }] };
    },
    (value) => progress.push(value),
  );
  assert.equal(maxActive, 1);
  assert.deepEqual(order, ["a", "b", "c"]);
  assert.deepEqual(progress.map((item) => item.current), [1, 2, 3]);
  assert.deepEqual(outcome, {
    status: "completed", total: 3, added: 2, unavailable: 1,
  });
});

test("bulk AI stops after a thrown request so later manifest patches are not attempted", async () => {
  const calls = [];
  const outcome = await requestAiSuggestionsSequentially(
    [{ feature: "a" }, { feature: "b" }, { feature: "c" }],
    async (feature) => {
      calls.push(feature);
      if (feature === "b") throw new Error("stale manifest");
      return { features: [{ feature, adjudication: { status: "succeeded" } }] };
    },
  );
  assert.deepEqual(calls, ["a", "b"]);
  assert.equal(outcome.status, "stopped");
  assert.equal(outcome.completed, 1);
  assert.equal(outcome.failedFeature, "b");
  assert.equal(outcome.notAttempted, 1);
});

test("observed results group by immutable empirical classification", () => {
  const rows = [{ result_id: "1", metrics_json: { evidence: { observed_direction: "NON_MONOTONIC" } } },
    { result_id: "2", metrics_json: { evidence: { observed_direction: "INCREASING" } } }];
  const grouped = groupObserved(rows);
  assert.equal(grouped.NON_MONOTONIC[0].result_id, "1");
  assert.equal(grouped.INCREASING[0].result_id, "2");
});

test("reference and segmentation fields cannot also enter the analysis scope", () => {
  const rows = [
    { feature: "target", numeric: true, classification_source: "KB_V0_3_EXACT" },
    { feature: "segment", numeric: true, classification_source: "KB_V0_3_EXACT" },
    { feature: "ltv", numeric: true, classification_source: "KB_V0_3_EXACT" },
    { feature: "label", numeric: false, classification_source: "KB_V0_3_EXACT" },
  ];
  assert.deepEqual(scopeCandidates(rows, "target", "segment").map((row) => row.feature), ["ltv"]);
  assert.deepEqual(suggestedScope(rows, "target", "segment"), ["ltv"]);
});

test("reference contracts use risk language while preserving governed enum keys", () => {
  assert.equal(REFERENCE_DIRECTION_LABELS.HIGHER_IS_WORSE, "Higher value → Higher risk");
  assert.equal(REFERENCE_DIRECTION_LABELS.HIGHER_IS_BETTER, "Higher value → Lower risk");
});

test("bulk selection omits protected roles while manual scope remains available", () => {
  const roles = ["Feature", "Score", "Target", "Date", "Period", "Ignore", "Weight"];
  const rows = roles.map((role) => ({ feature: role.toLowerCase(), role, numeric: true }));
  assert.deepEqual(bulkEligibleScope(rows).map((row) => row.role), ["Feature", "Score"]);
  assert.equal(scopeCandidates(rows).length, roles.length);
  assert.equal(isBulkEligibleRole(" target "), false);
});
