import assert from "node:assert/strict";
import test from "node:test";

import {
  artifactDisplayName, artifactTypeMap, isTechnicalField, partitionArtifactPayload,
} from "../src/features/aar/artifactPresentation.js";

test("artifact type catalog provides user-friendly labels", () => {
  const types = artifactTypeMap([
    { artifact_type: "feature_profile", display_name: "Feature profile" },
  ]);
  assert.equal(artifactDisplayName("feature_profile", types), "Feature profile");
  assert.equal(artifactDisplayName("future_evidence", types), "future evidence");
});

test("technical identifiers and fingerprints are separated from user evidence", () => {
  const payload = {
    score: 0.91,
    finding: "Income distribution shifted",
    assertion_id: "dsca_123",
    methodology_fingerprint: "abcdef",
    case_id: "case-1",
  };

  const partitioned = partitionArtifactPayload(payload);

  assert.deepEqual(partitioned.user, {
    score: 0.91,
    finding: "Income distribution shifted",
    case_id: "case-1",
  });
  assert.deepEqual(partitioned.technical, {
    assertion_id: "dsca_123",
    methodology_fingerprint: "abcdef",
  });
  assert.equal(isTechnicalField("payload_hash"), true);
  assert.equal(isTechnicalField("run_id"), false);
});
