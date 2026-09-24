// Display grouping uses persisted identity, never the wording of a question.
export function candidateLabel(index) {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    value -= 1;
    label = String.fromCharCode(65 + value % 26) + label;
    value = Math.floor(value / 26);
  }
  return `Candidate ${label}`;
}

export function hypothesisGroups(bundle) {
  const looks = bundle?.looks || [];
  const known = new Map();
  const add = (hypothesis) => {
    if (!hypothesis?.hypothesis_id || known.has(hypothesis.hypothesis_id)) return;
    known.set(hypothesis.hypothesis_id, { ...hypothesis,
      number: null, looks: [] });
  };
  (bundle?.hypothesis_catalog || []).forEach(add);
  [...(bundle?.hypothesis_candidates || []), ...(bundle?.focused_hypothesis_candidates || []),
    ...(bundle?.hypotheses || []), bundle?.selected_initial_hypothesis,
    bundle?.selected_focused_hypothesis, bundle?.active_investigation_hypothesis].forEach(add);
  const byLook = new Map(looks.map((look) => [look.look_id, look]));
  const unassigned = [];
  let nextNumber = 1;
  for (const look of looks) {
    const fork = look.fork_json || {};
    const parent = byLook.get(fork.combined_parent_look_id)?.fork_json;
    const hypothesisId = fork.hypothesis_id || parent?.hypothesis_id;
    if (!hypothesisId) { unassigned.push(look); continue; }
    add({ hypothesis_id: hypothesisId, statement: fork.hypothesis || parent?.hypothesis });
    const group = known.get(hypothesisId);
    // Catalog numbers describe candidate creation, not investigation order.
    // Retained plans (including failed/cancelled tests) keep their historical place.
    if (group.number == null) group.number = nextNumber++;
    group.looks.push({ ...look, display_number: `${group.number}.${group.looks.length + 1}`,
      display_stage: fork.combined_parent_look_id ? "Confirmation"
        : fork.kind === "agent_driver_search" ? "Discovery"
          : group.looks.length ? "Follow-up" : "Hypothesis test" });
  }
  const active = bundle?.active_investigation_hypothesis
    || bundle?.selected_focused_hypothesis || bundle?.selected_initial_hypothesis;
  const pending = known.get(active?.hypothesis_id);
  if (pending && pending.number == null) pending.number = nextNumber;
  return { groups: [...known.values()].sort((a, b) => (a.number ?? Infinity) - (b.number ?? Infinity)), unassigned };
}
