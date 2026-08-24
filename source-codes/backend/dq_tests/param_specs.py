"""RETIRED — parameter specs for the 14-test registry (FWK-13, 30 Jul 2026).

The replacement is the threshold semantic layer (FWK-06,
``dq_diagnostics/thresholds.py`` + the ``threshold_settings`` table): defaults
per diagnostic, tunable per dimension and engagement, audited — never code
constants. Import surface preserved for the wizard paths that retire at the
Phase-6 Test Lab cutover.
"""
from __future__ import annotations

PARAM_SPECS: dict[str, dict] = {}


def default_params(test_name: str) -> dict:  # noqa: ARG001 — retired, every test unknown
    return {}


def param_spec(test_name: str) -> dict:  # noqa: ARG001 — retired, every test unknown
    return {}
