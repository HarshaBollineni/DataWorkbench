"""Canonical reporting-period parsing for the Row Completeness engine."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from .models import ReportingGrain


_MONTH = re.compile(r"^(\d{4})[-/]?(\d{1,2})$")
_QUARTER = re.compile(r"^(\d{4})-?Q([1-4])$", re.IGNORECASE)
_HALF = re.compile(r"^(\d{4})-?H([12])$", re.IGNORECASE)
_YEAR = re.compile(r"^(\d{4})$")


@dataclass(frozen=True)
class ParsedPeriod:
    key: str
    ordinal: int


def _from_year_part(year: int, part: int, grain: ReportingGrain) -> ParsedPeriod:
    if not 1 <= year <= 9999:
        raise ValueError("year is outside the supported range")
    if grain == "monthly":
        if not 1 <= part <= 12:
            raise ValueError("month is outside 1..12")
        return ParsedPeriod(f"{year:04d}-{part:02d}", year * 12 + part - 1)
    if grain == "quarterly":
        if not 1 <= part <= 4:
            raise ValueError("quarter is outside 1..4")
        return ParsedPeriod(f"{year:04d}-Q{part}", year * 4 + part - 1)
    if grain == "semiannual":
        if not 1 <= part <= 2:
            raise ValueError("half-year is outside 1..2")
        return ParsedPeriod(f"{year:04d}-H{part}", year * 2 + part - 1)
    return ParsedPeriod(f"{year:04d}", year)


def _from_timestamp(value: Any, grain: ReportingGrain) -> ParsedPeriod:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("period is null")
    if grain == "monthly":
        return _from_year_part(timestamp.year, timestamp.month, grain)
    if grain == "quarterly":
        return _from_year_part(timestamp.year, (timestamp.month - 1) // 3 + 1, grain)
    if grain == "semiannual":
        return _from_year_part(timestamp.year, 1 if timestamp.month <= 6 else 2, grain)
    return _from_year_part(timestamp.year, 1, grain)


def parse_period(value: Any, grain: ReportingGrain) -> ParsedPeriod:
    """Parse one value without guessing a different reporting grain.

    Explicit labels must match the confirmed grain. Calendar dates are accepted
    and mapped into that grain because period columns commonly contain month-end
    or quarter-end dates rather than compact labels.
    """
    if value is None or value is pd.NaT:
        raise ValueError("period is null")
    try:
        is_null = bool(pd.isna(value))
    except (TypeError, ValueError):
        is_null = False
    if is_null:
        raise ValueError("period is null")

    if isinstance(value, pd.Period):
        return _from_timestamp(value.start_time, grain)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return _from_timestamp(value, grain)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("numeric period is not an integer label")
        text = str(int(value))
    else:
        text = str(value).strip()
    if not text:
        raise ValueError("period is blank")

    if grain == "monthly":
        match = _MONTH.fullmatch(text)
        if match:
            return _from_year_part(int(match.group(1)), int(match.group(2)), grain)
        if _QUARTER.fullmatch(text) or _HALF.fullmatch(text) or _YEAR.fullmatch(text):
            raise ValueError("period label is incompatible with monthly grain")
    elif grain == "quarterly":
        match = _QUARTER.fullmatch(text)
        if match:
            return _from_year_part(int(match.group(1)), int(match.group(2)), grain)
        if _HALF.fullmatch(text) or _YEAR.fullmatch(text) or re.fullmatch(r"\d{6}", text):
            raise ValueError("period label is incompatible with quarterly grain")
    elif grain == "semiannual":
        match = _HALF.fullmatch(text)
        if match:
            return _from_year_part(int(match.group(1)), int(match.group(2)), grain)
        if _QUARTER.fullmatch(text) or _YEAR.fullmatch(text) or re.fullmatch(r"\d{6}", text):
            raise ValueError("period label is incompatible with semiannual grain")
    else:
        match = _YEAR.fullmatch(text)
        if match:
            return _from_year_part(int(match.group(1)), 1, grain)
        if _QUARTER.fullmatch(text) or _HALF.fullmatch(text) or re.fullmatch(r"\d{6}", text):
            raise ValueError("period label is incompatible with annual grain")

    # A string with calendar punctuation may be a full observation date.
    # Compact labels that reached this point are rejected rather than guessed.
    if not re.search(r"[-/T:]", text):
        raise ValueError("period is unparseable or incompatible with confirmed grain")
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        raise ValueError("period is unparseable or incompatible with confirmed grain")
    return _from_timestamp(parsed, grain)


def key_from_ordinal(ordinal: int, grain: ReportingGrain) -> str:
    if grain == "monthly":
        year, part = divmod(ordinal, 12)
        return _from_year_part(year, part + 1, grain).key
    if grain == "quarterly":
        year, part = divmod(ordinal, 4)
        return _from_year_part(year, part + 1, grain).key
    if grain == "semiannual":
        year, part = divmod(ordinal, 2)
        return _from_year_part(year, part + 1, grain).key
    return _from_year_part(ordinal, 1, grain).key
