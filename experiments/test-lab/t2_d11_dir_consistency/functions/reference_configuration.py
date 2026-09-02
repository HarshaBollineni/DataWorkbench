"""Explicit reference configuration for feature directionality diagnostics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ReferenceType(str, Enum):
    """Supported primary reference types."""

    TARGET = "TARGET"
    ANCHOR = "ANCHOR"


class ReferenceOrientation(str, Enum):
    """How increasing reference values relate to credit risk."""

    HIGHER_IS_WORSE = "HIGHER_IS_WORSE"
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"


@dataclass(frozen=True)
class ReferenceConfig:
    """Explicitly configured reference variable and its risk orientation."""

    variable: str
    reference_type: ReferenceType
    orientation: ReferenceOrientation

    def __post_init__(self) -> None:
        if not isinstance(self.variable, str) or not self.variable.strip():
            raise ValueError("variable must be a non-empty string")
        if not isinstance(self.reference_type, ReferenceType):
            raise ValueError("reference_type must be a ReferenceType")
        if not isinstance(self.orientation, ReferenceOrientation):
            raise ValueError("orientation must be a ReferenceOrientation")

        object.__setattr__(self, "variable", self.variable.strip())


class ReferenceSelectionError(ValueError):
    """Raised when metadata and explicit reference configuration are incompatible."""


def select_reference_config(
    column_metadata: Iterable[Mapping[str, Any]],
    *,
    target: ReferenceConfig | None = None,
    anchor: ReferenceConfig | None = None,
) -> ReferenceConfig:
    """Select one explicit reference using saved column-role metadata.

    A configured TARGET takes precedence whenever metadata contains a TARGET role.
    An ANCHOR is considered only when no TARGET role is available. Orientations are
    read only from the supplied configs and are never inferred.
    """

    available_names: set[str] = set()
    target_names: set[str] = set()
    for column in column_metadata:
        if not isinstance(column, Mapping):
            raise ValueError("each column metadata item must be a mapping")
        name = _column_name(column)
        available_names.add(name)
        if _is_target(column.get("role")):
            target_names.add(name)

    if target_names:
        if target is None:
            raise ReferenceSelectionError(
                "A TARGET is available, but an explicit TARGET configuration is required."
            )
        _require_type(target, ReferenceType.TARGET, argument="target")
        if target.variable not in target_names:
            raise ReferenceSelectionError(
                f"Configured TARGET {target.variable!r} is not a TARGET in column metadata."
            )
        return target

    if anchor is None:
        raise ReferenceSelectionError(
            "No TARGET is available and no explicit ANCHOR configuration was provided."
        )

    _require_type(anchor, ReferenceType.ANCHOR, argument="anchor")
    if anchor.variable not in available_names:
        raise ReferenceSelectionError(
            f"Configured ANCHOR {anchor.variable!r} is not present in column metadata."
        )
    return anchor


def _column_name(column: Mapping[str, Any]) -> str:
    name = column.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("each column metadata item must have a non-empty name")
    return name.strip()


def _is_target(role: Any) -> bool:
    value = getattr(role, "value", role)
    return isinstance(value, str) and value.strip().casefold() == "target"


def _require_type(
    config: ReferenceConfig,
    expected: ReferenceType,
    *,
    argument: str,
) -> None:
    if not isinstance(config, ReferenceConfig):
        raise ValueError(f"{argument} must be a ReferenceConfig")
    if config.reference_type is not expected:
        raise ReferenceSelectionError(
            f"{argument} configuration must have reference_type={expected.value}."
        )


__all__ = [
    "ReferenceConfig",
    "ReferenceOrientation",
    "ReferenceSelectionError",
    "ReferenceType",
    "select_reference_config",
]
