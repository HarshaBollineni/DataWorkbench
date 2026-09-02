"""Compatibility alias for T1-D02 binning review presentation."""
import sys
from domains.test_lab.diagnostics.t1_d02_feature_target_separation import binning_reviews as _implementation
sys.modules[__name__] = _implementation
