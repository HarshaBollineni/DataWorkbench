"""Galileo v2 data-quality test registry."""

from .contracts import TestResult
from .registry import TEST_REGISTRY, run_registered

__all__ = ["TEST_REGISTRY", "TestResult", "run_registered"]
