"""Data-dictionary normalization and logical type selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
import unicodedata

import pandas as pd

from .dictionary_mapping import canonicalize_dictionary

USABLE_TYPES = {"categorical", "binary", "continuous", "period", "date"}
KNOWN_TYPES = USABLE_TYPES | {"identifier", "free_text", "other"}
KNOWN_ROLES = {"identifier", "target", "mandatory", "unmarked", "optional", "free_text"}


@dataclass(frozen=True)
class ColumnMetadata:
    """Normalized metadata maps used throughout one analysis run.

    Attributes:
        types: Logical type for each dataset column.
        roles: Data-quality role used to choose a missing-rate tolerance.
        descriptions: Human-readable dictionary descriptions.
        missing_value_codes: Dictionary-approved values that count as missing.
        business_contexts: Optional user context for each column.
        dictionary_state: ``yes``, ``thin``, or ``absent``.
        provisional: Columns whose logical type had to be inferred.
        warnings: Structured dictionary and type-quality warnings.
    """

    types: dict[str, str]
    roles: dict[str, str]
    descriptions: dict[str, str]
    missing_value_codes: dict[str, set[Any]]
    business_contexts: dict[str, str]
    dictionary_state: str
    provisional: set[str]
    warnings: list[dict[str, str]]
    dictionary_mapping: dict[str, str]
    unused_dictionary_fields: list[str]
    dictionary_value_mapping: dict[str, dict[str, str]]

    @property
    def sentinels(self) -> dict[str, set[Any]]:
        """Return the legacy internal name for backward-compatible callers."""
        return self.missing_value_codes


def normalize_category_values(series: pd.Series) -> pd.Series:
    """Normalize categorical labels without changing case semantics.

    Args:
        series: Raw values that may contain mixed Python/string representations.

    Returns:
        Nullable strings normalized with Unicode NFKC and stripped of surrounding
        whitespace. Empty normalized labels remain missing. Letter case is
        deliberately preserved because case can be meaningful in business codes.
    """
    text = series.astype("string")

    def normalize(value: Any) -> Any:
        """Normalize one observed label while preserving pandas missing values."""
        if pd.isna(value):
            return pd.NA
        normalized = unicodedata.normalize("NFKC", str(value)).strip()
        return normalized if normalized else pd.NA

    return text.map(normalize).astype("string")


def parse_missing_value_codes(value: Any) -> set[Any]:
    """Convert one dictionary missing-code cell into comparable values.

    Args:
        value: A scalar, comma-delimited string, or collection of special values.

    Returns:
        Text and numeric representations of each non-blank code. Keeping both
        representations lets a dictionary value match either numeric or text input.
    """
    if value is None or (not isinstance(value, (list, tuple, set)) and pd.isna(value)):
        return set()
    parts = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    parsed: set[Any] = set()
    for part in parts:
        text = str(part).strip()
        if not text:
            continue
        parsed.add(text)
        try:
            parsed.add(float(text))
        except ValueError:
            pass
    return parsed


def parse_sentinels(value: Any) -> set[Any]:
    """Backward-compatible alias for :func:`parse_missing_value_codes`."""
    return parse_missing_value_codes(value)


def infer_logical_type(series: pd.Series, categorical_raw_level_limit: int) -> str:
    """Infer a conservative logical type when dictionary metadata is missing.

    Args:
        series: Source values for one column.
        categorical_raw_level_limit: Maximum normalized distinct values inferred
            as categorical instead of free text.

    Returns:
        A value from the supported logical-type vocabulary.
    """
    distinct = (
        normalize_category_values(series).nunique(dropna=True)
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)
        else series.nunique(dropna=True)
    )
    if pd.api.types.is_bool_dtype(series) or distinct == 2:
        return "binary"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_numeric_dtype(series):
        return "continuous"
    return (
        "categorical"
        if distinct <= max(2, categorical_raw_level_limit)
        else "free_text"
    )


def storage_family(series: pd.Series) -> str:
    """Group a physical pandas dtype for frontend completeness summaries.

    Args:
        series: Loaded values for one source column.

    Returns:
        ``numeric``, ``string``, ``date``, or ``other``. This describes physical
        storage independently of the dictionary's analytical logical type.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return "numeric"
    if (
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
        or isinstance(series.dtype, pd.CategoricalDtype)
    ):
        return "string"
    return "other"


def prepare_metadata(
    dataset: pd.DataFrame,
    dictionary: pd.DataFrame | None,
    categorical_raw_level_limit: int,
    dictionary_mapping: Mapping[str, str] | None = None,
    dictionary_value_mapping: Mapping[str, Mapping[str, str]] | None = None,
) -> ColumnMetadata:
    """Align an optional data dictionary to the columns in a dataset.

    Args:
        dataset: Table being assessed.
        dictionary: Optional user dictionary. Its headers are normalized through
            the guided canonical mapping contract.
        categorical_raw_level_limit: Maximum normalized levels inferred as a
            categorical field rather than free text.
        dictionary_mapping: Optional confirmed canonical-field to source-header
            mapping for the supplied dictionary.
        dictionary_value_mapping: Optional confirmed translations from supplied
            logical-type and role values to the supported vocabularies.

    Returns:
        Normalized metadata with an explicit dictionary quality state.

    Raises:
        ValueError: If a non-empty dictionary cannot be mapped to a column name.
    """
    types: dict[str, str] = {}
    roles: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    missing_value_codes: dict[str, set[Any]] = {}
    business_contexts: dict[str, str] = {}
    provisional: set[str] = set()
    warnings: list[dict[str, str]] = []

    submitted_value_mapping = {
        str(field): {
            str(source).strip(): str(target).strip().lower()
            for source, target in values.items()
            if str(source).strip() and str(target).strip()
        }
        for field, values in (dictionary_value_mapping or {}).items()
        if field in {"logical_type", "role"}
    }
    allowed_targets = {"logical_type": KNOWN_TYPES, "role": KNOWN_ROLES}
    for field, values in submitted_value_mapping.items():
        invalid = sorted(set(values.values()) - allowed_targets[field])
        if invalid:
            raise ValueError(
                f"Unsupported {field} mapping target(s): " + ", ".join(invalid)
            )
    value_mapping_lookup = {
        field: {source.lower(): target for source, target in values.items()}
        for field, values in submitted_value_mapping.items()
    }

    if dictionary is None or dictionary.empty:
        rows: dict[str, dict[str, Any]] = {}
        state = "absent"
        resolved_mapping: dict[str, str] = {}
        unused_dictionary_fields: list[str] = []
    else:
        normalized = canonicalize_dictionary(dictionary, dictionary_mapping)
        dictionary_copy = normalized.frame.fillna("").copy()
        resolved_mapping = normalized.mapping
        unused_dictionary_fields = normalized.unused_fields
        dictionary_copy["column_name"] = (
            dictionary_copy["column_name"].astype(str).str.strip()
        )
        duplicates = dictionary_copy.loc[
            dictionary_copy["column_name"].duplicated(keep=False), "column_name"
        ].unique()
        if len(duplicates):
            raise ValueError(
                "Data dictionary contains duplicate column names: "
                + ", ".join(map(str, duplicates))
            )
        rows = dictionary_copy.set_index("column_name").to_dict("index")
        recommended = {
            "description",
            "logical_type",
            "role",
            "missing_value_codes",
        }
        state = "yes" if recommended.issubset(resolved_mapping) else "thin"

        extra_variables = sorted(set(rows) - {str(column) for column in dataset.columns})
        for variable in extra_variables:
            warnings.append({
                "column": variable,
                "code": "dictionary_variable_not_in_dataset",
                "message": "Dictionary variable is not present in the dataset.",
            })
        if unused_dictionary_fields:
            warnings.append({
                "column": "Data dictionary",
                "code": "unused_dictionary_fields",
                "message": (
                    "Preserved but not used by this analysis: "
                    + ", ".join(unused_dictionary_fields)
                ),
            })

    for column in dataset.columns:
        row = rows.get(str(column), {})
        supplied_type = str(row.get("logical_type", "")).strip()
        declared = value_mapping_lookup.get("logical_type", {}).get(
            supplied_type.lower(), supplied_type.lower()
        )
        if declared not in KNOWN_TYPES:
            supplied = supplied_type
            declared = infer_logical_type(
                dataset[column], categorical_raw_level_limit
            )
            provisional.add(column)
            if supplied:
                warnings.append({
                    "column": str(column),
                    "code": "unsupported_logical_type",
                    "message": (
                        f"Unsupported logical type '{supplied}' was replaced by inferred "
                        f"type '{declared}'."
                    ),
                })
            elif rows:
                warnings.append({
                    "column": str(column),
                    "code": "missing_logical_type",
                    "message": f"Logical type is missing; inferred '{declared}'.",
                })
        types[column] = declared
        raw_role = str(row.get("role", "unmarked")).strip()
        supplied_role = value_mapping_lookup.get("role", {}).get(
            raw_role.lower(), raw_role.lower()
        ) or "unmarked"
        if supplied_role not in KNOWN_ROLES:
            warnings.append({
                "column": str(column),
                "code": "unsupported_role",
                "message": f"Unsupported role '{supplied_role}' was replaced by 'unmarked'.",
            })
            supplied_role = "unmarked"
        roles[column] = supplied_role
        descriptions[column] = str(row.get("description", "")).strip()
        missing_value_codes[column] = parse_missing_value_codes(
            row.get("missing_value_codes", "")
        )
        business_contexts[column] = str(row.get("business_context", "")).strip()

        series = dataset[column]
        if declared == "continuous" and (
            pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)
        ):
            warnings.append({
                "column": str(column),
                "code": "continuous_stored_as_text",
                "message": "Declared continuous field is stored as text; numeric parsing will be required.",
            })
        if (
            column in provisional
            and declared == "continuous"
            and pd.api.types.is_integer_dtype(series)
            and 2 < series.nunique(dropna=True) <= categorical_raw_level_limit
        ):
            warnings.append({
                "column": str(column),
                "code": "possible_numeric_code",
                "message": "Low-cardinality integer was inferred as continuous; confirm whether it is categorical.",
            })

    # A complete-looking dictionary is still thin when one or more columns lack types.
    if (provisional or warnings) and state == "yes":
        state = "thin"
    return ColumnMetadata(
        types=types,
        roles=roles,
        descriptions=descriptions,
        missing_value_codes=missing_value_codes,
        business_contexts=business_contexts,
        dictionary_state=state,
        provisional=provisional,
        warnings=warnings,
        dictionary_mapping=resolved_mapping,
        unused_dictionary_fields=unused_dictionary_fields,
        dictionary_value_mapping=submitted_value_mapping,
    )


def select_period_column(
    requested: str | None,
    types: dict[str, str],
    columns: Iterable[str],
) -> str | None:
    """Resolve the period column used for the pre-tree lineage-break check.

    Args:
        requested: Explicit user-selected period column, if supplied.
        types: Logical type by column name.
        columns: Dataset column names.

    Returns:
        The explicit column, the sole inferred date/period column, or ``None`` when
        automatic selection would be ambiguous.

    Raises:
        ValueError: If ``requested`` is not present in ``columns``.
    """
    column_list = list(columns)
    if requested:
        if requested not in column_list:
            raise ValueError(f"Period column '{requested}' is not in the dataset")
        return requested
    candidates = [column for column in column_list if types[column] in {"period", "date"}]
    return candidates[0] if len(candidates) == 1 else None
