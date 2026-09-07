"""Deterministically fan confirmed semantic roles out to rule entries."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def route_roles_to_rules(
    confirmed_roles: Iterable[str],
    kb: Mapping[str, Any],
    *,
    available_roles: Iterable[str] | None = None,
    declarations: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    confirmed = set(confirmed_roles)
    available = set(available_roles if available_roles is not None else confirmed)
    supplied_declarations = set((declarations or {}).keys())
    rows: list[dict[str, Any]] = []
    for rule in kb["rules"]:
        for entry in rule["entries"]:
            target = entry["target_role"]
            if target not in confirmed:
                continue
            missing_roles = sorted(set(entry.get("indicator_roles", [])) - available)
            required = set(entry.get("required_declarations", [])) | set(entry.get("indicator_declarations", []))
            missing_declarations = sorted(required - supplied_declarations)
            rows.append(
                {
                    "matched_role": target,
                    "rule": rule["rule"],
                    "entry": entry["entry"],
                    "routing_status": "READY_FOR_EVALUATION" if not missing_roles and not missing_declarations else "UNSCOPED",
                    "missing_roles": missing_roles,
                    "missing_declarations": missing_declarations,
                    "context_hints": {
                        "model_types": list(entry.get("model_types", rule.get("model_types", []))),
                        "use_cases": list(entry.get("use_cases", rule.get("use_cases", []))),
                        "product_scope": rule.get("product_scope"),
                    },
                }
            )
    return rows
