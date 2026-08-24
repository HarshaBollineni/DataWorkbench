"""Guided mapping from user dictionaries to the canonical metadata contract."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any, Mapping

import pandas as pd


@dataclass(frozen=True)
class DictionaryField:
    """Describe one canonical dictionary field and its familiar aliases."""

    name: str
    label: str
    description: str
    required: bool
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalDictionary:
    """Normalized dictionary plus the mapping evidence used to create it."""

    frame: pd.DataFrame
    mapping: dict[str, str]
    unused_fields: list[str]


VALUE_VOCABULARIES: dict[str, tuple[str, ...]] = {
    "logical_type": (
        "continuous",
        "categorical",
        "binary",
        "date",
        "period",
        "identifier",
        "free_text",
        "other",
    ),
    "role": (
        "identifier",
        "target",
        "mandatory",
        "optional",
        "unmarked",
        "free_text",
    ),
}

VALUE_ALIASES: dict[str, dict[str, str]] = {
    "logical_type": {
        "numeric": "continuous",
        "number": "continuous",
        "decimal": "continuous",
        "float": "continuous",
        "integer": "continuous",
        "int": "continuous",
        "measure": "continuous",
        "category": "categorical",
        "factor": "categorical",
        "enum": "categorical",
        "nominal": "categorical",
        "boolean": "binary",
        "bool": "binary",
        "flag": "binary",
        "yes/no": "binary",
        "datetime": "date",
        "timestamp": "date",
        "month": "period",
        "quarter": "period",
        "year": "period",
        "reporting period": "period",
        "id": "identifier",
        "key": "identifier",
        "primary key": "identifier",
        "unique identifier": "identifier",
        "free text": "free_text",
        "long text": "free_text",
        "narrative": "free_text",
        "unknown": "other",
    },
    "role": {
        "id": "identifier",
        "key": "identifier",
        "primary key": "identifier",
        "outcome": "target",
        "label": "target",
        "response": "target",
        "dependent variable": "target",
        "required": "mandatory",
        "critical": "mandatory",
        "must have": "mandatory",
        "nullable": "optional",
        "non mandatory": "optional",
        "input": "unmarked",
        "feature": "unmarked",
        "predictor": "unmarked",
        "attribute": "unmarked",
        "free text": "free_text",
        "comments": "free_text",
    },
}

MAX_PROFILE_VALUES = 100


DICTIONARY_FIELDS: tuple[DictionaryField, ...] = (
    DictionaryField(
        "column_name",
        "Column name",
        "Dataset column identified by this dictionary row.",
        True,
        ("variable", "column", "column name", "field", "field name", "variable name"),
    ),
    DictionaryField(
        "description",
        "Description",
        "Business-friendly meaning of the column.",
        False,
        ("description", "business description", "definition", "business label", "label"),
    ),
    DictionaryField(
        "logical_type",
        "Logical type",
        "Analytical type such as continuous, categorical, date, or free text.",
        False,
        ("type", "logical type", "declared type", "data type", "datatype", "format", "data format"),
    ),
    DictionaryField(
        "role",
        "Role",
        "Quality role such as mandatory, optional, identifier, or target.",
        False,
        ("role", "field role", "required flag", "requirement", "usage"),
    ),
    DictionaryField(
        "missing_value_codes",
        "Values treated as missing",
        "Special codes such as 9999, N/A, Unknown, or -1 that represent missingness.",
        False,
        (
            "sentinels missing",
            "sentinel missing",
            "missing codes",
            "missing value codes",
            "null values",
            "null codes",
            "values treated as missing",
        ),
    ),
    DictionaryField(
        "valid_values",
        "Valid values",
        "Allowed values, levels, or domain constraints declared for the column.",
        False,
        (
            "valid values",
            "allowed values",
            "permitted values",
            "domain values",
            "value domain",
            "levels",
            "categories",
        ),
    ),
    DictionaryField(
        "business_context",
        "Comments",
        "Optional notes, dependencies, or guidance for interpreting this column.",
        False,
        (
            "business context",
            "comments",
            "comment",
            "notes",
            "analysis guidance",
            "business rules",
        ),
    ),
)

FIELD_BY_NAME = {field.name: field for field in DICTIONARY_FIELDS}


def _normalized_header(value: Any) -> str:
    """Return a punctuation-insensitive representation used only for matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def suggest_dictionary_mapping(columns: list[Any]) -> dict[str, dict[str, Any]]:
    """Suggest one non-conflicting source header for every canonical field.

    Exact alias matches are high confidence. Conservative fuzzy matches are
    offered as medium-confidence suggestions for user confirmation; they are
    never used when their similarity is below the configured threshold.
    """
    source_columns = [str(column) for column in columns]
    normalized_sources = {
        source: _normalized_header(source) for source in source_columns
    }
    used: set[str] = set()
    suggestions: dict[str, dict[str, Any]] = {}
    for field in DICTIONARY_FIELDS:
        aliases = {_normalized_header(field.name), *map(_normalized_header, field.aliases)}
        exact = next(
            (
                source
                for source in source_columns
                if source not in used and normalized_sources[source] in aliases
            ),
            None,
        )
        if exact is not None:
            suggestions[field.name] = {
                "source_column": exact,
                "confidence": "high",
            }
            used.add(exact)
            continue

        best_source: str | None = None
        best_score = 0.0
        for source in source_columns:
            if source in used:
                continue
            score = max(
                SequenceMatcher(None, normalized_sources[source], alias).ratio()
                for alias in aliases
            )
            if score > best_score:
                best_source, best_score = source, score
        if best_source is not None and best_score >= 0.84:
            suggestions[field.name] = {
                "source_column": best_source,
                "confidence": "medium",
            }
            used.add(best_source)
        else:
            suggestions[field.name] = {
                "source_column": None,
                "confidence": "none",
            }
    return suggestions


def _normalized_value(value: Any) -> str:
    """Return the stable lookup form used for metadata-value aliases."""
    return re.sub(r"\s+", " ", str(value).strip().lower()).replace("-", "_")


def suggest_dictionary_value(value: Any, field: str) -> str | None:
    """Suggest a canonical logical type or role for one supplied value.

    Args:
        value: Distinct value observed in the selected dictionary field.
        field: Either ``logical_type`` or ``role``.

    Returns:
        A canonical value for exact or safe-alias matches, otherwise ``None``.
    """
    normalized = _normalized_value(value)
    if not normalized:
        return None
    vocabulary = VALUE_VOCABULARIES[field]
    if normalized in vocabulary:
        return normalized
    alias_key = normalized.replace("_", " ")
    return VALUE_ALIASES[field].get(alias_key)


def profile_dictionary_values(
    dictionary: pd.DataFrame,
    mapping: Mapping[str, str],
) -> dict[str, Any]:
    """Profile distinct type/role values for a compact confirmation UI.

    Only the two governed metadata fields are inspected. Values are returned with
    counts because they are dictionary vocabulary, not source-dataset values.

    Args:
        dictionary: User-supplied data dictionary.
        mapping: Confirmed canonical-field to source-header mapping.

    Returns:
        Profiles containing bounded distinct values, counts, vocabulary, and safe
        suggestions for the mapped logical-type and role columns.
    """
    profiles: dict[str, Any] = {}
    for field in ("logical_type", "role"):
        source = mapping.get(field)
        if not source or source not in dictionary.columns:
            continue
        text = dictionary[source].fillna("").astype(str).str.strip()
        nonblank = text[text != ""]
        counts = nonblank.value_counts(dropna=False)
        values = [
            {
                "source_value": str(value),
                "count": int(count),
                "suggested_value": suggest_dictionary_value(value, field),
            }
            for value, count in counts.head(MAX_PROFILE_VALUES).items()
        ]
        profiles[field] = {
            "source_column": source,
            "vocabulary": list(VALUE_VOCABULARIES[field]),
            "values": values,
            "distinct_count": int(len(counts)),
            "truncated": len(counts) > MAX_PROFILE_VALUES,
            "blank_count": int((text == "").sum()),
        }
    return profiles


def inspect_dictionary(
    dictionary: pd.DataFrame,
    mapping: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return guided header and governed-value mapping suggestions.

    Args:
        dictionary: User-supplied dictionary to inspect.
        mapping: Optional user-confirmed header mapping. Automatic header
            suggestions are used when it is omitted.

    Returns:
        Header suggestions plus bounded value profiles for logical type and role.
    """
    suggestions = suggest_dictionary_mapping(list(dictionary.columns))
    suggested_mapping = {
        name: item["source_column"]
        for name, item in suggestions.items()
        if item["source_column"] is not None
    }
    profiled_mapping = {
        str(name): str(source)
        for name, source in (mapping or suggested_mapping).items()
        if name in FIELD_BY_NAME and source in dictionary.columns
    }
    mapped = set(profiled_mapping.values())
    return {
        "schema_version": "data-dictionary-v1",
        "row_count": len(dictionary),
        "source_columns": [str(column) for column in dictionary.columns],
        "fields": [
            {
                "name": field.name,
                "label": field.label,
                "description": field.description,
                "required": field.required,
                **suggestions[field.name],
            }
            for field in DICTIONARY_FIELDS
        ],
        "suggested_mapping": suggested_mapping,
        "profiled_mapping": profiled_mapping,
        "value_profiles": profile_dictionary_values(dictionary, profiled_mapping),
        "suggested_value_mapping": {
            field: {
                item["source_value"]: item["suggested_value"]
                for item in profile["values"]
                if item["suggested_value"] is not None
            }
            for field, profile in profile_dictionary_values(
                dictionary, profiled_mapping
            ).items()
        },
        "unused_fields": [
            column for column in map(str, dictionary.columns) if column not in mapped
        ],
    }


def canonicalize_dictionary(
    dictionary: pd.DataFrame,
    mapping: Mapping[str, str] | None = None,
) -> CanonicalDictionary:
    """Apply a confirmed or automatically inferred mapping to a dictionary.

    Args:
        dictionary: User-supplied dictionary with arbitrary column headers.
        mapping: Optional canonical-field to source-header mapping confirmed by
            the caller. When omitted, only the mapper's suggestions are applied.

    Raises:
        ValueError: If mappings use unknown canonical/source fields, reuse one
            source field, or omit the required column identifier.
    """
    source_columns = [str(column) for column in dictionary.columns]
    if mapping is None:
        suggested = suggest_dictionary_mapping(source_columns)
        resolved = {
            name: item["source_column"]
            for name, item in suggested.items()
            if item["source_column"] is not None
        }
    else:
        resolved = {
            str(name): str(source)
            for name, source in mapping.items()
            if source is not None and str(source).strip()
        }

    unknown_targets = sorted(set(resolved) - set(FIELD_BY_NAME))
    if unknown_targets:
        raise ValueError(
            "Unknown canonical dictionary fields: " + ", ".join(unknown_targets)
        )
    missing_sources = sorted(set(resolved.values()) - set(source_columns))
    if missing_sources:
        raise ValueError(
            "Mapped dictionary columns are not present: " + ", ".join(missing_sources)
        )
    duplicates = sorted(
        source for source in set(resolved.values()) if list(resolved.values()).count(source) > 1
    )
    if duplicates:
        raise ValueError(
            "Each dictionary source field may be mapped only once: "
            + ", ".join(duplicates)
        )
    if "column_name" not in resolved:
        raise ValueError(
            "Map one dictionary field to 'Column name', or continue without the dictionary."
        )

    canonical = pd.DataFrame(index=dictionary.index)
    for field in DICTIONARY_FIELDS:
        source = resolved.get(field.name)
        canonical[field.name] = dictionary[source] if source else ""
    unused = [column for column in source_columns if column not in resolved.values()]
    return CanonicalDictionary(canonical, resolved, unused)
