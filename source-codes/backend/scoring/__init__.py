"""Plan 3 / F4 — deterministic Health Score & Criticality engine (no AI math)."""
from .categories import CATEGORIES, category_of, roll_up_results  # noqa: F401
from .criticality import assign  # noqa: F401
from .health import category_scores, final_score  # noqa: F401
