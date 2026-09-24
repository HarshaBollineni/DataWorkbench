import test from "node:test";
import assert from "node:assert/strict";
import { candidateLabel, hypothesisGroups } from "../src/features/rca/hypothesisGroups.js";

test("persisted hypothesis identity groups and numbers tests without renumbering history", () => {
  const bundle = { hypothesis_catalog: [{ hypothesis_id: "hyp-a", number: 1 }, { hypothesis_id: "hyp-b", number: 2 }],
    looks: [{ look_id: "opening" }, { look_id: "discovery", fork_json: { hypothesis_id: "hyp-a", kind: "agent_driver_search" } },
      { look_id: "confirmation", fork_json: { combined_parent_look_id: "discovery" } },
      { look_id: "followup", fork_json: { hypothesis_id: "hyp-a" } }] };
  const grouped = hypothesisGroups(bundle);
  assert.deepEqual(grouped.unassigned.map((look) => look.look_id), ["opening"]);
  assert.deepEqual(grouped.groups[0].looks.map((look) => [look.display_number, look.display_stage]),
    [["1.1", "Discovery"], ["1.2", "Confirmation"], ["1.3", "Follow-up"]]);
  bundle.looks.push({ look_id: "another", fork_json: { hypothesis_id: "hyp-b" } });
  assert.deepEqual(hypothesisGroups(JSON.parse(JSON.stringify(bundle))).groups[0], grouped.groups[0]);
  assert.equal(hypothesisGroups(bundle).groups[1].looks[0].display_number, "2.1");
});

test("legacy records without IDs stay unlinked rather than becoming invented hypotheses", () => {
  assert.equal(hypothesisGroups({ looks: [{ look_id: "old", fork_json: { hypothesis: "Text is not identity" } }] }).groups.length, 0);
  assert.deepEqual(hypothesisGroups({}).unassigned, []);
});

test("the second initial candidate becomes the first investigated hypothesis", () => {
  const bundle = { hypothesis_catalog: ["a", "b", "c"].map((id, index) => ({ hypothesis_id: id, number: index + 1 })),
    active_investigation_hypothesis: { hypothesis_id: "b" },
    looks: [{ look_id: "opening" }, { look_id: "first", fork_json: { hypothesis_id: "b", kind: "agent_driver_search" } },
      { look_id: "confirm", fork_json: { combined_parent_look_id: "first" } }] };
  let groups = hypothesisGroups(bundle).groups;
  assert.equal(groups[0].hypothesis_id, "b");
  assert.equal(groups[0].number, 1);
  assert.deepEqual(groups[0].looks.map((look) => look.display_number), ["1.1", "1.2"]);
  assert.equal(groups.find((group) => group.hypothesis_id === "a").number, null);
  bundle.looks.push({ look_id: "later", fork_json: { hypothesis_id: "a" } });
  bundle.active_investigation_hypothesis = { hypothesis_id: "a" };
  groups = hypothesisGroups(JSON.parse(JSON.stringify(bundle))).groups;
  assert.deepEqual(groups.filter((group) => group.number).map((group) => [group.hypothesis_id, group.number]), [["b", 1], ["a", 2]]);
  assert.equal(groups[1].looks[0].display_number, "2.1");
});

test("selected hypothesis awaiting its first plan is one, candidate labels stay separate", () => {
  const grouped = hypothesisGroups({ hypothesis_candidates: [{ hypothesis_id: "a" }, { hypothesis_id: "b" }],
    selected_initial_hypothesis: { hypothesis_id: "b" } });
  assert.equal(grouped.groups[0].hypothesis_id, "b");
  assert.equal(grouped.groups[0].number, 1);
  assert.equal(candidateLabel(0), "Candidate A");
  assert.equal(candidateLabel(1), "Candidate B");
  assert.equal(candidateLabel(26), "Candidate AA");
});
