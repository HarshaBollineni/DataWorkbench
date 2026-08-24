import test from "node:test";
import assert from "node:assert/strict";

import { findingWorkflowState, matchesFindingFilter } from "../src/pages/testlab/findingWorkflowState.js";

test("issue lifecycle takes precedence over an unchanged product recommendation", () => {
  const promoted = { review_state: "open", existing_issue: { issue_row_id: "iss_1", status: "Open" } };
  const closed = { review_state: "confirmed", existing_issue: { issue_row_id: "iss_2", status: "Closed" } };
  assert.equal(findingWorkflowState(promoted), "promoted");
  assert.equal(findingWorkflowState(closed), "closed");
});

test("finding workflow filters separate pending, promoted, and closed findings", () => {
  const row = { findings: [
    { finding_id: "f1", review_state: "open" },
    { finding_id: "f2", review_state: "confirmed", existing_issue: { issue_row_id: "iss_2", status: "Open" } },
  ] };
  assert.equal(matchesFindingFilter(row, "review_needed"), true);
  assert.equal(matchesFindingFilter(row, "promoted"), true);
  assert.equal(matchesFindingFilter(row, "closed"), false);
});
