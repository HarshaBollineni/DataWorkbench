"""Compatibility alias for the AAR domain repository."""
import sys
from domains.aar import repository as _implementation
sys.modules[__name__] = _implementation
