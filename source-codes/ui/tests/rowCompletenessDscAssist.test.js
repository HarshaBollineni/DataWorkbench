import test from "node:test";
import assert from "node:assert/strict";

import { roleSourceLabel, rowCompletenessValidation } from "../src/features/test-lab/diagnostics/t2-d06-row-completeness/rowCompletenessWorkflow.js";

const manifest = {
  table: "applications", roles: { facility_id: { column: "facility_id" }, period: { column: "period" } },
  configuration: { reporting_grain: { value: "monthly" }, continuity_floor: { value: 0.95 } },
};

test("DSC assist is reviewed through the normal D06 scope without a separate acknowledgement", () => {
  assert.deepEqual(rowCompletenessValidation({ ...manifest, dsc_assist: { state: "available", scope_confirmed: false } }), []);
  assert.deepEqual(rowCompletenessValidation({ ...manifest, dsc_assist: { state: "unavailable" } }), []);
  assert.equal(roleSourceLabel({ source: "dsc_assist" }), "Confirmed Dataset Structure suggestion");
});
