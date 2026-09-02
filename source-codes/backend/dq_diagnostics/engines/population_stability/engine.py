"""Compatibility alias for the T4-D14 PSI engine."""
import sys
from domains.test_lab.diagnostics.t4_d14_population_stability import engine as _implementation
sys.modules[__name__] = _implementation
