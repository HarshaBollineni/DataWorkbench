import assert from "node:assert/strict";
import test from "node:test";

import {
  restoreStagedStructureChoices, stagedStructureReviewComplete, stagedStructureSelections,
} from "../src/pages/sourcing/stagedStructureReview.js";

const precheck = { tables: [{
  table: "accounts",
  entities: [{ candidate_id: "entity" }],
  temporals: [{ candidate_id: "temporal" }],
  candidates: [{ candidate_id: "grain", quality: "exact" }],
}], draft: { stale: false } };

const complete = {
  accounts: {
    default_entity_candidate_id: "entity",
    default_temporal_candidate_id: "temporal",
    row_grain_candidate_id: "grain",
    expected_cadence: { action: "confirm", value: { unit: "quarter", step: 1 } },
  },
};

test("all four structure concepts remain incomplete until explicitly declared", () => {
  assert.equal(stagedStructureReviewComplete(precheck, {}), false);
  assert.equal(stagedStructureReviewComplete(precheck, {
    accounts: { default_entity_candidate_id: "entity", default_temporal_candidate_id: "temporal", row_grain_candidate_id: "grain" },
  }), false);
  assert.equal(stagedStructureReviewComplete(precheck, complete), true);
});

test("limited declarations require acknowledgements", () => {
  const limited = { accounts: {
    default_entity_candidate_id: null, entity_acknowledged: true,
    default_temporal_candidate_id: null, temporal_acknowledged: true,
    row_grain_candidate_id: null, row_grain_acknowledged: true,
    expected_cadence: { action: "mark_not_applicable", acknowledged: true },
  } };
  assert.equal(stagedStructureReviewComplete(precheck, limited), true);
  limited.accounts.row_grain_acknowledged = false;
  assert.equal(stagedStructureReviewComplete(precheck, limited), false);
});

test("stale drafts do not restore and payload retains holistic declarations", () => {
  assert.deepEqual(restoreStagedStructureChoices({ draft: { stale: true, selections: { tables: [{ table: "accounts" }] } } }), {});
  assert.deepEqual(stagedStructureSelections(precheck, complete), [{ table: "accounts", ...complete.accounts }]);
});
