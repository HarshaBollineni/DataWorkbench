"""Compatibility alias for governed Data Sourcing AAR projections."""
import sys
from domains.aar import data_sourcing as _implementation
sys.modules[__name__] = _implementation
