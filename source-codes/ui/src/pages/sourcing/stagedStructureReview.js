export const STAGED_STRUCTURE_FIELDS = [
  "default_entity_candidate_id", "default_temporal_candidate_id", "row_grain_candidate_id",
];

const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);

export function restoreStagedStructureChoices(precheck) {
  if (precheck?.draft?.stale) return {};
  return Object.fromEntries((precheck?.draft?.selections?.tables || []).map((row) => [row.table, { ...row }]));
}

export function stagedStructureReviewComplete(precheck, choices) {
  const tables = precheck?.tables || [];
  if (!tables.length || precheck?.draft?.stale) return false;
  return tables.every((table) => {
    const selection = choices[table.table] || {};
    if (!STAGED_STRUCTURE_FIELDS.every((field) => hasOwn(selection, field))) return false;
    if (selection.default_entity_candidate_id === null && !selection.entity_acknowledged) return false;
    if (selection.default_temporal_candidate_id === null && !selection.temporal_acknowledged) return false;
    const grain = table.candidates.find((row) => row.candidate_id === selection.row_grain_candidate_id);
    if ((!grain || grain.quality !== "exact") && !selection.row_grain_acknowledged) return false;
    const cadence = selection.expected_cadence;
    if (!cadence) return false;
    if (cadence.action === "confirm") {
      return selection.default_temporal_candidate_id !== null
        && ["day", "week", "month", "quarter", "year"].includes(cadence.value?.unit)
        && Number.isInteger(cadence.value?.step) && cadence.value.step > 0;
    }
    return ["clear", "mark_not_applicable"].includes(cadence.action) && cadence.acknowledged === true;
  });
}

export function stagedStructureSelections(precheck, choices) {
  return (precheck?.tables || []).filter((table) => choices[table.table])
    .map((table) => ({ table: table.table, ...choices[table.table] }));
}
