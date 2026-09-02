"""Compatibility alias for governed AAR artifact types."""
import sys
from domains.aar import types as _implementation
sys.modules[__name__] = _implementation
