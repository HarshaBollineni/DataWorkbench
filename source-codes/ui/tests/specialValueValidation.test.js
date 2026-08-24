import assert from "node:assert/strict";
import test from "node:test";

import {
  parseSpecialValueInput, specialValueIssue, specialValueIssues, specialValuesFor,
} from "../src/lib/specialValueValidation.js";

test("serialized SQLite arrays are normalized without splitting their characters", () => {
  assert.deepEqual(specialValuesFor({ missing_value_codes_json: '["-999"]' }), ["-999"]);
});

test("comma-separated numeric codes are parsed as separate atomic values", () => {
  assert.deepEqual(parseSpecialValueInput("-999, -998"), ["-999", "-998"]);
  assert.deepEqual(parseSpecialValueInput("-999,"), ["-999"]);
});

test("numeric columns reject descriptive annotations and suggest the atomic code", () => {
  const issue = specialValueIssue({
    classification: "numerical",
    missing_value_codes_json: ["-999.0 = pre-existing source missing", "blank/NaN = missing"],
  });

  assert.equal(issue.code, "descriptive_special_value");
  assert.deepEqual(issue.suggestedValues, ["-999"]);
  assert.match(issue.message, /Blank and NaN values are already counted as null/);
});

test("numeric columns reject nonnumeric literals while categorical codes remain valid", () => {
  assert.equal(specialValueIssue({
    classification: "numerical", missing_value_codes_json: ["UNKNOWN"],
  }).code, "non_numeric_special_value");
  assert.equal(specialValueIssue({
    classification: "categorical", missing_value_codes_json: ["UNKNOWN"],
  }), null);
});

test("issue collection identifies the affected row for blocking navigation", () => {
  const issues = specialValueIssues([
    { column_name: "good", classification: "numerical", missing_value_codes_json: ["-1"] },
    { column_name: "bad", classification: "numerical", missing_value_codes_json: ["N/A"] },
  ]);
  assert.equal(issues.length, 1);
  assert.equal(issues[0].row.column_name, "bad");
  assert.equal(issues[0].index, 1);
});
