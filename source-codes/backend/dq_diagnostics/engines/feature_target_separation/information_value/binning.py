"""Compatibility alias for shared Information Value/binning."""
import sys
from domains.test_lab.shared.binning import binning as _implementation
sys.modules[__name__] = _implementation
