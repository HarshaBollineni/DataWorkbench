"""Run the experimental cell-level value-semantics diagnostic on test fixtures."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .cell_rule_execution import CellExecutionResult, execute_value_semantics
from .kb_loader import load_value_semantics_kb
from .result_summary import summarize_value_semantics


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = EXPERIMENT_ROOT / "inputs" / "test_fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    name = name.lower()
    if name not in {"pd", "lgd", "ead"}:
        raise ValueError("Fixture must be pd, lgd, or ead")
    return {
        "name": name,
        "data": pd.read_csv(FIXTURE_ROOT / f"{name}_data_v0_2.csv"),
        "dictionary": pd.read_csv(FIXTURE_ROOT / f"{name}_dictionary_v0_2.csv", keep_default_na=False),
        "bindings": yaml.safe_load(
            (FIXTURE_ROOT / f"{name}_expected_role_bindings_v0_2.yaml").read_text(encoding="utf-8")
        )["bindings"],
        "declarations": yaml.safe_load(
            (FIXTURE_ROOT / f"{name}_runtime_declarations_v0_2.yaml").read_text(encoding="utf-8")
        ),
        "legacy_expected_claims": pd.read_csv(FIXTURE_ROOT / f"{name}_expected_cell_tags_v0_2.csv"),
    }


def run_fixture(name: str, kb: dict[str, Any] | None = None) -> dict[str, Any]:
    kb = kb or load_value_semantics_kb(EXPERIMENT_ROOT / "kb" / "value_semantics_kb_v0_2.yaml")
    fixture = load_fixture(name)
    execution = execute_value_semantics(
        fixture["data"], fixture["dictionary"], fixture["bindings"],
        fixture["declarations"], kb,
    )
    summaries = summarize_value_semantics(
        fixture["data"], fixture["dictionary"], fixture["bindings"], kb, execution,
    )
    expected = fixture["legacy_expected_claims"].rename(columns={
        "row_id": "row_reference", "column_name": "input_variable", "expected_tag": "tag",
    })
    keys = ["row_reference", "input_variable", "tag"]
    comparison = execution.raw_cell_claims[keys].drop_duplicates().merge(
        expected[keys].drop_duplicates(), on=keys, how="outer", indicator=True,
    )
    comparison["comparison"] = comparison.pop("_merge").map({
        "both": "EXPECTED_AND_GENERATED",
        "left_only": "ADDITIONAL_KB_FINDING",
        "right_only": "EXPECTED_NOT_GENERATED",
    }).astype(str)
    return {"fixture": fixture, "execution": execution, "summaries": summaries,
            "expected_comparison": comparison}


def export_execution(name: str, execution: CellExecutionResult,
                     summaries: dict[str, pd.DataFrame], output_root: str | Path,
                     *, expected_comparison: pd.DataFrame | None = None,
                     extra_frames: dict[str, pd.DataFrame] | None = None,
                     file_version: str = "v0_1",
                     manifest_context: dict[str, Any] | None = None) -> Path:
    """Export a fixture or snapshot execution using the same auditable layout."""
    output = Path(output_root) / name
    output.mkdir(parents=True, exist_ok=True)
    frames = {
        "execution_plan": execution.execution_plan,
        "assessment_ledger": execution.assessment_ledger,
        "raw_cell_claims": execution.raw_cell_claims,
        "resolved_cell_tags": execution.resolved_cell_tags,
        "group_assessments": execution.group_assessments,
        **summaries,
    }
    if expected_comparison is not None:
        frames["expected_comparison"] = expected_comparison
    frames.update(extra_frames or {})
    for name, frame in frames.items():
        frame.to_csv(output / f"{name}_{file_version}.csv", index=False)
    hashes = {}
    for path in sorted(output.glob("*.csv")):
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "knowledge_base_version": "0.2", "execution_version": "0.1",
        "run_name": name, "files": hashes,
        **(manifest_context or {}),
    }
    (output / f"run_manifest_{file_version}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output


def export_run(run: dict[str, Any], output_root: str | Path) -> Path:
    fixture = run["fixture"]
    return export_execution(
        fixture["name"], run["execution"], run["summaries"], output_root,
        expected_comparison=run["expected_comparison"],
        manifest_context={"fixture": fixture["name"], "data_rows": len(fixture["data"]),
                          "reviewed_fixture_bindings": True},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=["pd", "lgd", "ead", "all"], default="all")
    parser.add_argument("--output-dir", type=Path,
                        default=EXPERIMENT_ROOT / "output" / "cell_execution_v0_1")
    args = parser.parse_args()
    names = ("pd", "lgd", "ead") if args.fixture == "all" else (args.fixture,)
    kb = load_value_semantics_kb(EXPERIMENT_ROOT / "kb" / "value_semantics_kb_v0_2.yaml")
    for name in names:
        run = run_fixture(name, kb)
        output = export_run(run, args.output_dir)
        print(f"{name.upper()}\n{run['summaries']['dataset_summary'].to_string(index=False)}")
        print(f"Wrote: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
