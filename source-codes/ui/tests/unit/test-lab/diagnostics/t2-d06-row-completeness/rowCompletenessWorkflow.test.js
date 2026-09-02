import assert from "node:assert/strict";
import test from "node:test";

import {
  FLOOR_RULES, roleSourceLabel, rowCompletenessReviewCount,
  rowCompletenessValidation, tableOptionSummary,
} from "../../../../../src/features/test-lab/diagnostics/t2-d06-row-completeness/rowCompletenessWorkflow.js";

function manifest(overrides = {}) {
  return {
    table: "portfolio",
    roles: {
      facility_id: { column: "facility_id", source: "governed_metadata" },
      period: { column: "reporting_period", source: "deterministic" },
      segment: null,
    },
    configuration: {
      reporting_grain: { value: "monthly" },
      continuity_floor: { value: 0.95 },
    },
    ...overrides,
  };
}

test("row completeness setup accepts required roles with no optional segment", () => {
  assert.deepEqual(rowCompletenessValidation(manifest()), []);
});

test("row completeness setup reports missing, duplicate, and invalid configuration", () => {
  const value = manifest({
    roles: {
      facility_id: { column: "same" }, period: { column: "same" }, segment: null,
    },
    configuration: {
      reporting_grain: { value: "weekly" }, continuity_floor: { value: 1.2 },
    },
  });
  assert.deepEqual(rowCompletenessValidation(value), [
    "Each semantic role must use a different column.",
    "Select a supported reporting grain.",
    "Continuity floor must be between 0% and 100%.",
  ]);
});

test("only the three coverage rules use the single continuity floor", () => {
  assert.deepEqual([...FLOOR_RULES], ["T2D6-03", "T2D6-05", "T2D6-06"]);
});

test("only open violation findings count as requiring human review", () => {
  assert.equal(rowCompletenessReviewCount([
    { outcome: "VIOLATION", review_state: "open" },
    { outcome: "VIOLATION", review_state: "confirmed", existing_issue: {} },
    { outcome: "VIOLATION", review_state: "dismissed" },
    { outcome: "PASS", review_state: "not_required" },
  ]), 1);
});

test("metadata labels and table summaries remain user-facing", () => {
  assert.equal(roleSourceLabel({ source: "governed_metadata" }), "Governed metadata");
  assert.equal(roleSourceLabel(null), "Not selected");
  assert.equal(tableOptionSummary({ facility_candidate: true, period_candidate: true,
    segment_candidate: false }), "facility candidate · period candidate");
  assert.equal(tableOptionSummary({}), "manual role selection required");
});
