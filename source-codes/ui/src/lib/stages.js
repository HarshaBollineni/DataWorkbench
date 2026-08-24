// Display names for the two framework stages (feedback 3.1: user-facing labels
// are Systemic / Specific everywhere; internal keys stay stage1 / stage2).
const LABELS = { stage1: "Systemic", stage2: "Specific", both: "Both" };

export function stageLabel(stage) {
  return LABELS[stage] || stage || "-";
}
