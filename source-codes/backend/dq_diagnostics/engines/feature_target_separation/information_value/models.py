"""Compatibility alias for shared binning models."""
import sys
from domains.test_lab.shared.binning import models as _implementation
sys.modules[__name__] = _implementation
