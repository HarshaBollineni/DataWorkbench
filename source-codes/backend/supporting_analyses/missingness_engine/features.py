"""Independent-feature selection and transparent tree encoding."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .metadata import USABLE_TYPES, normalize_category_values

OTHER_CATEGORY = "<OTHER / RARE>"


@dataclass(frozen=True)
class PreparedFeature:
    """Target-independent screening evidence and optional encoded values.

    Attributes:
        diagnostic: Stable inclusion or exclusion evidence for one source column.
        encoded: Float-valued tree block when the feature is eligible and encoding
            was requested; otherwise ``None``.
        feature_map: Mapping from encoded column names to the source column.
    """

    diagnostic: dict[str, object]
    encoded: pd.DataFrame | None
    feature_map: dict[str, str]


def _categorical_profile(
    series: pd.Series,
    missing_mask: pd.Series,
    max_levels: int,
    min_category_rows: int,
) -> tuple[pd.Series, dict[str, int]]:
    """Normalize, mode-impute, and pool one categorical predictor.

    Args:
        series: Raw categorical values.
        missing_mask: Missing-code-aware missing indicator for ``series``.
        max_levels: Maximum encoded levels, including a pooled other level.
        min_category_rows: Minimum observed rows required to retain a level.

    Returns:
        Encodable labels and counts describing raw, retained, pooled, and encoded
        levels. Predictor missingness is concealed by mode imputation before use.
    """
    text = normalize_category_values(series.mask(missing_mask))
    counts = text.value_counts(dropna=True)
    ranked = [str(value) for value in counts.index]
    mode = ranked[0] if ranked else "<EMPTY>"
    text = text.fillna(mode)

    retained = [
        value for value in ranked if int(counts.loc[value]) >= min_category_rows
    ]
    if len(retained) < min(2, len(ranked)):
        retained = ranked[: min(2, len(ranked))]

    pooling_needed = len(retained) < len(ranked) or len(ranked) > max_levels
    retained_limit = max_levels - 1 if pooling_needed else max_levels
    retained = retained[: max(1, retained_limit)]
    pooled_levels = max(0, len(ranked) - len(retained))
    if pooled_levels:
        text = text.where(text.isin(retained), OTHER_CATEGORY)

    return text, {
        "raw_levels": len(ranked),
        "retained_levels": len(retained),
        "pooled_levels": pooled_levels,
        "encoded_levels": len(retained) + (1 if pooled_levels else 0),
    }


def _prepare_feature(
    column: str,
    dataset: pd.DataFrame,
    types: dict[str, str],
    missing_masks: pd.DataFrame,
    categorical_raw_level_limit: int,
    categorical_max_levels: int,
    min_category_rows: int,
    *,
    include_encoded: bool,
) -> PreparedFeature:
    """Prepare one candidate once, optionally retaining its encoded tree block."""
    logical_type = types[column]
    diagnostic: dict[str, object] = {
        "column": column,
        "logical_type": logical_type,
        "status": "excluded",
        "reason": "",
    }
    if logical_type not in USABLE_TYPES:
        diagnostic["reason"] = f"logical type '{logical_type}' is not predictor-eligible"
        return PreparedFeature(diagnostic, None, {})

    clean = dataset[column].mask(missing_masks[column])
    categorical = logical_type in {"categorical", "binary"} or (
        logical_type in {"period", "date"}
        and not (
            pd.api.types.is_numeric_dtype(clean)
            or pd.api.types.is_datetime64_any_dtype(clean)
        )
    )
    encoded: pd.DataFrame | None = None
    if categorical:
        text, profile = _categorical_profile(
            dataset[column],
            missing_masks[column],
            categorical_max_levels,
            min_category_rows,
        )
        diagnostic.update(profile)
        if profile["raw_levels"] < 2:
            diagnostic["reason"] = "fewer than two observed normalized levels"
        elif profile["raw_levels"] > categorical_raw_level_limit:
            diagnostic["reason"] = (
                f"{profile['raw_levels']} levels exceed raw categorical limit "
                f"{categorical_raw_level_limit}"
            )
        elif profile["encoded_levels"] < 2:
            diagnostic["reason"] = "rare-level pooling left fewer than two levels"
        else:
            diagnostic["status"] = "included"
            diagnostic["reason"] = (
                f"categorical encoded to {profile['encoded_levels']} level(s)"
            )
            if include_encoded:
                encoded = pd.get_dummies(
                    text,
                    prefix=column,
                    prefix_sep=" == ",
                    dtype=float,
                )
    elif logical_type == "continuous":
        numeric = pd.to_numeric(clean, errors="coerce")
        observed = int(clean.notna().sum())
        parsed = int(numeric.notna().sum())
        diagnostic["numeric_parse_share"] = parsed / observed if observed else 0.0
        if numeric.nunique(dropna=True) < 2:
            diagnostic["reason"] = "fewer than two usable numeric values"
        elif observed and parsed / observed < 0.95:
            diagnostic["reason"] = "fewer than 95% of observed values parse as numeric"
        else:
            diagnostic["status"] = "included"
            diagnostic["reason"] = "usable continuous predictor"
            if include_encoded:
                if pd.api.types.is_datetime64_any_dtype(clean):
                    parsed = pd.to_datetime(clean, errors="coerce")
                    numeric = pd.Series(
                        parsed.astype("int64"),
                        index=dataset.index,
                        dtype=float,
                    )
                    numeric[parsed.isna()] = np.nan
                encoded = numeric.fillna(numeric.median()).to_frame(column)
    elif clean.nunique(dropna=True) < 2:
        diagnostic["reason"] = "fewer than two observed values"
    else:
        diagnostic["status"] = "included"
        diagnostic["reason"] = "usable date or period predictor"
        if include_encoded:
            if pd.api.types.is_datetime64_any_dtype(clean):
                parsed = pd.to_datetime(clean, errors="coerce")
                numeric = pd.Series(
                    parsed.astype("int64"),
                    index=dataset.index,
                    dtype=float,
                )
                numeric[parsed.isna()] = np.nan
            else:
                numeric = pd.to_numeric(clean, errors="coerce")
            encoded = numeric.fillna(numeric.median()).to_frame(column)

    if encoded is None:
        return PreparedFeature(diagnostic, None, {})
    encoded = encoded.astype(float)
    feature_map = {str(encoded_name): column for encoded_name in encoded.columns}
    return PreparedFeature(diagnostic, encoded, feature_map)


def prepare_feature(
    column: str,
    dataset: pd.DataFrame,
    types: dict[str, str],
    missing_masks: pd.DataFrame,
    categorical_raw_level_limit: int,
    categorical_max_levels: int,
    min_category_rows: int,
) -> PreparedFeature:
    """Profile and encode one eligible candidate in a single preparation pass.

    Args:
        column: Source column to prepare as a possible tree predictor.
        dataset: Assessment dataframe containing ``column``.
        types: Resolved logical type by source column.
        missing_masks: Missing-code-aware masks aligned to ``dataset``.
        categorical_raw_level_limit: Raw normalized categorical level ceiling.
        categorical_max_levels: Encoded level ceiling after rare-level pooling.
        min_category_rows: Minimum observations required to retain a category.

    Returns:
        Screening evidence plus an encoded block for an eligible predictor.
    """
    return _prepare_feature(
        column,
        dataset,
        types,
        missing_masks,
        categorical_raw_level_limit,
        categorical_max_levels,
        min_category_rows,
        include_encoded=True,
    )


def profile_feature(
    column: str,
    dataset: pd.DataFrame,
    types: dict[str, str],
    missing_masks: pd.DataFrame,
    categorical_raw_level_limit: int,
    categorical_max_levels: int,
    min_category_rows: int,
) -> dict[str, object]:
    """Profile one candidate without allocating an encoded feature block.

    Parameters match :func:`prepare_feature`; the returned dictionary preserves
    the compatibility contract used by :func:`screen_eligible_features`.
    """
    return _prepare_feature(
        column,
        dataset,
        types,
        missing_masks,
        categorical_raw_level_limit,
        categorical_max_levels,
        min_category_rows,
        include_encoded=False,
    ).diagnostic


def screen_eligible_features(
    target: str,
    dataset: pd.DataFrame,
    types: dict[str, str],
    target_block: set[str],
    missing_masks: pd.DataFrame,
    categorical_raw_level_limit: int,
    categorical_max_levels: int,
    min_category_rows: int,
) -> tuple[list[str], list[dict[str, object]]]:
    """Select predictors and explain every inclusion or exclusion decision.

    Args:
        target: Column whose missingness is being explained.
        dataset: Source table containing candidate predictors.
        types: Logical type by source column.
        target_block: Co-missingness block containing ``target``.
        missing_masks: Missing-code-aware missing indicators.
        categorical_raw_level_limit: Raw normalized level ceiling.
        categorical_max_levels: Encoded level ceiling after rare pooling.
        min_category_rows: Frequency required to retain a category level.

    Returns:
        Eligible columns plus ordered diagnostics for every candidate column.
    """
    features: list[str] = []
    diagnostics: list[dict[str, object]] = []
    for column in dataset.columns:
        logical_type = types[column]
        if column == target:
            diagnostic: dict[str, object] = {
                "column": column,
                "logical_type": logical_type,
                "status": "excluded",
                "reason": "target column",
            }
        elif column in target_block:
            diagnostic = {
                "column": column,
                "logical_type": logical_type,
                "status": "excluded",
                "reason": "target co-missingness block mate",
            }
        else:
            diagnostic = profile_feature(
                column,
                dataset,
                types,
                missing_masks,
                categorical_raw_level_limit,
                categorical_max_levels,
                min_category_rows,
            )

        if diagnostic["status"] == "included":
            features.append(column)
        diagnostics.append(diagnostic)
    return features, diagnostics


def select_eligible_features(
    target: str,
    dataset: pd.DataFrame,
    types: dict[str, str],
    target_block: set[str],
    max_cat_levels: int,
) -> list[str]:
    """Select independent columns that may explain target missingness.

    Args:
        target: Name of the column whose missingness is the tree target.
        dataset: Source table containing candidate predictor columns.
        types: Logical type by column name.
        target_block: Co-missingness block containing ``target``. Every member is
            excluded to avoid predicting a shared gap with the gap itself.
        max_cat_levels: Maximum distinct levels for categorical/binary features.
    Returns:
        Eligible column names in source-column order.
    """
    # Compatibility wrapper for library callers using the original compact API.
    # The analyzer uses ``screen_eligible_features`` directly so it can retain the
    # complete audit trail and missing-code-aware masks.
    masks = dataset.isna()
    features, _ = screen_eligible_features(
        target=target,
        dataset=dataset,
        types=types,
        target_block=target_block,
        missing_masks=masks,
        categorical_raw_level_limit=max_cat_levels,
        categorical_max_levels=max_cat_levels,
        min_category_rows=2,
    )
    return features


def encode_tree_features(
    dataset: pd.DataFrame,
    features: list[str],
    missing_masks: pd.DataFrame,
    types: dict[str, str] | None = None,
    *,
    categorical_max_levels: int = 20,
    min_category_rows: int = 2,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Encode eligible values without exposing predictor missingness as a signal.

    Args:
        dataset: Source table containing ``features``.
        features: Eligible predictor names selected by
            :func:`select_eligible_features`.
        missing_masks: Missing-code-aware indicators aligned to ``dataset``.
        types: Optional logical type mapping. When supplied, dictionary-declared
            categorical fields are encoded categorically even when stored as numbers.
        categorical_max_levels: Maximum dummy levels after pooling.
        min_category_rows: Minimum observed frequency for an unpooled category.

    Returns:
        A numeric tree matrix and a mapping from encoded names to source columns.

    Notes:
        Numeric/date gaps are median-imputed and categorical gaps are mode-imputed.
        This deliberately prevents one column's missing indicator from predicting
        another column's missingness target.
    """
    pieces: list[pd.DataFrame] = []
    feature_map: dict[str, str] = {}
    for column in features:
        clean = dataset[column].mask(missing_masks[column])
        logical_type = (types or {}).get(column)
        categorical = logical_type in {"categorical", "binary"} or (
            logical_type in {"period", "date"}
            and not (
                pd.api.types.is_numeric_dtype(clean)
                or pd.api.types.is_datetime64_any_dtype(clean)
            )
        )
        if categorical:
            text, _ = _categorical_profile(
                dataset[column],
                missing_masks[column],
                categorical_max_levels,
                min_category_rows,
            )
            encoded = pd.get_dummies(
                text,
                prefix=column,
                prefix_sep=" == ",
                dtype=float,
            )
        elif pd.api.types.is_datetime64_any_dtype(clean):
            parsed = pd.to_datetime(clean, errors="coerce")
            numeric = pd.Series(parsed.astype("int64"), index=dataset.index, dtype=float)
            numeric[parsed.isna()] = np.nan
            encoded = numeric.fillna(numeric.median()).to_frame(column)
        elif pd.api.types.is_numeric_dtype(clean):
            numeric = pd.to_numeric(clean, errors="coerce")
            encoded = numeric.fillna(numeric.median()).to_frame(column)
        else:
            numeric = pd.to_numeric(clean, errors="coerce")
            encoded = numeric.fillna(numeric.median()).to_frame(column)

        for encoded_name in encoded.columns:
            feature_map[str(encoded_name)] = column
        pieces.append(encoded.astype(float))

    matrix = pd.concat(pieces, axis=1) if pieces else pd.DataFrame(index=dataset.index)
    return matrix, feature_map
