import assert from "node:assert/strict";
import test from "node:test";

import {
  binningClassification, hasBinningResult, hasBinningWork, hasRepositoryMatch, reviewBinCounts,
} from "../src/pages/testlab/psiWorkflow.js";

test("repository matches count as binning work before a draft exists", () => {
  const rows = [{ bin_route: { evidence: { exact_fine: [{ artifact_id: "fine-1" }] } } }];
  assert.equal(hasRepositoryMatch(rows), true);
  assert.equal(hasBinningWork({ frozen_bins: {}, bin_drafts: {} }, rows), true);
});

test("rejected evidence is classified but does not trigger the source-change work warning", () => {
  const route = { workflow: "generate_target_free", evidence: { rejected: [{ artifact_id: "bad-1" }] } };
  assert.equal(binningClassification(route, "repository"), "Invalid or ineligible repository artifact");
  assert.equal(hasRepositoryMatch([{ bin_route: route }]), false);
});

test("review counts distinguish fine and coarse definitions", () => {
  assert.deepEqual(reviewBinCounts({
    payload: { bins: [{}, {}, {}] },
    fine_payload: { bins: [{}, {}, {}, {}, {}] },
  }), { fine: 5, coarse: 3 });
});

test("artifact metadata supplies counts before the review is expanded", () => {
  assert.deepEqual(reviewBinCounts(null, { fine_bin_count: 12, coarse_bin_count: 5 }), {
    fine: 12, coarse: 5,
  });
});

test("rejected repository history is not presented as a usable matching result", () => {
  assert.equal(hasBinningResult({ evidence: { rejected: [{ artifact_id: "bad-1" }] } }), false);
  assert.equal(hasBinningResult({ evidence: {}, artifact: null }), false);
});
