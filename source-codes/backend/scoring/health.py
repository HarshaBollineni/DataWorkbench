"""Plan 3 / F4 — deterministic Health Score (0-100). No AI in the math.

Inputs: a list of test results, each ``{category, passed(bool/0/1), criticality}``.
Criticality weight: High=5, Medium=3, Low=1.
Category score  S_c = sum(pass*w) / sum(w)  in [0,1] over that category's tests.
M = number of selected/active categories (>=1 test). Category weight = 100/M.
Final = sum_c (S_c * 100/M)  in [0,100].
"""
from __future__ import annotations

from .categories import CATEGORY_NAME, category_of

_WEIGHT = {"high": 5, "medium": 3, "low": 1, "critical": 5}


def _w(criticality: str | None) -> int:
    return _WEIGHT.get(str(criticality or "medium").strip().lower(), 3)


def _passed(v) -> int:
    if isinstance(v, str):
        return 1 if v.strip().lower() in {"1", "true", "passed", "pass"} else 0
    return 1 if v else 0


def category_scores(results: list[dict]) -> dict:
    """Per-active-category score in [0,1] keyed by category code, with metadata."""
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        code = r.get("category") or category_of(r.get("test_id") or r.get("l1_theme"))
        by_cat.setdefault(code, []).append(r)

    out: dict[str, dict] = {}
    for code, rows in by_cat.items():
        wsum = sum(_w(r.get("criticality")) for r in rows)
        psum = sum(_passed(r.get("passed")) * _w(r.get("criticality")) for r in rows)
        score = (psum / wsum) if wsum else 0.0
        out[code] = {
            "code": code, "name": CATEGORY_NAME.get(code, code),
            "score": round(score, 4), "n_tests": len(rows),
            "n_passed": sum(_passed(r.get("passed")) for r in rows),
        }
    return out


def final_score(results: list[dict]) -> dict:
    """Final 0-100 health score + per-category breakdown."""
    cats = category_scores(results)
    m = len(cats)
    if m == 0:
        return {"final_score": None, "categories": {}, "active_categories": 0}
    cat_weight = 100.0 / m
    final = sum(c["score"] * cat_weight for c in cats.values())
    for c in cats.values():
        c["weighted_points"] = round(c["score"] * cat_weight, 2)
    return {
        "final_score": round(final, 2),
        "categories": cats,
        "active_categories": m,
        "category_weight": round(cat_weight, 2),
    }
