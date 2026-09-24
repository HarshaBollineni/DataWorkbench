import test from "node:test";
import assert from "node:assert/strict";
import { presentationMetrics, metricValue, metricChange } from "../src/features/rca/resultPresentation.js";

test("metric presentation is independent of diagnostic and column names", () => {
  for (const [label, unit, value] of [["PSI", "number", .2], ["Outliers", "ratio", .02], ["Duplicates", "count", 17], ["Relationship", "correlation", -.5]]) {
    assert.equal(presentationMetrics({ version: 1, metrics: [{ label, unit, value }] }).length, 1);
  }
  assert.equal(metricValue(.02, "ratio"), "2.00%");
  assert.equal(metricChange({ value: .09, unit: "ratio", comparison: { value: .05 } }), "+4.00 percentage points");
});
test("legacy and invalid metrics do not invent cards", () => {
  assert.deepEqual(presentationMetrics(undefined), []);
  for (const value of [null, "0.1", NaN, Infinity, -1, 2]) {
    assert.deepEqual(presentationMetrics({ version: 1, metrics: [{ label: "Rate", unit: "ratio", value }] }), []);
  }
});
