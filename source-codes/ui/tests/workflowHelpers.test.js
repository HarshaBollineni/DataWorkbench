import assert from "node:assert/strict";
import test from "node:test";

import {
  highConfidenceHeaderMapping, inferTaxonomyValue, periodDate,
} from "../src/pages/sourcing/workflowHelpers.js";

test("highConfidenceHeaderMapping keeps only confident source columns", () => {
  assert.deepEqual(highConfidenceHeaderMapping({ fields: [
    { name: "column_name", confidence: "high", source_column: "Variable" },
    { name: "logical_type", confidence: "medium", source_column: "Type" },
    { name: "role", confidence: "high", source_column: "" },
  ] }), { column_name: "Variable" });
  assert.deepEqual(highConfidenceHeaderMapping(null), {});
});

test("inferTaxonomyValue matches normalized labels, keys, and configured aliases", () => {
  const options = [
    { key: "credit_risk", label: "Credit Risk" },
    { key: "fraud", label: "Fraud" },
  ];
  assert.equal(inferTaxonomyValue(options, "Quarterly CREDIT-risk assessment"), "Credit Risk");
  assert.equal(inferTaxonomyValue(options, "Portfolio default monitoring", [["credit_risk", ["default monitoring"]]]), "Credit Risk");
  assert.equal(inferTaxonomyValue(options, "Unrelated context"), "");
});

test("periodDate expands quarter and year values to inclusive calendar bounds", () => {
  assert.equal(periodDate("2025 Q2"), "2025-04-01");
  assert.equal(periodDate("2025-Q2", true), "2025-06-30");
  assert.equal(periodDate("2024"), "2024-01-01");
  assert.equal(periodDate("2024", true), "2024-12-31");
  assert.equal(periodDate("not a period"), "");
});

test("periodDate can expand a parsed date to its quarter end", () => {
  assert.equal(periodDate("2025-02-10", true, true), "2025-03-31");
  assert.equal(periodDate("2025-02-10"), "2025-02-10");
});
