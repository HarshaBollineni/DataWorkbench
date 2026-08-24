// ui/src/lib/assetHistoryLabels.js — 0.5.0 Step 3c (ADM-06/07).
//
// Human labels for the raw status/intent tokens the asset-history endpoint
// returns (`backend/assets/history.py` deliberately returns them RAW —
// snapshot_status, version status, intent, kind — because turning a status
// into prose is a rendering concern, the same M-2 discipline Step 2 already
// established for reset messages: `superseded` must never be shown as the
// bare word "superseded", `add_period` must never leak as "add_period", etc.
//
// This is a DIFFERENT map from `./resetLabels.js` — that one groups reset
// DELETE COUNTS by table name (ADM-01..05); this one labels asset lifecycle
// tokens (ADM-06/07). Reusing resetLabels.js's `summarizeReset()` for the
// `resets` array in the history payload is what Step 3c's brief actually
// asks to reuse, and `VersionHistory.jsx` does exactly that — this module
// is the SEPARATE, necessary label set for the version/snapshot tokens
// resetLabels.js has no entries for at all.
//
// Every function here is total: an unrecognised token still renders as a
// legible (if generic) label — "Unknown (<token>)" — rather than crashing
// or leaking `undefined`, but every token this release actually produces
// has an explicit entry below, so the generic fallback is a safety net,
// never the normal path.

const SNAPSHOT_STATUS_LABELS = {
  active: "Active",
  superseded: "Superseded (retained for audit)",
};

const VERSION_STATUS_LABELS = {
  current: "Current version",
  superseded: "Superseded",
};

const INTENT_LABELS = {
  fresh: "Fresh upload",
  add_period: "Add period",
  full_replacement: "Full replacement",
};

const ASSET_KIND_LABELS = {
  dataset: "Dataset",
  database: "Database",
};

const RESET_GRADE_LABELS = {
  surgical: "Surgical reset",
  wipe: "Full wipe",
};

function labelOr(map, token, kind) {
  if (token == null || token === "") return "—";
  return map[token] || `Unknown (${kind}: ${token})`;
}

export const snapshotStatusLabel = (token) => labelOr(SNAPSHOT_STATUS_LABELS, token, "snapshot status");
export const versionStatusLabel = (token) => labelOr(VERSION_STATUS_LABELS, token, "version status");
export const intentLabel = (token) => labelOr(INTENT_LABELS, token, "intent");
export const assetKindLabel = (token) => labelOr(ASSET_KIND_LABELS, token, "kind");
export const resetGradeLabel = (token) => labelOr(RESET_GRADE_LABELS, token, "reset grade");
