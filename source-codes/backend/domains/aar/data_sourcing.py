"""Deterministic governed projections of retained Data Sourcing profiles."""
from __future__ import annotations

import json
import math
from typing import Any

import system_db as db
from .repository import AnalysisArtifactRepository
from analysis_runtime.contracts import stable_fingerprint


_METHODOLOGY = {"name": "data_sourcing_profile", "version": "4",
                "source": "confirmed_exact_regular_value_profile"}
_IDENTIFIER_TOP_K_LIMIT = 5
_DEFAULT_TOP_K_LIMIT = 50


def _special_value_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = value.strip()
    if value is None:
        return []
    values = list(value) if isinstance(value, (list, tuple, set)) else [value]
    return [item.strip() if isinstance(item, str) else item
            for item in values if not isinstance(item, str) or item.strip()]


def _canonical_special_labels(values: list[Any]) -> set[str]:
    labels: set[str] = set()
    for value in values:
        label = str(value).strip()
        try:
            numeric = float(label)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None and math.isfinite(numeric):
            label = str(int(numeric)) if numeric.is_integer() else str(numeric)
        labels.add(label)
    return labels


def _safe_profile(row: dict[str, Any]) -> dict[str, Any]:
    """Keep the complete exact core profile and bounded categorical evidence."""
    profile = dict(row.get("profile_json") or {})
    if profile.get("calculation_method") != "exact":
        raise ValueError(
            f"{row.get('table_name')}.{row.get('column_name')} has no exact retained profile")
    is_identifier = str(row.get("role") or "").strip().lower() == "identifier"
    top_k_limit = _IDENTIFIER_TOP_K_LIMIT if is_identifier else _DEFAULT_TOP_K_LIMIT
    special_values = _special_value_list(
        row.get("missing_value_codes_json") or row.get("missing_value_codes"))
    confirmed = bool(row.get("missing_codes_confirmed"))
    if special_values and not confirmed:
        raise ValueError(
            f"{row.get('table_name')}.{row.get('column_name')} has unconfirmed special values")
    if special_values and profile.get("profile_basis") != "confirmed_regular_values":
        raise ValueError(
            f"{row.get('table_name')}.{row.get('column_name')} has no confirmed exact regular-value profile")
    if special_values:
        normalized = profile.get("normalized_special_values")
        counts = profile.get("special_value_counts")
        expected_labels = _canonical_special_labels(special_values)
        if (not isinstance(normalized, list) or not isinstance(counts, dict)
                or set(map(str, normalized)) != expected_labels
                or set(map(str, counts)) != expected_labels
                or int(profile.get("special_value_row_count") or 0) != sum(
                    int(count) for count in counts.values())):
            raise ValueError(
                f"{row.get('table_name')}.{row.get('column_name')} has inconsistent "
                "confirmed special-value evidence")
    result = {
        "data_type": row.get("data_type"), "classification": row.get("classification"),
        "role": row.get("role"), "description": row.get("description") or "",
        # Role review is independent from type-confidence provenance. Missing
        # or NULL role_reviewed deliberately remains unreviewed.
        "metadata_reviewed": row.get("role_reviewed") == 1,
        "calculation_method": profile["calculation_method"],
        "profile_basis": profile.get("profile_basis"),
        "special_values_confirmed": confirmed,
        "declared_special_value_count": len(special_values),
        "special_value_count": len(special_values), "special_values": list(special_values),
        "total_count": int(profile.get("total_count") or (
            int(profile.get("non_null_count") or 0) + int(profile.get("null_count") or 0))),
        "non_null_count": int(profile.get("non_null_count") or 0),
        "null_count": int(profile.get("null_count") or 0),
        "distinct_count": int(profile.get("cardinality") or profile.get("unique_count") or 0),
        "null_share": profile.get("null_share"), "min": profile.get("min"), "max": profile.get("max"),
        "mean": profile.get("mean"), "stddev": profile.get("stddev"),
        "histogram": profile.get("histogram") or [], "distinct_set_hash": profile.get("distinct_set_hash"),
        "truncation": {
            "top_k_limit": top_k_limit,
            "identifier_values_retained": is_identifier,
        },
    }
    for key in (
        "declared_special_values", "normalized_special_values",
        "physical_null_count", "physical_null_share", "special_value_row_count",
        "special_value_share", "special_value_counts", "proposed_special_value_row_count",
        "proposed_special_value_counts", "unmatched_declared_special_values",
        "unmatched_special_values",
        "regular_value_count", "regular_value_share", "effective_missing_count",
        "effective_missing_share", "raw_distinct_count", "numeric_value_count",
        "numeric_parse_failure_count", "numeric_parse_failure_share",
        "finite_value_count", "non_finite_count", "non_finite_share", "zero_count",
        "zero_share", "negative_count", "negative_share", "variance", "median",
        "percentiles", "q1", "q3", "iqr", "mad", "skewness", "excess_kurtosis",
        "mode", "mode_count", "mode_share", "distinct_value_ratio",
        "duplicate_value_count", "duplicate_value_share", "date_parse_failure_count",
        "date_parse_failure_share", "period_bounds",
    ):
        if key in profile:
            result[key] = profile[key]
    # Keep a small identifier sample for diagnostics, while categorical
    # features retain enough distribution evidence for useful analysis.
    top_k = profile.get("top_k") or profile.get("top_values") or {}
    result["top_k"] = dict(list(top_k.items())[:top_k_limit])
    return result


def persist_snapshot_profile_artifacts(
    snapshot_id: str,
    *,
    actor: str | None = None,
    artifact_repository: AnalysisArtifactRepository | None = None,
) -> list[str]:
    """Idempotently register profile evidence once a snapshot is active and ready."""
    snapshot = db.query_one("dq_items", item_id=snapshot_id)
    if not snapshot or snapshot.get("snapshot_status") != "active" or snapshot.get("ingest_status") != "ready":
        return []
    asset_id = snapshot.get("dataset_family_id")
    if not asset_id or not db.query_one("dq_assets", asset_id=asset_id):
        return []
    repo = artifact_repository or AnalysisArtifactRepository()
    methodology_fingerprint = stable_fingerprint(_METHODOLOGY)
    dictionary_version = snapshot.get("dictionary_version_id")
    artifacts: list[str] = []
    by_table: dict[str, list[dict[str, Any]]] = {}
    for row in db.query("variable_inventory", item_id=snapshot_id, order_by="table_name, column_name"):
        by_table.setdefault(row["table_name"], []).append(row)
    active_columns: dict[tuple[str | None, str | None], list[Any]] = {}
    for item in repo.list(snapshot_id=snapshot_id, artifact_type="column_profile", status="active"):
        active_columns.setdefault((item.identity.get("table"), item.feature), []).append(item)
    active_tables: dict[str | None, list[Any]] = {}
    for item in repo.list(snapshot_id=snapshot_id, artifact_type="table_profile", status="active"):
        active_tables.setdefault(item.identity.get("table"), []).append(item)
    for table, rows in by_table.items():
        population = stable_fingerprint({"table": table, "population": "all_snapshot_rows"})
        column_ids = []
        for row in rows:
            prior_columns = active_columns.get((table, row["column_name"]), [])
            payload = _safe_profile(row)
            outcome = repo.save(
                payload, artifact_type="column_profile", asset_id=asset_id,
                snapshot_id=snapshot_id, population_fingerprint=population,
                methodology_fingerprint=methodology_fingerprint, scope="universal", table=table,
                feature=row["column_name"], identity_inputs={"dictionary_version_id": dictionary_version,
                "profile_source": "confirmed_exact_regular_value_profile", "profile_schema_version": 4,
                "schema_role": row.get("role"),
                "profile_evidence_fingerprint": stable_fingerprint(payload)}, created_by=actor,
            )
            # The saved schema is a current projection of the immutable Step 3
            # decision. Keep history, but expose exactly one active projection
            # per snapshot/table/column when reviewed metadata changes.
            for prior in prior_columns:
                if prior.artifact_id != outcome.artifact.artifact_id:
                    repo.supersede(prior.artifact_id,
                                   by_artifact_id=outcome.artifact.artifact_id, actor=actor)
            column_ids.append(outcome.artifact.artifact_id); artifacts.append(outcome.artifact.artifact_id)
        table_payload = {"table": table, "column_count": len(rows),
            "row_count": max((int(item.get("total_count") or 0) for item in (_safe_profile(row) for row in rows)), default=0),
            "columns": [row["column_name"] for row in rows], "source": "retained_profile_evidence"}
        prior_tables = active_tables.get(table, [])
        table_outcome = repo.save(table_payload, artifact_type="table_profile", asset_id=asset_id,
            snapshot_id=snapshot_id, population_fingerprint=population, methodology_fingerprint=methodology_fingerprint,
            scope="universal", table=table, source_artifact_ids=tuple(column_ids),
            identity_inputs={"dictionary_version_id": dictionary_version, "profile_schema_version": 4}, created_by=actor)
        for prior in prior_tables:
            if prior.artifact_id != table_outcome.artifact.artifact_id:
                repo.supersede(prior.artifact_id,
                               by_artifact_id=table_outcome.artifact.artifact_id, actor=actor)
        artifacts.append(table_outcome.artifact.artifact_id)
    return artifacts
