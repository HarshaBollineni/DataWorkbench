"""Frozen Galileo v2 test contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TestResult:
    status: str
    metric: Optional[float] = None
    threshold: Any = None
    violation_count: int = 0
    evidence: dict = field(default_factory=dict)
    not_runnable_reason: Optional[str] = None
    watch_note: bool = False

    # ``reason`` was the pre-v14 wire name.  Keeping this read-only alias
    # makes old custom snippets and persisted-plan readers harmless while all
    # new framework output uses the explicit contract name.
    @property
    def reason(self) -> Optional[str]:
        return self.not_runnable_reason


def _canon(value: str | None) -> str:
    return str(value or "other").strip().lower().replace("/", " ")


def check_types(columns, classification, allowed, blocked):
    """Return a reason if a selected column is blocked or outside allowed types."""
    if not columns:
        return None
    allowed = [_canon(a) for a in (allowed or ["*"])]
    blocked = {_canon(b) for b in (blocked or [])}
    for col in columns:
        kind = _canon((classification or {}).get(col))
        if kind in blocked:
            return f"{col} is classified as {kind}, which is blocked for this test."
        if "*" not in allowed and kind not in allowed:
            return f"{col} is classified as {kind}; expected one of {', '.join(allowed)}."
    return None


def missing_supporting(supporting, required_keys):
    """Return a reason naming the first missing supporting value."""
    supporting = supporting or {}
    for key in required_keys or []:
        value = supporting.get(key)
        if value is None or value == "" or value == []:
            return f"Missing supporting input: {key}."
    return None
