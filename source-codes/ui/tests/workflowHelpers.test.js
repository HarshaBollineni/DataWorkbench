import assert from "node:assert/strict";
import test from "node:test";

import {
  highConfidenceHeaderMapping, inferTaxonomyValue, periodDate, targetProfileFacts,
  temporalProfileEvidence,
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

test("temporalProfileEvidence requires profiled values rather than temporal-looking names", () => {
  assert.equal(temporalProfileEvidence({
    column_name: "Num.Cash.Advances.3.Month", role: "Period",
    profile_json: { regular_value_count: 100, min: 0, max: 12 },
  }), null);
  assert.deepEqual(temporalProfileEvidence({
    column_name: "qt", role: "Period", profile_json: {
      regular_value_count: 2,
      period_format_evidence_available: true,
      period_format_checked_regular_count: 2,
      period_format_failure_count: 0,
      period_bounds: { start_date: "2006-01-01", end_date: "2006-06-30" },
    },
  }), { kind: "period", startDate: "2006-01-01", endDate: "2006-06-30" });
});

test("targetProfileFacts uses agreed binary counts/rate and continuous range statistics", () => {
  assert.deepEqual(targetProfileFacts({ inferred_type: "categorical", distinct_count: 2, profile_json: {
    top_values: { 0: 90, 1: 10 }, regular_value_count: 100,
  } }), [["0 count", "90"], ["1 count", "10"], ["1 rate", "10.0%"]]);
  assert.deepEqual(targetProfileFacts({ inferred_type: "binary", distinct_count: 2, profile_json: {
    min: 0, max: 1, zero_count: 4601, finite_value_count: 4816, regular_value_count: 4816,
  } }), [["0 count", "4,601"], ["1 count", "215"], ["1 rate", "4.5%"]]);
  assert.deepEqual(targetProfileFacts({ inferred_type: "numerical", distinct_count: 20, profile_json: {
    min: 1, max: 99, mean: 42, percentiles: { p05: 5, p95: 95 },
  } }), [["Range", "1 → 99"], ["P5–P95", "5 → 95"], ["Mean", "42"]]);
});
