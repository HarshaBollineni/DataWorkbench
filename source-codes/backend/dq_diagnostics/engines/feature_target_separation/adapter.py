"""Compatibility alias for the T1-D02 adapter."""
import sys
from domains.test_lab.diagnostics.t1_d02_feature_target_separation import adapter as _implementation
sys.modules[__name__] = _implementation
