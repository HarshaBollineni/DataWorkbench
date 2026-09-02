"""Compatibility alias for governed RCA analysis helpers."""
import sys
from domains.rca import analysis_helpers as _implementation
sys.modules[__name__] = _implementation
