"""Run an ExpectationSuite over a DataFrame and parse GX results into the
unified JSON shape the frontend consumes."""
from __future__ import annotations

from typing import Any

import great_expectations as gx
import pandas as pd

from .context import get_gx_context


def run_suite(df: pd.DataFrame, expectations: list, contract: dict | None = None) -> dict:
    """Validate ``df`` against a list of expectation instances.

    The suite is built and registered inside this function's ephemeral context
    so callers never deal with GX context/registration ordering.
    """
    ctx = get_gx_context()
    suite = ctx.suites.add(gx.ExpectationSuite(name="run"))
    for exp in expectations:
        suite.add_expectation(exp)

    ds = ctx.data_sources.add_pandas("runtime_ds")
    asset = ds.add_dataframe_asset("runtime_asset")
    batch_def = asset.add_batch_definition_whole_dataframe("batch")

    validation_def = ctx.validation_definitions.add(
        gx.ValidationDefinition(name="run", data=batch_def, suite=suite)
    )
    result = validation_def.run(batch_parameters={"dataframe": df})
    return parse_gx_result(result, contract or {})


def _to_str(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def parse_gx_result(result, contract: dict) -> dict:
    """Map GX ExpectationValidationResults to the frontend shape.

    Per-test metadata (id, name, l1_theme, l2_area, criticality, expected) is
    carried on each expectation's ``meta`` dict, set by suite_builder. Anything
    not provided falls back to values derived from the GX config/result.
    """
    out = []
    for r in result.results:
        cfg = r.expectation_config
        meta = dict(getattr(cfg, "meta", None) or {})
        kwargs = dict(getattr(cfg, "kwargs", None) or {})
        observed = (r.result or {}).get("observed_value")
        out.append(
            {
                "id": meta.get("id", cfg.type),
                "name": meta.get("name", cfg.type),
                "l1_theme": meta.get("l1_theme", ""),
                "l2_area": meta.get("l2_area", ""),
                "criticality": meta.get("criticality", "Medium"),
                "column": meta.get("column") or kwargs.get("column") or kwargs.get("column_A", ""),
                "expected": meta.get("expected", ""),
                "actual": _to_str(observed) if observed is not None else "",
                "outcome": "passed" if r.success else "failed",
                "detail": r.result or {},
            }
        )
    return {"success": result.success, "results": out}
