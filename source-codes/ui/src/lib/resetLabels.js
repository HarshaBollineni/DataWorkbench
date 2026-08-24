// ui/src/lib/resetLabels.js — ADM-01..05 (0.5.0 plan Step 2).
//
// Reset results are reported in business language, never in table names
// (ADM-01). This is the ONE shared helper both Admin.jsx reset messages and
// the retained-data notice compose through (ADM-04/ADM-05) — replaces the
// old `summarizeCounts` that rendered raw `table=count` pairs.
//
// The table/key -> {label, group} mapping itself lives in exactly one place,
// `./resetLabels.json`, so the frontend and `backend/tests/test_reset_language.py`
// (the ADM-04 build-time "fail loudly" gate) read the same data — there is no
// second map anywhere that could drift from this one.
//
// Contract:
//   summarizeReset(counts) -> {
//     yourWork:  [{ label, count }],   // ADM-02 order, zero-count rows filtered
//     platform:  [{ label, count }],
//     sentence:  string                // the full human-readable message (ADM-03)
//   }
import RESET_LABELS from "./resetLabels.json";

const PLATFORM_SENTENCE =
  "Platform reference data (framework, taxonomy, agent skills and thresholds) " +
  "was cleared and immediately restored — the product is ready to use.";

const EMPTY_SENTENCE = "Nothing needed removing — there was no work data to clear.";

// Grammatically correct singular forms for the handful of labels that can
// realistically show a count of exactly 1 (ADM-02 adversarial case: "1 asset",
// never "1 assets"). Anything not listed here falls back to a naive
// strip-trailing-"s", which is right for the plural nouns this map actually
// produces.
const SINGULAR = {
  assets: "asset",
  "connected databases": "connected database",
  "uploaded files": "uploaded file",
  "uploaded tables": "uploaded table",
  "variables and their profiles": "variable and its profile",
  tags: "tag",
  issues: "issue",
  "RCA cases": "RCA case",
  "knowledge-base documents": "knowledge-base document",
  "test results and findings": "test result or finding",
  "saved workflow context": "saved workflow context item",
  "scheduled tasks and alerts": "scheduled task or alert",
  "background activity records": "background activity record",
  "support requests": "support request",
  "agent skills": "agent skill",
  "framework and diagnostics reference data": "framework or diagnostics reference record",
  "thresholds and settings": "threshold or setting",
  "tenancy configuration": "tenancy configuration record",
  "taxonomy reference data": "taxonomy reference record",
  "other platform records": "other platform record",
};

function noun(label, count) {
  if (count === 1) {
    return SINGULAR[label] || (label.endsWith("s") ? label.slice(0, -1) : label);
  }
  return label;
}

function joinWithAnd(parts) {
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0];
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`;
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

const EXACT = RESET_LABELS.exact || {};
const PREFIXES = Object.entries(RESET_LABELS.prefixes || {});
const ORDER = RESET_LABELS.order || { yourWork: [], platform: [] };

/**
 * Resolve one raw key to its {label, group}, or null if resetLabels.json has
 * no entry for it. Exact match first; then the (currently one, `rca_`)
 * prefix rule — mirroring `system_db._rca_tables()`, which deliberately
 * reads every `rca_*` table LIVE off `sqlite_master` rather than a hardcoded
 * list ("so a reset never silently misses a table a later RCA stage adds").
 * The label map has to be equally dynamic for that one family, or an ad-hoc
 * or newly-added rca_* table would leak its raw name the moment it exists.
 */
function resolveKey(key) {
  if (EXACT[key]) return EXACT[key];
  for (const [prefix, meta] of PREFIXES) {
    if (key.startsWith(prefix)) return meta;
  }
  return null;
}

/**
 * Group a raw {tableOrKey: count} object into ADM-02's two ordered buckets,
 * summing counts that share a label (a user never sees one concept counted
 * twice) and hiding zero counts (ADM-03). Order follows resetLabels.json's
 * explicit `order` lists — the business priority this map was written in —
 * never whatever order the backend happened to serialize its dict in. A
 * label that earns a count but is missing from `order` (stale order list,
 * not a missing label) is still shown, appended after the ones that are
 * listed, rather than silently dropped.
 *
 * A key with NO entry in resetLabels.json at all (ADM-04's "gains a count
 * later") is never rendered under its raw name: it is folded into a generic
 * "other platform records" line and reported to the console so it gets
 * noticed and labelled — the build-time completeness test
 * (backend/tests/test_reset_language.py) is what actually stops this from
 * shipping; this is only the runtime backstop if it ever did.
 */
function groupRows(counts) {
  const safeCounts = counts || {};
  const sums = new Map(); // label -> { label, group, count }

  for (const [key, value] of Object.entries(safeCounts)) {
    if (!value) continue;
    const meta = resolveKey(key);
    if (meta) {
      if (!sums.has(meta.label)) sums.set(meta.label, { label: meta.label, group: meta.group, count: 0 });
      sums.get(meta.label).count += value;
      continue;
    }
    if (typeof console !== "undefined" && console.error) {
      console.error(
        `[resetLabels] no human label configured for reset key "${key}" — ` +
          "add it to ui/src/lib/resetLabels.json (ADM-04)."
      );
    }
    const label = "other platform records";
    if (!sums.has(label)) sums.set(label, { label, group: "platform", count: 0 });
    sums.get(label).count += value;
  }

  function ordered(group, orderList) {
    const seen = new Set();
    const rows = [];
    for (const label of orderList) {
      const row = sums.get(label);
      if (row && row.group === group) {
        rows.push({ label: row.label, count: row.count });
        seen.add(label);
      }
    }
    // Anything with a count that earned this group but isn't in the order
    // list (a stale/incomplete `order` array) still gets shown.
    for (const row of sums.values()) {
      if (row.group === group && !seen.has(row.label)) rows.push({ label: row.label, count: row.count });
    }
    return rows;
  }

  return {
    yourWork: ordered("yourWork", ORDER.yourWork),
    platform: ordered("platform", ORDER.platform),
  };
}

export function summarizeReset(counts) {
  const { yourWork, platform } = groupRows(counts);

  const yourWorkPhrase = yourWork.length
    ? `Removed ${joinWithAnd(yourWork.map((r) => `${r.count} ${noun(r.label, r.count)}`))}.`
    : "";
  const platformPhrase = platform.length ? PLATFORM_SENTENCE : "";

  const sentence =
    yourWorkPhrase || platformPhrase
      ? [yourWorkPhrase, platformPhrase].filter(Boolean).join(" ")
      : EMPTY_SENTENCE;

  return { yourWork, platform, sentence };
}
