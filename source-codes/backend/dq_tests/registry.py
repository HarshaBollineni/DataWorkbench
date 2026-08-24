"""RETIRED — the 14-test Galileo registry was replaced by the 0.4.0 framework.

FWK-13 (docs/0.4.0/01-disposition.md) retired all fourteen shipped tests on
30 Jul 2026: keep 2 / absorb 3 / replace 1 / defer 5 / delete 3, plus *Key
uniqueness* demoted to a profiling precondition (D-04 —
``dq_diagnostics/profiling_preconditions.py``). The product's diagnostics now
live in the 9-row register (``dq_diagnostics/register.py``, seeded from
``knowledge_base/dq_framework_data.json``); execution engines land per
diagnostic under ``dq_diagnostics/engines/`` (FWK-18), starting with
cross-field (#4) in Phase 6.

This module keeps its import surface so the remaining wizard paths in
``ai/v2/service.py`` stay importable until the Phase-6 Test Lab cutover
deletes them; every entry point behaves as "no such test" — a retired test is
indistinguishable from a test that never existed (FWK-05 / D-17 refusal
semantics):

- the name lists and registries are empty,
- ``run_registered`` raises ``KeyError`` for every name,
- ``get_impl_source`` returns ``""`` for every name (its historical
  unknown-name behaviour),
- ``self_check`` verifies the register is empty.
"""
from __future__ import annotations

from typing import Any, Callable

STAGE1_TESTS: list[str] = []
STAGE2_TESTS: list[str] = []
TEST_NAMES: list[str] = []

TEST_SPECS: dict[str, dict] = {}
TEST_REGISTRY: dict[str, Callable[..., Any]] = {}


def get_impl_source(name: str) -> str:  # noqa: ARG001 — retired, every test unknown
    """No implementation source exists — the registry retired with the framework."""
    return ""


def run_registered(name: str, *_args: Any, **_kwargs: Any) -> Any:
    """Every test name is unknown — the registry retired with the framework."""
    raise KeyError(f"unknown test: {name}")


def self_check() -> bool:
    """The retired registry holds nothing and can execute nothing."""
    return not TEST_NAMES and not TEST_REGISTRY and not TEST_SPECS
