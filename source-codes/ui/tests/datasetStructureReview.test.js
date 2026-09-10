import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_CANDIDATES_PER_FACET, STRUCTURE_COPY, boundedCandidates, candidateEvidenceSummary,
  createAutosaveCoordinator, createMaterializationPoller, draftPayload, facetCandidates,
  canConfirmStructure, createDecisionCoordinator, decisionPayload, initialStructureSelections, mergeCandidatePage, reviewNeedsReconfirmation, selectionMessage,
} from "../src/pages/sourcing/datasetStructureReview.js";
import { metadataCorrectionParams, metadataCorrectionTarget, structureReviewParams } from "../src/pages/sourcing/metadataCorrectionReturn.js";

// Mirrors the public Slice-2 DTO: no assertion pins, hashes, source data, or
// backend-only candidate fields are needed by the UI compatibility boundary.
const review = {
  review_contract_version: "1",
  structure_review_state: "review_required",
  materialization: { job_id: "dscj_safe", status: "succeeded", generation: 3, freshness: "current", retry_after_ms: null },
  draft: { draft_id: "dscd_safe", revision: 7, evidence_fingerprint: "evidence-1", editable: true, selections: { tables: [] } },
  tables: [{
    table: "applications", state: "review_required",
    requirements: { entity: true, temporal: true, row_grain: true },
    candidates: {
      entities: [{ candidate_id: "entity-2", display_label: "Entity: alternate", predicate: "table.structure/entity_binding", instance_key: "alternate", evidence_summary: { usable_observations: 4, evidence_count: 1 }, rank: 2 }, { candidate_id: "entity-1", display_label: "Entity: application", predicate: "table.structure/entity_binding", instance_key: "primary", evidence_summary: { usable_observations: 5, evidence_count: 2 }, rank: 1, recommended: true }],
      temporals: [{ candidate_id: "period-1", display_label: "Period: reporting month", predicate: "table.temporal/temporal_binding", instance_key: "primary", evidence_summary: { usable_observations: 5 }, rank: 1 }],
      row_grains: [{ candidate_id: "grain-1", display_label: "Row grain: application + month", predicate: "table.structure/row_grain", instance_key: "primary", evidence_summary: { usable_observations: 5 }, rank: 1 }],
      observed_cadences: [],
    },
    candidate_limits: { entities: { returned: 2, truncated: true, limit: 20 }, temporals: { returned: 1, truncated: false, limit: 20 }, row_grains: { returned: 1, truncated: false, limit: 20 }, observed_cadences: { returned: 0, truncated: false, limit: 20 } },
    recommendations: { default_entity: "entity-1", default_temporal: "period-1", row_grain: "grain-1", observed_cadence: null }, warnings: [],
  }], diagnostic_assistance: [],
};

test("endpoint-shaped DTO uses the public materialization, candidate, and limit fields", () => {
  const selections = initialStructureSelections(review);
  assert.deepEqual(selections, { applications: { default_entity_candidate_id: "entity-1", default_temporal_candidate_id: "period-1", row_grain_candidate_id: "grain-1" } });
  assert.equal(candidateEvidenceSummary(review.tables[0].candidates.entities[1]), "5 usable observations · 2 evidence records");
  assert.equal(boundedCandidates(review.tables[0].candidates.entities, "", review.tables[0].candidate_limits.entities).length, 2);
  assert.equal(selectionMessage(review, selections), STRUCTURE_COPY.supported);
});

test("candidate UI ranks, filters, and honors the server-provided bound", () => {
  const candidates = Array.from({ length: MAX_CANDIDATES_PER_FACET + 2 }, (_, index) => ({ candidate_id: String(index), display_label: `Candidate ${index}`, rank: MAX_CANDIDATES_PER_FACET + 2 - index }));
  assert.equal(boundedCandidates(candidates, "", { limit: 3 }).length, 3);
  assert.equal(boundedCandidates(candidates, "Candidate 3", { limit: 20 })[0].candidate_id, "3");
});

test("optional backend facet-page envelope is consumed without changing the candidate contract", () => {
  const page = facetCandidates({ candidates: { entities: { items: [{ candidate_id: "entity-1" }], next_page: 2 } } }, "entities");
  assert.equal(page.items[0].candidate_id, "entity-1");
  assert.equal(page.page.next_page, 2);
});

test("two bounded server pages and a search result remain reachable without duplicate candidates", () => {
  const first = Array.from({ length: 20 }, (_, index) => ({ candidate_id: `candidate-${index}` }));
  const second = Array.from({ length: 20 }, (_, index) => ({ candidate_id: `candidate-${index + 20}` }));
  const combined = mergeCandidatePage(first, second);
  assert.equal(combined.length, 40);
  assert.equal(combined.at(-1).candidate_id, "candidate-39");
  assert.deepEqual(mergeCandidatePage(combined, [{ candidate_id: "candidate-21" }, { candidate_id: "search-match" }]).at(-1), { candidate_id: "search-match" });
});

test("explicit null remains a user selection and does not re-prefill a recommendation", () => {
  const withNull = { ...review, draft: { ...review.draft, selections: { tables: [{ table: "applications", default_entity_candidate_id: null }] } } };
  const selections = initialStructureSelections(withNull);
  assert.equal(selections.applications.default_entity_candidate_id, null);
  assert.equal(draftPayload(withNull, selections).selections.tables[0].default_entity_candidate_id, null);
});

test("re-opening a limited review preserves acknowledged no-selection decisions", () => {
  const saved = {
    ...review,
    tables: [{ ...review.tables[0], state: "limited", requirements: ["entity", "temporal", "row_grain"] }],
    draft: { ...review.draft, selections: { tables: [{
      table: "applications",
      default_entity_candidate_id: null, default_entity_candidate_id_action: "clear", default_entity_candidate_id_acknowledged: true,
      default_temporal_candidate_id: null, default_temporal_candidate_id_action: "clear", default_temporal_candidate_id_acknowledged: true,
      row_grain_candidate_id: null, row_grain_candidate_id_action: "clear", row_grain_candidate_id_acknowledged: true,
      expected_cadence: { action: "clear", acknowledged: true },
    }] } },
  };
  const selections = initialStructureSelections(saved);
  assert.equal(selections.applications.default_entity_candidate_id_acknowledged, true);
  assert.equal(selections.applications.default_temporal_candidate_id_acknowledged, true);
  assert.equal(selections.applications.row_grain_candidate_id_acknowledged, true);
  assert.equal(canConfirmStructure(saved, selections), true);
});

test("selection messaging uses selections and stated requirements, not candidate availability", () => {
  const temporalOnly = { ...review, tables: [{ ...review.tables[0], requirements: ["temporal"], candidates: { ...review.tables[0].candidates, temporals: [] } }] };
  assert.equal(selectionMessage(temporalOnly, { applications: { default_temporal_candidate_id: "cand_previous" } }), STRUCTURE_COPY.supported);
  assert.equal(selectionMessage(temporalOnly, { applications: { default_temporal_candidate_id: null } }), STRUCTURE_COPY.missingTime);
});

test("reconfirmation is recovered from review state, stale materialization, or save outcome", () => {
  assert.equal(reviewNeedsReconfirmation({ ...review, structure_review_state: "needs_reconfirmation" }), true);
  assert.equal(reviewNeedsReconfirmation({ ...review, materialization: { ...review.materialization, freshness: "stale" } }), true);
  assert.equal(reviewNeedsReconfirmation(review, "needs_reconfirmation"), true);
  assert.equal(reviewNeedsReconfirmation(review), false);
});

test("metadata correction deep-link keeps only the exact item/table/column and returns to the same structure review", () => {
  const params = metadataCorrectionParams("item_safe", { table: "observations", column: "reporting_period" });
  const target = metadataCorrectionTarget(new URLSearchParams(Object.entries(params).filter(([, value]) => value != null)));
  assert.deepEqual(target, { itemId: "item_safe", table: "observations", column: "reporting_period" });
  assert.deepEqual(structureReviewParams(target.itemId), { structure: "item_safe", item: null, table: null, column: null, return: null });
  assert.equal(metadataCorrectionTarget(new URLSearchParams("return=dataset-structure&table=observations")), null);
});

test("serialized autosave coalesces edits and reuses an idempotency key for an exact retry", () => {
  let keyNo = 0;
  const saves = createAutosaveCoordinator(() => `key-${++keyNo}`);
  saves.markDirty();
  const first = saves.begin({ draft_revision: 1, selections: { tables: [] }, evidence_fingerprint: "a" });
  saves.markDirty();
  assert.equal(saves.begin(first.payload), null, "a second request cannot overlap the first");
  assert.equal(saves.finish(first).hasNewerEdits, true, "an old response cannot clear newer dirty edits");
  const retry = saves.begin(first.payload);
  assert.equal(retry.idempotencyKey, first.idempotencyKey);
  assert.equal(saves.finish(retry).hasNewerEdits, false);
  saves.markDirty();
  const changed = saves.begin({ ...first.payload, draft_revision: 2 });
  assert.notEqual(changed.idempotencyKey, first.idempotencyKey);
});

test("materialization poller repeatedly polls live statuses, reloads once terminal, and cancels cleanly", async () => {
  const timers = [];
  const schedule = (callback, delay) => { const timer = { callback, delay, cancelled: false }; timers.push(timer); return timer; };
  const cancelTimer = (timer) => { timer.cancelled = true; };
  const statuses = [{ status: "queued", retry_after_ms: 700 }, { status: "running", retry_after_ms: 900 }, { status: "succeeded", retry_after_ms: null }];
  let polls = 0;
  let reloads = 0;
  const stop = createMaterializationPoller({
    jobId: "dscj-1", status: "queued", retryAfterMs: 500,
    poll: async () => { polls += 1; return statuses.shift(); }, reload: async () => { reloads += 1; },
    schedule, cancelTimer,
  });
  assert.equal(timers[0].delay, 500);
  await timers[0].callback();
  assert.equal(polls, 1);
  assert.equal(timers[1].delay, 700);
  await timers[1].callback();
  assert.equal(polls, 2);
  assert.equal(timers[2].delay, 900);
  await timers[2].callback();
  assert.equal(polls, 3);
  assert.equal(reloads, 1);
  stop();
  const cancelled = createMaterializationPoller({ jobId: "dscj-2", status: "queued", retryAfterMs: 500, poll: async () => { throw new Error("must not poll"); }, reload: async () => {}, schedule, cancelTimer });
  const timer = timers.at(-1);
  cancelled();
  assert.equal(timer.cancelled, true);
});

test("observed cadence uses the structured safe evidence formatter", () => {
  const formatted = candidateEvidenceSummary({ evidence_summary: { usable_observations: 12, evidence_count: 3 } });
  assert.equal(formatted, "12 usable observations · 3 evidence records");
  assert.equal(formatted.includes("[object Object]"), false);
});

test("authority decision preserves an expected-cadence proposal and retries with one stable key", () => {
  const withProposal = { ...review, tables: [{ ...review.tables[0], recommendations: { ...review.tables[0].recommendations, expected_cadence: { axis_candidate_id: "period-1", grouping_candidate_id: "entity-1", value: { unit: "month", step: 1 } } } }] };
  const selections = initialStructureSelections(withProposal);
  const payload = decisionPayload(withProposal, selections);
  assert.equal(payload.confirm, true);
  assert.deepEqual(payload.selections.tables[0].expected_cadence.value, { unit: "month", step: 1 });
  const decisions = createDecisionCoordinator(() => "decision-key");
  assert.equal(decisions.begin(payload).idempotencyKey, decisions.begin(payload).idempotencyKey);
  assert.equal(canConfirmStructure(withProposal, selections), true);
});

test("expected-cadence draft edit is included in autosave and survives re-open", () => {
  const edit = { action: "replace", axis_candidate_id: "period-1", grouping_candidate_id: "entity-1", value: { unit: "quarter", step: 1 } };
  const saved = { ...review, draft: { ...review.draft, selections: { tables: [{ table: "applications", expected_cadence: edit }] } } };
  const selections = initialStructureSelections(saved);
  assert.deepEqual(selections.applications.expected_cadence, edit);
  assert.deepEqual(draftPayload(saved, selections).selections.tables[0].expected_cadence, edit);
});

test("limited no-selection confirmation needs an acknowledged closed action", () => {
  const limited = { ...review, tables: [{ ...review.tables[0], state: "limited", requirements: ["entity"] }] };
  assert.equal(canConfirmStructure(limited, { applications: { default_entity_candidate_id: null, default_entity_candidate_id_action: "clear", default_entity_candidate_id_acknowledged: false } }), false);
  const required = { ...review, tables: [{ ...review.tables[0], requirements: ["entity"] }] };
  assert.equal(canConfirmStructure(required, { applications: { default_entity_candidate_id: null, default_entity_candidate_id_action: "clear", default_entity_candidate_id_acknowledged: false } }), false);
  assert.equal(canConfirmStructure(required, { applications: { default_entity_candidate_id: null, default_entity_candidate_id_action: "clear", default_entity_candidate_id_acknowledged: true } }), true);
});
