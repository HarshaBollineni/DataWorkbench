"""Explicit core-diagnostic dispatch; unknown IDs never fall through."""
from __future__ import annotations

from typing import Any


def adapter(diagnostic_id: int) -> dict[str, Any]:
    if diagnostic_id == 2:
        from domains.test_lab.diagnostics.t1_d02_feature_target_separation import manifest, runner
        return {"manifest": manifest, "runner": runner, "agent": "feature_target_separation_engine",
                "work_count": lambda value: len(value["scope"]["selected_features"])}
    if diagnostic_id == 4:
        from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import manifest, runner
        return {"manifest": manifest, "runner": runner, "agent": "cross_field_engine",
                "work_count": lambda value: len(value["rules"])}
    if diagnostic_id == 6:
        from domains.test_lab.diagnostics.t2_d06_row_completeness import manifest, runner
        return {"manifest": manifest, "runner": runner, "agent": "row_completeness_engine",
                "work_count": lambda value: len(value["rule_ids"])}
    if diagnostic_id == 8:
        from domains.test_lab.diagnostics.t2_d08_value_semantics import manifest, runner
        return {"manifest": manifest, "runner": runner,
                "agent": "value_semantics_engine",
                "work_count": lambda value: len(value["coverage"]["ready_routes"])}
    if diagnostic_id == 11:
        from domains.test_lab.diagnostics.t2_d11_directional_monotonic_consistency import manifest, runner
        return {"manifest": manifest, "runner": runner,
                "agent": "directional_monotonic_consistency_engine",
                "work_count": lambda value: len(value["selected_features"])}
    if diagnostic_id == 14:
        from domains.test_lab.diagnostics.t4_d14_population_stability import manifest, runner
        return {"manifest": manifest, "runner": runner, "agent": "population_stability_index_engine",
                "work_count": lambda value: len(value["selected_features"])}
    raise ValueError(f"no diagnostic adapter is registered for diagnostic {diagnostic_id}")
