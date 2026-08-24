"""Serializable result types for service and API consumers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd


@dataclass
class MissingnessReport:
    """Portable assessment result shared by every output adapter.

    Attributes:
        preamble: Resolved run configuration and data context.
        columns: One verdict dictionary per source column.
        blocks: Co-missingness block definitions.
        summary: Dataset-level fitness, issues, drivers, and recommended actions.
    """
    preamble: dict[str, Any]
    columns: list[dict[str, Any]]
    blocks: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a recursively serializable dictionary representation."""
        return asdict(self)

    def to_frame(self) -> pd.DataFrame:
        """Project column findings into a review-friendly DataFrame."""
        return pd.DataFrame(self.columns)
