import test from "node:test";
import assert from "node:assert/strict";
import { intakePresentation } from "../src/features/rca/intakePresentation.js";

test("PSI uses only retained metric and declared boundary, never profile values", () => {
  const issue = { metric: .3, source_evidence: { diagnostic_id: 14,
    metrics: { psi: .3, classification: "investigate", thresholds: { investigate: .25 } },
    data_profile: { mean: 999, null_count: 300 }, population_context: { preview: { baseline_count: 100, current_count: 80, excluded_count: 0 } } } };
  const before = structuredClone(issue);
  const view = intakePresentation(issue);
  assert.equal(view.status, "Investigation recommended");
  assert.deepEqual(view.facts, [["Population stability index", .3], ["Investigate boundary", .25]]);
  assert.ok(view.scope.some(([label, value]) => label === "Excluded records" && value === 0));
  assert.deepEqual(issue, before);
});

test("completeness and rule exceptions retain measures without inventing units or comparator", () => {
  for (const id of [6, 4, 8, 99]) {
    const view = intakePresentation({ source_evidence: { diagnostic_id: id, finding: {
      rule_text: "The declared rule was not met.", outcome: "FAIL", rate: 0, tolerance: .95, violation_count: 10,
    } } });
    assert.equal(view.summary, "The declared rule was not met.");
    assert.deepEqual(view.facts, [["Observed measure", 0], ["Declared threshold", .95], ["Reported exceptions", 10]]);
  }
});

test("contextual directionality and unevaluable results are not reported as failed", () => {
  const view = intakePresentation({ status: "Open", source_evidence: { diagnostic_id: 11, decision_type: "contextual", finding: { outcome: "CONTEXTUAL" } } });
  assert.equal(view.status, "Review required");
  assert.deepEqual(view.facts, []);
  const unavailable = intakePresentation({ metric: .8, source_evidence: { verdict: "FAIL", na_reason: "Insufficient paired observations" } });
  assert.equal(unavailable.status, "Could not evaluate");
  assert.deepEqual(unavailable.facts, []);
  assert.ok(unavailable.limitations.includes("Insufficient paired observations"));
});

test("legacy, nonfinite, proposed special-value and limitation payloads disclose gaps", () => {
  assert.equal(intakePresentation({ status: "Failed" }).status, "Outcome not specified");
  assert.ok(intakePresentation({}).limitations.some((text) => text.includes("not retained")));
  const view = intakePresentation({ metric: NaN, source_evidence: { metrics: { limitations: ["Sample only"], structured_result: { limitations: ["Sample only", "No lineage"] } },
    data_profile: { special_values: [-999], special_values_confirmed: false } } });
  assert.deepEqual(view.facts, []);
  assert.equal(view.limitations.filter((text) => text === "Sample only").length, 1);
  assert.ok(view.limitations.some((text) => text.includes("unconfirmed")));
});
