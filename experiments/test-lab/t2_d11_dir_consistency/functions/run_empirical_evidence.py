"""Run empirical directionality evidence against an approved saved snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

from functions.empirical_directionality import ReferenceKind, analyze_empirical_directionality
from functions.reference_configuration import (
    ReferenceConfig,
    ReferenceOrientation,
    ReferenceType,
    select_reference_config,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = EXPERIMENT_ROOT.parents[2]
BACKEND_ROOT = WORKSPACE_ROOT / "source-codes" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def run(
    snapshot_id: str,
    table: str,
    reference: str,
    *,
    reference_type: ReferenceType = ReferenceType.TARGET,
    reference_kind: ReferenceKind = ReferenceKind.BINARY,
    reference_orientation: ReferenceOrientation = ReferenceOrientation.HIGHER_IS_WORSE,
    run_purpose: str = "diagnostic_evidence",
    allow_anchor_with_target_for_poc: bool = False,
) -> tuple[Path, Path]:
    from analysis_runtime.snapshots import SnapshotLoader
    from functions import extract_saved_schema

    frame = SnapshotLoader().load_table(snapshot_id=snapshot_id, table=table)
    if reference not in frame:
        raise KeyError(f"Reference {reference!r} is not present in the snapshot table.")
    if run_purpose not in {"diagnostic_evidence", "proof_of_concept"}:
        raise ValueError("run_purpose must be diagnostic_evidence or proof_of_concept")

    mapping_path = (
        EXPERIMENT_ROOT / "output" / f"directionality_results_{snapshot_id}_kb_v0_3.csv"
    )
    mappings = pd.read_csv(mapping_path)
    repository = extract_saved_schema._load_repository()
    schema = extract_saved_schema.extract_saved_schema(
        repository, snapshot_id=snapshot_id, table=table
    )
    metadata = {
        column["name"]: column
        for saved_table in schema["tables"]
        for column in saved_table["columns"]
    }
    configured_reference = ReferenceConfig(
        variable=reference,
        reference_type=reference_type,
        orientation=reference_orientation,
    )
    if allow_anchor_with_target_for_poc:
        if run_purpose != "proof_of_concept" or reference_type is not ReferenceType.ANCHOR:
            raise ValueError(
                "allow_anchor_with_target_for_poc requires proof_of_concept and ANCHOR"
            )
        saved_role = str(metadata[reference].get("role") or "").strip().casefold()
        if saved_role == "target":
            raise ValueError("A saved TARGET must not be relabeled as an ANCHOR")
        selected_reference = configured_reference
    else:
        selected_reference = select_reference_config(
            metadata.values(),
            target=(configured_reference if reference_type is ReferenceType.TARGET else None),
            anchor=(configured_reference if reference_type is ReferenceType.ANCHOR else None),
        )

    def confirmed_special_values(column_name: str) -> list[object]:
        profile = metadata[column_name].get("profile") or {}
        if not profile.get("special_values_confirmed", False):
            return []
        return list(profile.get("special_values") or [])

    reference_specials = confirmed_special_values(reference)
    rows: list[dict[str, object]] = []
    full_results: dict[str, object] = {}
    for feature_name in mappings["feature_name"]:
        if feature_name == reference:
            continue
        if feature_name not in frame:
            raise KeyError(f"Feature {feature_name!r} is not present in the snapshot table.")
        result = analyze_empirical_directionality(
            frame[feature_name],
            frame[reference],
            reference_kind=reference_kind,
            feature_missing_values=confirmed_special_values(feature_name),
            reference_missing_values=reference_specials,
        )
        full_results[feature_name] = result.to_dict()
        rows.append(
            {
                "feature_name": feature_name,
                "reference_variable": reference,
                "reference_type": selected_reference.reference_type.value,
                "reference_kind": reference_kind.value,
                "reference_orientation": selected_reference.orientation.value,
                "run_purpose": run_purpose,
                "target_first_rule_bypassed_for_poc": allow_anchor_with_target_for_poc,
                "observed_direction": result.observed_direction.value,
                "status_reason": result.status_reason,
                "n_input": result.n_input,
                "n_paired": result.n_paired,
                "n_dropped": result.n_dropped,
                "n_special_value_dropped": result.n_special_value_dropped,
                "event_value": result.event_value,
                "class_counts": json.dumps(result.class_counts, sort_keys=True),
                "pearson_coefficient": result.pearson.coefficient,
                "pearson_p_value": result.pearson.p_value,
                "pearson_material_direction": result.pearson.material_direction,
                "spearman_coefficient": result.spearman.coefficient,
                "spearman_p_value": result.spearman.p_value,
                "spearman_material_direction": result.spearman.material_direction,
                "regression_model": result.regression.model,
                "regression_coefficient": result.regression.coefficient,
                "regression_intercept": result.regression.intercept,
                "regression_p_value": result.regression.p_value,
                "regression_material_direction": result.regression.material_direction,
                "regression_feature_mean": result.regression.feature_mean,
                "regression_feature_std": result.regression.feature_std,
                "regression_reference_mean": result.regression.reference_mean,
                "regression_reference_std": result.regression.reference_std,
                "binned_realized_bins": result.binned.realized_bins,
                "binned_reference_range": result.binned.reference_range,
                "binned_reference_range_standardized": result.binned.reference_range_standardized,
                "binned_up_steps": result.binned.material_up_steps,
                "binned_down_steps": result.binned.material_down_steps,
                "binned_trend": result.binned.trend,
                "binned_reason": result.binned.reason,
                "binned_detail": json.dumps([item.__dict__ for item in result.binned.bins]),
            }
        )

    output_dir = EXPERIMENT_ROOT / "output"
    csv_path = output_dir / f"empirical_evidence_{snapshot_id}_{reference}_v0_1.csv"
    json_path = output_dir / f"empirical_evidence_{snapshot_id}_{reference}_v0_1.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    json_path.write_text(
        json.dumps(
            {
                "contract_version": "empirical_directionality_evidence_v0_1",
                "snapshot_id": snapshot_id,
                "table": table,
                "reference_variable": reference,
                "reference_type": selected_reference.reference_type.value,
                "reference_kind": reference_kind.value,
                "reference_orientation": selected_reference.orientation.value,
                "run_purpose": run_purpose,
                "target_first_rule_bypassed_for_poc": allow_anchor_with_target_for_poc,
                "features": full_results,
            },
            indent=2,
            default=lambda value: value.value if hasattr(value, "value") else value,
        ),
        encoding="utf-8",
    )
    return csv_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-id", default="item_d982dc3d4a34")
    parser.add_argument("--table", default="Data")
    parser.add_argument("--reference", default="default_12m")
    parser.add_argument(
        "--reference-type",
        choices=[item.value for item in ReferenceType],
        default=ReferenceType.TARGET.value,
    )
    parser.add_argument(
        "--reference-kind",
        choices=[item.value for item in ReferenceKind],
        default=ReferenceKind.BINARY.value,
    )
    parser.add_argument(
        "--reference-orientation",
        choices=[item.value for item in ReferenceOrientation],
        default=ReferenceOrientation.HIGHER_IS_WORSE.value,
    )
    parser.add_argument(
        "--run-purpose",
        choices=["diagnostic_evidence", "proof_of_concept"],
        default="diagnostic_evidence",
    )
    parser.add_argument(
        "--allow-anchor-with-target-for-poc",
        action="store_true",
        help="Explicitly bypass target-first selection only for a proof-of-concept ANCHOR run.",
    )
    args = parser.parse_args()
    csv_path, json_path = run(
        args.snapshot_id,
        args.table,
        args.reference,
        reference_type=ReferenceType(args.reference_type),
        reference_kind=ReferenceKind(args.reference_kind),
        reference_orientation=ReferenceOrientation(args.reference_orientation),
        run_purpose=args.run_purpose,
        allow_anchor_with_target_for_poc=args.allow_anchor_with_target_for_poc,
    )
    print(csv_path)
    print(json_path)


if __name__ == "__main__":
    main()
