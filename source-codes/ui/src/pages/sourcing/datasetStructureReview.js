export const STRUCTURE_COPY = {
  supported: "These selections are supported by the dataset evidence. They will be used to prefill applicable diagnostics, and you can review them before execution.",
  startingPoints: "These selections can be used as diagnostic starting points. Some diagnostics will ask you to confirm their interpretation before execution.",
  limited: "Some selected details have limited supporting evidence. They can be carried forward as suggestions, but additional confirmation may be required.",
  different: "Your selection differs from the observed dataset structure. It will be preserved as your intended configuration, and applicable diagnostics will ask you to confirm the difference before execution.",
  missingTime: "No confirmed Date or Period field is available. Temporal diagnostics cannot be prepared, but non-temporal diagnostics remain available.",
  missingEntity: "No confirmed entity identifier is available. Entity-based and cadence diagnostics cannot be prepared. You may review suggested columns or confirm that no identifier exists.",
};

export const REVIEWABLE_MATERIALIZATION_STATUSES = new Set(["queued", "running", "retry_wait"]);
export const MAX_CANDIDATES_PER_FACET = 20;
export const MAX_ACCUMULATED_CANDIDATES = 100;
export const SELECTION_FIELDS = ["default_entity_candidate_id", "default_temporal_candidate_id", "row_grain_candidate_id"];
const FIELD_REQUIREMENT_KEYS = {
  default_entity_candidate_id: ["entity", "entities", "default_entity", "default_entity_candidate_id"],
  default_temporal_candidate_id: ["temporal", "temporals", "date", "period", "default_temporal", "default_temporal_candidate_id"],
  row_grain_candidate_id: ["row_grain", "row_grains", "grain", "row_grain_candidate_id"],
};
const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);

export function boundedCandidates(candidates = [], filter = "", candidateLimit = {}) {
  const term = String(filter).trim().toLocaleLowerCase();
  const limit = Number.isInteger(candidateLimit?.limit) ? candidateLimit.limit : MAX_CANDIDATES_PER_FACET;
  return [...candidates]
    .sort((left, right) => Number(left.rank ?? Number.MAX_SAFE_INTEGER) - Number(right.rank ?? Number.MAX_SAFE_INTEGER))
    .filter((candidate) => !term || `${candidate.display_label || ""} ${candidate.evidence_summary || ""} ${candidate.predicate || ""} ${candidate.instance_key || ""}`.toLocaleLowerCase().includes(term))
    .slice(0, limit);
}

// The initial Slice-2 endpoint returns arrays. Accept an optional paged facet
// envelope too, so a backend can add bounded page/search projection without a
// UI contract fork.
export function facetCandidates(table, facet) {
  const supplied = table?.candidates?.[facet];
  if (Array.isArray(supplied)) return { items: supplied, page: null };
  if (supplied && typeof supplied === "object") return { items: supplied.items || supplied.candidates || [], page: supplied };
  const page = table?.candidate_pages?.[facet] || null;
  return { items: page?.items || page?.candidates || [], page };
}

export function mergeCandidatePage(current = [], page = [], maximum = MAX_ACCUMULATED_CANDIDATES) {
  const merged = [...current];
  const known = new Set(current.map((candidate) => candidate.candidate_id));
  page.forEach((candidate) => {
    if (!known.has(candidate.candidate_id) && merged.length < maximum) {
      known.add(candidate.candidate_id);
      merged.push(candidate);
    }
  });
  return merged;
}

export function candidateEvidenceSummary(candidate) {
  const summary = candidate?.evidence_summary;
  if (typeof summary === "string") return summary;
  if (!summary || typeof summary !== "object") return "";
  const parts = [];
  if (summary.usable_observations != null) parts.push(`${Number(summary.usable_observations).toLocaleString()} usable observations`);
  if (summary.evidence_count != null) parts.push(`${Number(summary.evidence_count).toLocaleString()} evidence records`);
  return parts.join(" · ");
}

function draftTableByName(review, tableName) {
  return review?.draft?.selections?.tables?.find((entry) => entry.table === tableName) || {};
}
function recommendedId(table, field) {
  const recommendation = table?.recommendations?.[field];
  if (typeof recommendation === "string") return recommendation;
  return recommendation?.candidate_id || null;
}

// Explicit null is a user decision. Only an absent field can receive a
// recommendation while a draft is opened.
export function initialStructureSelections(review) {
  return Object.fromEntries((review?.tables || []).map((table) => {
    const saved = draftTableByName(review, table.table);
    const noSelectionDecisions = Object.fromEntries(SELECTION_FIELDS.flatMap((field) => [
      ...[`action`, `acknowledged`].filter((suffix) => hasOwn(saved, `${field}_${suffix}`))
        .map((suffix) => [`${field}_${suffix}`, saved[`${field}_${suffix}`]]),
    ]));
    return [table.table, {
      default_entity_candidate_id: hasOwn(saved, "default_entity_candidate_id") ? saved.default_entity_candidate_id : recommendedId(table, "default_entity"),
      default_temporal_candidate_id: hasOwn(saved, "default_temporal_candidate_id") ? saved.default_temporal_candidate_id : recommendedId(table, "default_temporal"),
      row_grain_candidate_id: hasOwn(saved, "row_grain_candidate_id") ? saved.row_grain_candidate_id : recommendedId(table, "row_grain"),
      ...noSelectionDecisions,
      ...(hasOwn(saved, "expected_cadence") ? { expected_cadence: saved.expected_cadence } : table.recommendations?.expected_cadence ? { expected_cadence: { action: "confirm", ...table.recommendations.expected_cadence } } : {}),
    }];
  }));
}

export function draftPayload(review, selections) {
  return {
    draft_revision: review?.draft?.revision ?? 0,
    evidence_fingerprint: review?.draft?.evidence_fingerprint || "",
    selections: {
      tables: (review?.tables || []).map((table) => {
        const selection = { table: table.table };
        const values = selections?.[table.table] || {};
        SELECTION_FIELDS.forEach((field) => { if (hasOwn(values, field)) selection[field] = values[field]; });
        SELECTION_FIELDS.forEach((field) => {
          ["action", "acknowledged"].forEach((suffix) => { const key = `${field}_${suffix}`; if (hasOwn(values, key)) selection[key] = values[key]; });
        });
        if (hasOwn(values, "expected_cadence")) selection.expected_cadence = values.expected_cadence;
        return selection;
      }),
    },
  };
}

export function decisionPayload(review, selections) {
  const draft = draftPayload(review, selections);
  draft.selections.tables = draft.selections.tables.map((table) => {
    const cadence = selections?.[table.table]?.expected_cadence;
    return cadence ? { ...table, expected_cadence: cadence } : table;
  });
  return { confirm: true, draft_revision: draft.draft_revision,
    decision_basis: { evidence_fingerprint: draft.evidence_fingerprint }, selections: draft.selections };
}

export function canConfirmStructure(review, selections, { saving = false, dirty = false, metadataIncompatible = false } = {}) {
  if (!review?.draft?.editable || saving || dirty || metadataIncompatible || REVIEWABLE_MATERIALIZATION_STATUSES.has(review?.materialization?.status)) return false;
  return (review.tables || []).every((table) => {
    const value = selections?.[table.table] || {};
    if (value.expected_cadence && value.expected_cadence.action !== "confirm" && value.expected_cadence.acknowledged !== true) return false;
    if (table.state === "limited") return SELECTION_FIELDS.every((field) => !requirementIsActive(table.requirements, field) || Boolean(value[field]) || (["clear", "mark_not_applicable"].includes(value[`${field}_action`]) && value[`${field}_acknowledged`] === true));
    return SELECTION_FIELDS.every((field) => !requirementIsActive(table.requirements, field) || Boolean(value[field]) || (["clear", "mark_not_applicable"].includes(value[`${field}_action`]) && value[`${field}_acknowledged`] === true));
  });
}

export function createDecisionCoordinator(makeKey = newIdempotencyKey) {
  const keys = new Map();
  return { begin(payload) { const canonical = canonicalPayloadKey(payload); if (!keys.has(canonical)) keys.set(canonical, makeKey()); return { payload, idempotencyKey: keys.get(canonical) }; } };
}

function requirementIsActive(requirements, field) {
  if (!requirements) return true;
  const keys = FIELD_REQUIREMENT_KEYS[field];
  if (Array.isArray(requirements)) return requirements.some((value) => keys.includes(String(value)));
  if (typeof requirements === "object") return keys.some((key) => requirements[key] === true || requirements[key]?.required === true);
  return true;
}
function selectedFor(review, selections, field) {
  return (review?.tables || []).some((table) => Boolean(selections?.[table.table]?.[field]));
}

export function selectionMessage(review, selections) {
  const tables = review?.tables || [];
  const requires = (field) => tables.some((table) => requirementIsActive(table.requirements, field));
  const anyMissingRequired = tables.some((table) => SELECTION_FIELDS.some((field) =>
    requirementIsActive(table.requirements, field) && !selections?.[table.table]?.[field]));
  const chosen = Object.values(selections || {}).some((table) => Object.values(table).some(Boolean));
  const differsFromObservation = tables.some((table) => (table.warnings || []).some((warning) =>
    String(warning?.code || warning) === "different_from_observed"));
  if (requires("default_temporal_candidate_id") && !selectedFor(review, selections, "default_temporal_candidate_id")) return STRUCTURE_COPY.missingTime;
  if (requires("default_entity_candidate_id") && !selectedFor(review, selections, "default_entity_candidate_id")) return STRUCTURE_COPY.missingEntity;
  if (differsFromObservation) return STRUCTURE_COPY.different;
  if (review?.structure_review_state === "limited" || tables.some((table) => table.state === "limited") || anyMissingRequired) return STRUCTURE_COPY.limited;
  if (chosen) return STRUCTURE_COPY.supported;
  return STRUCTURE_COPY.startingPoints;
}

export function reviewNeedsReconfirmation(review, savedStatus = "") {
  return review?.structure_review_state === "needs_reconfirmation"
    || review?.materialization?.freshness === "stale"
    || review?.materialization?.status === "stale"
    || savedStatus === "needs_reconfirmation";
}
export function insufficientAssistance(review) {
  return (review?.diagnostic_assistance || []).filter((entry) => ["limited", "unavailable"].includes(entry.state));
}
export function safeWarningLabel(warning) {
  if (typeof warning === "string") return warning.replaceAll("_", " ");
  return String(warning?.code || warning?.message || "Review warning").replaceAll("_", " ");
}
export function canonicalPayloadKey(payload) {
  const canonical = (value) => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
    return value;
  };
  return JSON.stringify(canonical(payload));
}
export function newIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `dsc-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// Timing belongs to the component; this coordinator keeps sequential saves,
// coalesced edits, and one key per exact canonical request/retry.
export function createAutosaveCoordinator(makeKey = newIdempotencyKey) {
  let editVersion = 0;
  let inFlight = false;
  const keys = new Map();
  return {
    markDirty() { editVersion += 1; return editVersion; },
    begin(payload) {
      if (inFlight) return null;
      const canonical = canonicalPayloadKey(payload);
      if (!keys.has(canonical)) keys.set(canonical, makeKey());
      inFlight = true;
      return { payload, idempotencyKey: keys.get(canonical), version: editVersion };
    },
    finish(operation) { inFlight = false; return { hasNewerEdits: editVersion > operation.version }; },
    isInFlight() { return inFlight; },
  };
}

export function createMaterializationPoller({
  jobId, status, retryAfterMs, poll, reload, update, onError,
  schedule = (callback, delay) => window.setTimeout(callback, delay),
  cancelTimer = (timer) => window.clearTimeout(timer),
}) {
  let cancelled = false;
  let timer = null;
  const controller = typeof AbortController === "undefined" ? null : new AbortController();
  const delayFor = (value) => Math.max(500, Math.min(Number(value) || 2000, 10000));
  const tick = async () => {
    if (cancelled) return;
    try {
      const current = await poll({ signal: controller?.signal });
      if (cancelled) return;
      if (REVIEWABLE_MATERIALIZATION_STATUSES.has(current.status)) {
        update?.(current);
        timer = schedule(tick, delayFor(current.retry_after_ms));
      } else await reload();
    } catch (error) {
      if (!cancelled && error?.name !== "AbortError") onError?.(error);
    }
  };
  if (jobId && REVIEWABLE_MATERIALIZATION_STATUSES.has(status)) timer = schedule(tick, delayFor(retryAfterMs));
  return () => {
    cancelled = true;
    if (timer != null) cancelTimer(timer);
    controller?.abort();
  };
}
