const applicableEvidenceKeys = [
  "exact_frozen", "exact_draft", "exact_automatic", "exact_fine", "safe_mismatch",
];

export function hasRepositoryMatch(rows = []) {
  return rows.some((row) => {
    const evidence = row.bin_route?.evidence || {};
    return applicableEvidenceKeys.some((key) => (evidence[key] || []).length > 0);
  });
}

export function hasBinningWork(manifest, selectedRows = []) {
  return Object.keys(manifest.frozen_bins || {}).length > 0
    || Object.keys(manifest.bin_drafts || {}).length > 0
    || hasRepositoryMatch(selectedRows);
}

export function hasBinningResult(route = {}) {
  return Boolean(route.artifact)
    || applicableEvidenceKeys.some((key) => (route.evidence?.[key] || []).length > 0);
}

export function binningClassification(route = {}, mode) {
  const rejected = (route.evidence?.rejected || []).length;
  if (route.workflow === "reuse_frozen" || route.workflow === "confirm_frozen_reuse") return "Exact reviewed match";
  if (route.workflow === "review_existing_automatic") return "Automatic coarse bins requiring review";
  if (route.workflow === "build_coarse_from_fine") return "Compatible fine-bin foundation";
  if (route.workflow === "bin_mismatch_choice") return "Applicable bins from another Baseline";
  if (route.workflow === "manual_grouping") return "Manual grouping required";
  if (route.workflow === "review_draft") {
    if (route.resolution === "universal_iv_prepared_for_psi") return "Exact universal Diagnostic 2 definition";
    if (route.resolution === "psi_numeric_override") return "PSI-specific numeric override";
    if (route.resolution === "repository_match_prepared_for_review") return "Historical repository evidence prepared for review";
    if (route.resolution === "repository_match_revised") return "PSI-specific revision";
    if (route.resolution === "repository_match_rejected_new") return "Repository candidate rejected; new draft generated";
    if (route.resolution === "no_applicable_repository_match") return "No applicable match; new draft generated";
    return "New diagnostic-specific draft";
  }
  if (mode === "repository" && rejected) return "Invalid or ineligible repository artifact";
  if (["generate_target_aware", "generate_target_free", "generate_constant_guard", "use_universal_iv"].includes(route.workflow)) {
    return mode === "repository" ? "No applicable repository match" : "New Baseline generation pending";
  }
  return "Binning decision pending";
}

export function reviewBinCounts(review, artifact = {}) {
  if (!review) return {
    fine: artifact.fine_bin_count ?? null,
    coarse: artifact.coarse_bin_count ?? null,
  };
  const fine = review.fine_payload?.bins?.length ?? null;
  const payload = review.payload || {};
  const definition = payload.definition || payload;
  const coarse = payload.bins?.length
    ?? (definition.numeric_splits?.length != null ? definition.numeric_splits.length + 1 : null)
    ?? (definition.categorical_groups?.length ?? payload.groups?.length ?? null);
  return { fine: fine ?? artifact.fine_bin_count ?? null, coarse: coarse ?? artifact.coarse_bin_count ?? null };
}
