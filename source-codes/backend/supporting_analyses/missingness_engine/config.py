"""Configuration for the missingness assessment engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_TOLERANCE = 0.05
DEFAULT_ROLE_TOLERANCES = {
    "identifier": 0.00,
    "target": 0.00,
    "mandatory": 0.01,
    "unmarked": 0.05,
    "optional": 0.15,
    "free_text": 0.30,
}
DEFAULT_TREE_DEPTH = 3
DEFAULT_LEAF_PURITY = 0.80
DEFAULT_MIN_LEAF_ENRICHMENT = 0.15
DEFAULT_MIN_LEAF_LIFT = 1.25
DEFAULT_MIN_EXPLAINED_MISSING_SHARE = 0.40
DEFAULT_CATEGORICAL_MAX_LEVELS = 20
DEFAULT_CATEGORICAL_RAW_LEVEL_LIMIT = 100
DEFAULT_RARE_CATEGORY_MIN_SHARE = 0.005
DEFAULT_BLOCK_THRESHOLD = 0.95
DEFAULT_BREAK_EXTREME = 0.95
DEFAULT_RANDOM_STATE = 20260719
ANALYSIS_PARAMETER_SCHEMA_VERSION = "analysis-parameters-v1"

# Authoritative HTTP bounds. Direct Python callers retain the broader historical
# AnalysisConfig validation contract for backward compatibility.
API_PARAMETER_BOUNDS: dict[str, tuple[float | int, float | int]] = {
    "tolerance_default": (0.0, 1.0),
    "tree_depth": (2, 5),
    "leaf_purity_min": (0.0, 1.0),
    "min_leaf_enrichment": (0.0, 1.0),
    "min_leaf_lift": (1.0, 20.0),
    "min_explained_missing_share": (0.0, 1.0),
    "categorical_max_levels": (2, 100),
    "categorical_raw_level_limit": (2, 1000),
    "rare_category_min_share": (0.0, 0.25),
    "block_threshold": (0.0, 1.0),
    "break_extreme": (0.5, 1.0),
    "random_state": (0, 2_147_483_647),
}


@dataclass(frozen=True)
class ParameterSpec:
    """Govern one user-editable parameter across API and browser adapters."""

    path: str
    ui_key: str
    group: str
    label: str
    unit: str
    step: float
    description: str
    display_scale: float = 1
    ui_minimum: float | int | None = None
    ui_maximum: float | int | None = None


PARAMETER_GROUPS = (
    (
        "missing_rate_policy",
        "Missing-rate policy",
        "Controls when a column moves beyond the simple rate check.",
    ),
    (
        "character_reliability",
        "Character reliability",
        "Normalizes labels and controls interpretable rare-level pooling.",
    ),
    (
        "explainability_rules",
        "Explainability rules",
        "Defines how concentrated, enriched, and broad a discovered pattern must be.",
    ),
    (
        "pattern_guards",
        "Pattern guards",
        "Controls shared-gap grouping and time-based lineage detection.",
    ),
)


PARAMETER_SPECS = (
    ParameterSpec(
        "tolerance_default", "defaultTolerance", "missing_rate_policy",
        "Default tolerance", "%", 0.5,
        "Maximum missing share for an unmarked column. Used for most fields.",
        display_scale=100,
    ),
    ParameterSpec(
        "role_tolerances.mandatory", "mandatoryTolerance", "missing_rate_policy",
        "Mandatory tolerance", "%", 0.5,
        "Stricter threshold for fields marked mandatory in the dictionary.",
        display_scale=100,
    ),
    ParameterSpec(
        "role_tolerances.optional", "optionalTolerance", "missing_rate_policy",
        "Optional tolerance", "%", 0.5,
        "Allowed missing share for dictionary fields marked optional.",
        display_scale=100,
    ),
    ParameterSpec(
        "role_tolerances.free_text", "freeTextTolerance", "missing_rate_policy",
        "Free-text tolerance", "%", 1,
        "Allowed missing share for optional free-text fields.",
        display_scale=100,
    ),
    ParameterSpec(
        "categorical_max_levels", "categoricalMaxLevels", "character_reliability",
        "Encoded category limit", "levels", 1,
        "Maximum levels emitted after rare-level pooling.",
    ),
    ParameterSpec(
        "categorical_raw_level_limit", "categoricalRawLevelLimit",
        "character_reliability", "Raw category limit", "levels", 1,
        "Fields above this normalized level count are excluded as high-cardinality.",
    ),
    ParameterSpec(
        "rare_category_min_share", "rareCategoryMinShare", "character_reliability",
        "Rare-level floor", "% rows", 0.1,
        "A category below this share is pooled into Other / Rare.",
        display_scale=100,
    ),
    ParameterSpec(
        "tree_depth", "treeDepth", "explainability_rules", "Tree depth", "levels", 1,
        "Maximum split levels. Three is recommended; the supported maximum is five.",
    ),
    ParameterSpec(
        "leaf_purity_min", "leafPurity", "explainability_rules",
        "Strong-leaf purity", "%", 1,
        "Minimum missing share inside a leaf before its rule can explain missingness.",
        display_scale=100, ui_minimum=50,
    ),
    ParameterSpec(
        "min_leaf_enrichment", "minLeafEnrichment", "explainability_rules",
        "Minimum enrichment", "% points", 1,
        "Minimum increase above the column's overall missing rate.",
        display_scale=100,
    ),
    ParameterSpec(
        "min_leaf_lift", "minLeafLift", "explainability_rules",
        "Minimum lift", "× baseline", 0.05,
        "Minimum ratio of leaf missingness to the column's overall missing rate.",
    ),
    ParameterSpec(
        "min_explained_missing_share", "minExplainedMissingShare",
        "explainability_rules", "Missing-case coverage", "%", 1,
        "Minimum share of missing records captured for a strong pattern.",
        display_scale=100,
    ),
    ParameterSpec(
        "block_threshold", "blockThreshold", "pattern_guards",
        "Co-missing similarity", "%", 1,
        "Jaccard similarity required to group columns with shared gaps.",
        display_scale=100, ui_minimum=50,
    ),
    ParameterSpec(
        "break_extreme", "breakExtreme", "pattern_guards",
        "Lineage-break extreme", "%", 1,
        "One side of a period break must be at least this incomplete.",
        display_scale=100,
    ),
    ParameterSpec(
        "random_state", "randomState", "pattern_guards", "Random seed", "seed", 1,
        "Fixes deterministic tie-breaking when tree splits are otherwise equal.",
    ),
)


@dataclass(frozen=True)
class AnalysisConfig:
    """Editable guards with conservative, deterministic defaults."""

    tolerance_default: float = DEFAULT_TOLERANCE
    role_tolerances: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_ROLE_TOLERANCES)
    )
    tree_depth: int = DEFAULT_TREE_DEPTH
    leaf_purity_min: float = DEFAULT_LEAF_PURITY
    min_leaf_enrichment: float = DEFAULT_MIN_LEAF_ENRICHMENT
    min_leaf_lift: float = DEFAULT_MIN_LEAF_LIFT
    min_explained_missing_share: float = DEFAULT_MIN_EXPLAINED_MISSING_SHARE
    categorical_max_levels: int = DEFAULT_CATEGORICAL_MAX_LEVELS
    categorical_raw_level_limit: int = DEFAULT_CATEGORICAL_RAW_LEVEL_LIMIT
    rare_category_min_share: float = DEFAULT_RARE_CATEGORY_MIN_SHARE
    block_threshold: float = DEFAULT_BLOCK_THRESHOLD
    break_extreme: float = DEFAULT_BREAK_EXTREME
    random_state: int = DEFAULT_RANDOM_STATE

    def resolved(self, n_rows: int) -> dict[str, Any]:
        """Return data-volume-dependent values used by one run.

        Args:
            n_rows: Number of observations in the assessed dataset.

        Returns:
            A dictionary containing static settings plus dynamic leaf-size and
            categorical-cardinality guards.
        """
        return {
            **asdict(self),
            "min_leaf_rows": max(30, round(0.01 * n_rows)),
            "min_category_rows": max(2, round(self.rare_category_min_share * n_rows)),
            # Backward-compatible report alias for integrations using the old key.
            "max_cat_levels": self.categorical_max_levels,
        }

    def validate(self) -> None:
        """Validate configuration ranges and interpretability constraints.

        Raises:
            ValueError: If a probability lies outside 0–1, tree depth is not 2–5,
                or another configured guard is invalid.
        """
        for name in (
            "tolerance_default",
            "leaf_purity_min",
            "min_leaf_enrichment",
            "min_explained_missing_share",
            "rare_category_min_share",
            "block_threshold",
            "break_extreme",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.tree_depth not in (2, 3, 4, 5):
            raise ValueError("tree_depth must be between 2 and 5 for interpretability")
        if self.min_leaf_lift < 1:
            raise ValueError("min_leaf_lift must be at least 1")
        if self.categorical_max_levels < 2:
            raise ValueError("categorical_max_levels must be at least 2")
        if self.categorical_raw_level_limit < self.categorical_max_levels:
            raise ValueError(
                "categorical_raw_level_limit must be at least categorical_max_levels"
            )
        for role, tolerance in self.role_tolerances.items():
            if not 0 <= tolerance <= 1:
                raise ValueError(f"role_tolerances['{role}'] must be between 0 and 1")


def _parameter_value(config: AnalysisConfig, path: str) -> float | int:
    """Resolve a catalog path from a concrete configuration."""
    if path.startswith("role_tolerances."):
        return config.role_tolerances.get(path.split(".", 1)[1], config.tolerance_default)
    return getattr(config, path)


def analysis_parameter_catalog(config: AnalysisConfig | None = None) -> dict[str, Any]:
    """Return the versioned parameter contract consumed by user interfaces."""
    resolved = config or AnalysisConfig()
    groups = []
    for group_key, title, description in PARAMETER_GROUPS:
        parameters = []
        for spec in (item for item in PARAMETER_SPECS if item.group == group_key):
            bounds_key = spec.path.split(".", 1)[0]
            minimum, maximum = (
                (0.0, 1.0)
                if spec.path.startswith("role_tolerances.")
                else API_PARAMETER_BOUNDS[bounds_key]
            )
            engine_default = _parameter_value(resolved, spec.path)
            parameters.append({
                "path": spec.path,
                "ui_key": spec.ui_key,
                "label": spec.label,
                "unit": spec.unit,
                "description": spec.description,
                "default": engine_default,
                "minimum": minimum,
                "maximum": maximum,
                "display_scale": spec.display_scale,
                "display_default": engine_default * spec.display_scale,
                "display_minimum": (
                    spec.ui_minimum
                    if spec.ui_minimum is not None
                    else minimum * spec.display_scale
                ),
                "display_maximum": (
                    spec.ui_maximum
                    if spec.ui_maximum is not None
                    else maximum * spec.display_scale
                ),
                "display_step": spec.step,
            })
        groups.append({
            "key": group_key,
            "title": title,
            "description": description,
            "parameters": parameters,
        })
    return {
        "schema_version": ANALYSIS_PARAMETER_SCHEMA_VERSION,
        "defaults": asdict(resolved),
        "groups": groups,
    }
