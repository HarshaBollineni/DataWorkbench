const TECHNICAL_KEYS = new Set([
  "artifact_type", "assertion_id", "context_version", "instance_key", "multiplicity",
  "predicate", "sensitivity", "schema_version", "source_artifact_ids", "source_artifacts",
  "governed_references", "identity_inputs", "producer", "payload_media_type",
]);

export function artifactTypeMap(catalog = []) {
  return Object.fromEntries(catalog.map((descriptor) => [descriptor.artifact_type, descriptor]));
}

export function artifactDisplayName(artifactType, types = {}) {
  return types[artifactType]?.display_name || String(artifactType || "Evidence").replaceAll("_", " ");
}

export function isTechnicalField(key) {
  const normalized = String(key || "").toLowerCase();
  return TECHNICAL_KEYS.has(normalized)
    || normalized.endsWith("_fingerprint")
    || normalized.endsWith("_hash");
}

export function partitionArtifactPayload(payload = {}) {
  const user = {};
  const technical = {};
  Object.entries(payload || {}).forEach(([key, value]) => {
    (isTechnicalField(key) ? technical : user)[key] = value;
  });
  return { user, technical };
}
