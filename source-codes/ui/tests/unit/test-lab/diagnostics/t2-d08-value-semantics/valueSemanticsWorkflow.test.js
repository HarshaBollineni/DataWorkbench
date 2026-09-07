import test from "node:test";
import assert from "node:assert/strict";

import {
  aiRoleReviewSummary, aiSuggestedRoles, canRequestAiRoleReview, hasCompletedAiRoleReview,
  isFieldNotApplicable,
  needsAiRoleReview, requestAiRoleReviewsSequentially,
} from "../../../../../src/features/test-lab/diagnostics/t2-d08-value-semantics/valueSemanticsWorkflow.js";

const pendingField = {
  column: "CLTV",
  review_required: true,
  candidates: [{ role: "static_attributes" }],
  adjudication: { status: "not_requested", attempts: [] },
};

test("CLTV no-match is a visible completed review rather than a missing suggestion", () => {
  const field = {
    ...pendingField,
    adjudication: { status: "proposal_ready", output: {
      decision: "NO_CANDIDATE_MATCH",
      primary_role: null,
      secondary_roles: [],
      requested_expansion_roles: [],
      reason: "CLTV is a current loan-to-value measure and no supplied role covers it.",
    } },
  };
  assert.equal(hasCompletedAiRoleReview(field), true);
  assert.equal(needsAiRoleReview(field), false);
  assert.deepEqual(aiRoleReviewSummary(field), {
    tone: "no_match",
    title: "No applicable Value Semantics role",
    recommendation: "Confirm Not applicable with a reason if no governed role fits; otherwise review the roles manually.",
    reason: "CLTV is a current loan-to-value measure and no supplied role covers it.",
    roles: [],
    confirmable: false,
  });
});

test("matched and multi-role outputs provide explicit confirmable roles", () => {
  const matched = { ...pendingField, adjudication: { status: "proposal_ready", output: {
    decision: "MATCH", primary_role: "arrears_measure", secondary_roles: [],
    requested_expansion_roles: [], reason: "Current DPD is an arrears measure.",
  } } };
  assert.deepEqual(aiSuggestedRoles(matched), ["arrears_measure"]);
  assert.equal(aiRoleReviewSummary(matched).confirmable, true);

  const multi = { ...pendingField, adjudication: { status: "proposal_ready", output: {
    decision: "MULTI_ROLE_MATCH", primary_role: "drawn_balance",
    secondary_roles: ["exposure_at_default"], requested_expansion_roles: [],
    reason: "The field serves both measurement purposes.",
  } } };
  assert.deepEqual(aiSuggestedRoles(multi), ["drawn_balance", "exposure_at_default"]);
  assert.match(aiRoleReviewSummary(multi).recommendation, /drawn_balance \+ exposure_at_default/);
});

test("batch eligibility includes only unresolved fields with candidates", () => {
  assert.equal(needsAiRoleReview(pendingField), true);
  assert.equal(needsAiRoleReview({ ...pendingField, review_required: false }), false);
  assert.equal(needsAiRoleReview({ ...pendingField, candidates: [] }), false);
  assert.equal(needsAiRoleReview({ ...pendingField, adjudication: {
    status: "proposal_ready", output: {
      decision: "AMBIGUOUS_ROLE", primary_role: null, secondary_roles: [],
      requested_expansion_roles: [], reason: "Two meanings remain plausible.",
    },
  } }), false);
});

test("provider failures remain visible and eligible for retry", () => {
  const field = { ...pendingField, adjudication: { status: "failed", error: "RuntimeError" } };
  assert.equal(needsAiRoleReview(field), true);
  assert.deepEqual(aiRoleReviewSummary(field), {
    tone: "review",
    title: "AI review unavailable",
    recommendation: "Retry Ask AI for this field or the pending batch; no AI role was applied.",
    reason: "The provider did not return a validated review (RuntimeError).",
    roles: [],
    confirmable: false,
  });
});

test("batch review processes fields sequentially and counts no-match as completed", async () => {
  const fields = [pendingField, { ...pendingField, column: "STATUS_CD" }];
  const progress = [];
  const calls = [];
  const outcome = await requestAiRoleReviewsSequentially(fields, async (column) => {
    calls.push(column);
    return { fields: [{ ...pendingField, column, adjudication: {
      status: "proposal_ready", output: {
        decision: "NO_CANDIDATE_MATCH", primary_role: null, secondary_roles: [],
        requested_expansion_roles: [], reason: "No governed role applies.",
      },
    } }] };
  }, (value) => progress.push(value));
  assert.deepEqual(calls, ["CLTV", "STATUS_CD"]);
  assert.deepEqual(progress.map((value) => value.field), ["CLTV", "STATUS_CD"]);
  assert.deepEqual(outcome, { status: "completed", total: 2, completed: 2, unavailable: 0 });
});

test("only an explicit field applicability decision means not applicable", () => {
  assert.equal(isFieldNotApplicable({ ...pendingField, selected: false }), false);
  assert.equal(isFieldNotApplicable({ ...pendingField, binding_source: "ai_no_applicable_role" }), false);
  const confirmed = { ...pendingField, applicability: { status: "not_applicable", reason: "No role fits" } };
  assert.equal(isFieldNotApplicable(confirmed), true);
  assert.equal(needsAiRoleReview(confirmed), false);
});

test("AI review is available only while role confirmation is required", () => {
  assert.equal(canRequestAiRoleReview(pendingField), true);
  assert.equal(canRequestAiRoleReview({
    ...pendingField, review_required: false, confirmed_roles: ["arrears_measure"],
    binding_source: "deterministic_exact",
  }), false);
  assert.equal(canRequestAiRoleReview({
    ...pendingField, review_required: false, confirmed_roles: ["static_attributes"],
    binding_source: "human_confirmed",
  }), false);
  assert.equal(canRequestAiRoleReview({
    ...pendingField, review_required: false,
    applicability: { status: "not_applicable", reason: "No governed role applies." },
  }), false);
  assert.equal(canRequestAiRoleReview({
    ...pendingField, review_required: true, confirmed_roles: [], binding_source: "human_reopened",
  }), true);
  assert.equal(canRequestAiRoleReview({
    ...pendingField, adjudication: { status: "proposal_ready", output: {
      decision: "NO_CANDIDATE_MATCH", primary_role: null, secondary_roles: [],
      requested_expansion_roles: [], reason: "A prior validated review found no applicable role.",
    } },
  }), false);
});

test("batch review stops cleanly and reports unattempted fields", async () => {
  const fields = [pendingField, { ...pendingField, column: "STATUS_CD" }];
  const outcome = await requestAiRoleReviewsSequentially(fields, async () => {
    throw new Error("provider unavailable");
  });
  assert.equal(outcome.status, "stopped");
  assert.equal(outcome.failedField, "CLTV");
  assert.equal(outcome.notAttempted, 1);
});
