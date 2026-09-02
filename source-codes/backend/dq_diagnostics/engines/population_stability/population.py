"""Compatibility alias for T4-D14 population splitting."""
import sys
from domains.test_lab.diagnostics.t4_d14_population_stability import population as _implementation
sys.modules[__name__] = _implementation
