import assert from "node:assert/strict";
import test from "node:test";

import {
  diagnosticPackageChanges, validateDiagnosticPackageEditor,
} from "../src/pages/knowledge-base/diagnosticPackageEditor.js";

function packageValue() {
  return {
    change_summary: "Clarify guidance.", methodology: "Observed-span methodology.",
    diagnostic: {
      name: "Row completeness", what_it_computes: "Checks required rows.",
      metric: "Received divided by required.", threshold_rule_default: "Below 95% is a gap.",
    },
    configuration: {
      default_reporting_grain: "monthly", default_continuity_floor: 0.95,
      continuity_floor_label: "Required row coverage",
      continuity_floor_help: "Minimum required share.",
    },
    rules: Array.from({ length: 6 }, (_, index) => ({
      rule_id: `T2D6-0${index + 1}`, title: `Rule ${index + 1}`,
      user_help: "Plain-language guidance.", severity: index === 0 ? "CRITICAL" : "MATERIAL",
      next_step: "Review the source records.",
    })),
  };
}

test("live diagnostic-package validation accepts the supported guided form", () => {
  assert.deepEqual(validateDiagnosticPackageEditor(packageValue()), {});
});

test("live validation identifies exact invalid fields before draft submission", () => {
  const value = packageValue();
  value.change_summary = "";
  value.configuration.default_continuity_floor = 1.2;
  value.rules[2].severity = "LOW";
  assert.deepEqual(validateDiagnosticPackageEditor(value), {
    change_summary: "Change summary is required.",
    "configuration.default_continuity_floor": "Enter a number from 0 through 1.",
    "rules.T2D6-03.severity": "Severity must be CRITICAL or MATERIAL.",
  });
});

test("live diff includes only supported values changed by the editor", () => {
  const baseline = packageValue();
  const edited = structuredClone(baseline);
  edited.change_summary = "A different audit summary";
  edited.diagnostic.name = "Row completeness reconciliation";
  edited.rules[4].next_step = "Investigate missing facility periods.";
  assert.deepEqual(diagnosticPackageChanges(baseline, edited).map((item) => item.path), [
    "diagnostic.name", "rules.T2D6-05.next_step",
  ]);
});
