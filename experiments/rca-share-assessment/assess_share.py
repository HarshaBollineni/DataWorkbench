from __future__ import annotations

import argparse
import importlib
import pathlib
import sys

import pandas as pd


def main(share: pathlib.Path) -> None:
    share = share.expanduser().resolve()
    if not share.is_dir():
        raise ValueError(f"RCA share directory does not exist: {share}")
    sys.path.insert(0, str(share))
    checks = importlib.import_module("check_workflow")
    reference = importlib.import_module("reference_library")
    static = importlib.import_module("static_workflow")

    python_files = sorted(share.glob("*.py"))
    for path in python_files:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    periods = [f"2026-{month:02d}" for month in range(1, 13)]
    facilities = {"F1": "EMEA", "F2": "EMEA", "F3": "APAC", "F4": "APAC"}
    frame = pd.DataFrame(
        [
            {"facility_id": facility, "reporting_period": period, "region": region}
            for facility, region in facilities.items()
            for period in periods
            if not (region == "APAC" and period in ("2026-06", "2026-07"))
        ]
    )
    issue = {
        "diagnostic_id": 6,
        "columns": ["facility_id"],
        "criticality": "High",
        "column_details": [{"classification": None}],
    }

    # Keep this assessment offline: retain the deterministic floor and bypass AI review.
    static._review_explained = lambda issue, analyses: {
        "explained_fraction": 0.0,
        "observations": [],
        "residual": "offline assessment",
    }
    static_result = static.run_static(issue, frame, "reporting_period", "region")
    coverage = reference.LIB.facility_period_coverage(
        frame, "facility_id", "reporting_period"
    )

    probes = {
        "network_reader": {
            "code": "result = pd.read_json('https://example.invalid/data.json')",
            "columns_used": [],
        },
        "mutating_input": {
            "code": (
                "df.drop(columns=['region'], inplace=True); "
                "result = {'columns': list(df.columns)}"
            ),
            "columns_used": ["region"],
        },
        "undeclared_column": {
            "code": "result = int(df['not_real'].isna().sum())",
            "columns_used": [],
        },
    }
    validations = {
        name: checks.validate_check(spec, list(frame.columns))
        for name, spec in probes.items()
    }
    mutable = frame.copy()
    mutation_result = checks.run_check(probes["mutating_input"]["code"], mutable)

    print(
        {
            "compiled_python_files": len(python_files),
            "static_decision": static_result["decision"],
            "time_low_buckets": static_result["analyses"]["time"]["concentration"][
                "low_count_buckets"
            ],
            "segment_low_buckets": static_result["analyses"]["segment"][
                "concentration"
            ]["low_count_buckets"],
            "coverage_below_floor": coverage["keys_below_floor"],
            "guard_validations": validations,
            "mutation_ran": mutation_result["ran"],
            "caller_columns_after_run": list(mutable.columns),
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Assess an unpacked RCA share offline.")
    parser.add_argument("share", type=pathlib.Path, help="Path to the unpacked RCA share")
    main(parser.parse_args().share)
