"""Explicit core-diagnostic dispatch; unknown IDs never fall through."""
from __future__ import annotations

from typing import Any


def adapter(diagnostic_id: int) -> dict[str, Any]:
    if diagnostic_id == 2:
        from . import manifest_feature_target as manifest
        from . import runner_feature_target as runner
        return {"manifest": manifest, "runner": runner, "agent": "feature_target_separation_engine",
                "work_count": lambda value: len(value["scope"]["selected_features"])}
    if diagnostic_id == 4:
        from . import manifest
        from . import runner_cross_field as runner
        return {"manifest": manifest, "runner": runner, "agent": "cross_field_engine",
                "work_count": lambda value: len(value["rules"])}
    if diagnostic_id == 6:
        from . import manifest_row_completeness as manifest
        from . import runner_row_completeness as runner
        return {"manifest": manifest, "runner": runner, "agent": "row_completeness_engine",
                "work_count": lambda value: len(value["rule_ids"])}
    if diagnostic_id == 14:
        from . import manifest_population_stability as manifest
        from . import runner_population_stability as runner
        return {"manifest": manifest, "runner": runner, "agent": "population_stability_index_engine",
                "work_count": lambda value: len(value["selected_features"])}
    raise ValueError(f"no diagnostic adapter is registered for diagnostic {diagnostic_id}")
