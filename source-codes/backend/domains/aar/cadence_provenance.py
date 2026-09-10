"""Pure cadence-provenance checks shared by producer and read-only consumers.

This module deliberately has no repository, database, snapshot-loader, or
producer imports.  Callers supply immutable payloads and already-observed
metadata, which keeps projection validation side-effect free.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_cadence_payload(payload: Mapping[str, Any], *, snapshot: Mapping[str, str], table: str,
                             instance_key: str, dependency_fingerprint: str,
                             expected_refs: set[tuple[str, str, str]], axis_id: str,
                             grouping: list[dict[str, str]], unknown_only: bool = False) -> bool:
    """Validate deterministic cadence payload arithmetic and ownership facts.

    ``expected_refs`` is built by the caller from active, hash-checked source
    metadata.  It is intentionally a pure value contract rather than a
    repository callback.
    """
    try:
        assertion_id = "dsca_" + fingerprint({"schema_version": 1, "context_version": "1", "snapshot": dict(snapshot),
            "subject": {"kind": "table", "table": table}, "predicate": "table.temporal/observed_cadence", "instance_key": instance_key})
        if (payload.get("assertion_id") != assertion_id or payload.get("predicate") != "table.temporal/observed_cadence"
                or payload.get("instance_key") != instance_key or payload.get("dependency_fingerprint") != dependency_fingerprint
                or payload.get("snapshot") != dict(snapshot) or payload.get("subject") != {"kind": "table", "table": table}):
            return False
        evidence = payload.get("evidence")
        if not isinstance(evidence, list) or len(evidence) != 1 or evidence[0].get("kind") != "bounded_scan": return False
        refs = {(ref.get("artifact_id"), ref.get("role"), ref.get("payload_hash"))
                for ref in evidence[0].get("source_refs", []) if isinstance(ref, dict)}
        if refs != expected_refs: return False
        basis, measurements = evidence[0].get("basis"), evidence[0].get("measurements")
        if not isinstance(basis, dict) or basis.get("computation") != "bounded_scan" or not isinstance(measurements, list): return False
        by_name = {entry.get("name"): entry for entry in measurements if isinstance(entry, dict)}
        required = {"distinct_entity_axis_observations", "duplicate_entity_axis_excess_rows", "entities_with_usable_observation",
                    "entities_with_three_or_more_observations", "usable_delta_count", "classified_delta_count",
                    "recognised_interval_class_count", "dominant_interval_delta_count", "second_interval_delta_count"}
        if set(by_name) - required - {"adequately_observed_entity_ratio", "dominant_interval_ratio"} or not required <= set(by_name): return False
        counts = {name: by_name[name].get("count") for name in required}
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()): return False
        exclusions = basis.get("exclusions")
        if (not isinstance(exclusions, dict) or set(exclusions) != {"physical_null", "confirmed_special", "parse_failure"}
                or not isinstance(basis.get("total_count"), int) or not isinstance(basis.get("usable_count"), int)
                or basis["total_count"] < 0 or basis["usable_count"] < 0
                or any(not isinstance(value, int) or value < 0 for value in exclusions.values())
                or basis["total_count"] != basis["usable_count"] + sum(exclusions.values())
                or counts["distinct_entity_axis_observations"] + counts["duplicate_entity_axis_excess_rows"] != basis["usable_count"]
                or counts["entities_with_three_or_more_observations"] > counts["entities_with_usable_observation"]
                or counts["classified_delta_count"] > counts["usable_delta_count"]
                or counts["second_interval_delta_count"] > counts["dominant_interval_delta_count"]): return False
        d, adequate, entities = counts["usable_delta_count"], counts["entities_with_three_or_more_observations"], counts["entities_with_usable_observation"]
        sufficient = d > 0 and 10 * adequate >= 9 * entities
        resolution, claims = payload.get("resolution"), payload.get("claims")
        if not isinstance(resolution, dict) or not isinstance(claims, list): return False
        if resolution.get("status") == "unknown":
            if (not unknown_only and False) or claims or resolution.get("effective_claim_ids") != [] or resolution.get("reason_codes") != ["DSC_R_INSUFFICIENT_BASIS"] or sufficient:
                return False
            expected_evidence_id = "dsce_" + fingerprint({"assertion_id": assertion_id, "kind": "bounded_scan",
                "source_refs": evidence[0].get("source_refs"), "basis": basis, "measurements": measurements})
            return evidence[0].get("evidence_id") == expected_evidence_id
        if unknown_only or not (resolution.get("status") == "observed" and len(claims) == 1
                                and resolution.get("effective_claim_ids") == [claims[0].get("claim_id")]
                                and claims[0].get("authority") == "verified_observation"):
            return False
        value = claims[0].get("value")
        if not isinstance(value, dict) or value.get("axis_id") != axis_id or value.get("grouping") != grouping: return False
        cadence, dominant = value.get("cadence"), counts["dominant_interval_delta_count"]
        if cadence == "regular":
            interval = value.get("observed_interval_class")
            valid = sufficient and dominant * 20 >= 19 * d and isinstance(interval, dict) and set(interval) == {"unit", "step"}
        elif cadence == "mixed":
            valid = (sufficient and dominant * 20 < 19 * d and counts["recognised_interval_class_count"] >= 2
                     and 20 * counts["second_interval_delta_count"] >= d and 20 * counts["classified_delta_count"] >= 19 * d
                     and "observed_interval_class" not in value)
        elif cadence == "irregular":
            mixed = (counts["recognised_interval_class_count"] >= 2 and 20 * counts["second_interval_delta_count"] >= d
                     and 20 * counts["classified_delta_count"] >= 19 * d)
            valid = sufficient and dominant * 20 < 19 * d and not mixed and "observed_interval_class" not in value
        else: return False
        expected_evidence_id = "dsce_" + fingerprint({"assertion_id": assertion_id, "kind": "bounded_scan",
            "source_refs": evidence[0].get("source_refs"), "basis": basis, "measurements": measurements})
        expected_claim_id = "dscc_" + fingerprint({"assertion_id": assertion_id, "authority": "verified_observation", "value": value})
        return bool(valid and evidence[0].get("evidence_id") == expected_evidence_id and claims[0].get("claim_id") == expected_claim_id)
    except Exception:
        return False
