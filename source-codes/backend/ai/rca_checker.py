"""Compatibility alias for the RCA effective-challenge adapter."""
import sys
from domains.rca import effective_challenge as _implementation
sys.modules[__name__] = _implementation
