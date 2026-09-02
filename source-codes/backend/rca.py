"""Compatibility alias for the RCA domain service."""
import sys
from domains.rca import service as _implementation
sys.modules[__name__] = _implementation
