"""Compatibility alias for T4-D14 bin validation."""
import sys
from domains.test_lab.diagnostics.t4_d14_population_stability import bins as _implementation
sys.modules[__name__] = _implementation
