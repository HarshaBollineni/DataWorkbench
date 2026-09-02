"""Compatibility alias for the T4-D14 domain runner."""
import sys
from domains.test_lab.diagnostics.t4_d14_population_stability import runner as _implementation
sys.modules[__name__] = _implementation
