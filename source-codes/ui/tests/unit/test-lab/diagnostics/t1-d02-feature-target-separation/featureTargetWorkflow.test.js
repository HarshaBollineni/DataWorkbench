import test from "node:test";
import assert from "node:assert/strict";

import {
  formatProfileNumber, primaryTreePerformance, profilePercentiles, promotionRecommendation,
} from "../../../../../src/features/test-lab/diagnostics/t1-d02-feature-target-separation/featureTargetWorkflow.js";

test("primary tree leaves produce deterministic ROC and cumulative lift rows", () => {
  const rows = primaryTreePerformance([
    { score: 0.8, rows: 20, target_events: 16 },
    { score: 0.2, rows: 80, target_events: 4 },
  ]);
  assert.deepEqual(rows[0], {
    threshold: null, rows: 0, events: 0, cumulative_rows: 0,
    cumulative_events: 0, population_share: 0, tpr: 0, fpr: 0,
    event_rate: null, cumulative_lift: null,
  });
  assert.equal(rows[1].threshold, 0.8);
  assert.equal(rows[1].tpr, 0.8);
  assert.equal(rows[1].fpr, 0.05);
  assert.equal(rows[1].cumulative_lift, 4);
  assert.equal(rows[2].tpr, 1);
  assert.equal(rows[2].fpr, 1);
  assert.equal(rows[2].cumulative_lift, 1);
});

test("equal leaf scores are combined into one ROC threshold", () => {
  const rows = primaryTreePerformance([
    { score: 0.7, rows: 10, target_events: 8 },
    { score: 0.7, rows: 10, target_events: 2 },
    { score: 0.1, rows: 20, target_events: 2 },
  ]);
  assert.equal(rows.length, 3);
  assert.equal(rows[1].rows, 20);
  assert.equal(rows[1].events, 10);
});

test("ROC rows are unavailable when the target has only one observed class", () => {
  assert.deepEqual(primaryTreePerformance([
    { score: 1, rows: 12, target_events: 12 },
  ]), []);
});

test("profile percentiles accept governed p01 keys and quartile fallbacks", () => {
  assert.deepEqual(profilePercentiles({
    percentiles: { p01: 1, p05: 5, p50: 50, p95: 95, p99: 99 },
    q1: 25, q3: 75,
  }), [["P1", 1], ["P5", 5], ["P25", 25], ["P50", 50],
    ["P75", 75], ["P95", 95], ["P99", 99]]);
});

test("promotion recommendation follows the persisted candidate type", () => {
  assert.equal(promotionRecommendation({ candidate: { candidate_type: "target_leakage" } }), "recommended");
  assert.equal(promotionRecommendation({ candidate: { candidate_type: "poor_discrimination" } }), "review");
  assert.equal(promotionRecommendation({}), "not_recommended");
});

test("profile values use at most two decimals and whole numbers above 100", () => {
  assert.equal(formatProfileNumber(2.3594), "2.36");
  assert.equal(formatProfileNumber(4), "4");
  assert.equal(formatProfileNumber(99.126), "99.13");
  assert.equal(formatProfileNumber(101.9).replaceAll(",", ""), "102");
  assert.equal(formatProfileNumber(null), "—");
});
