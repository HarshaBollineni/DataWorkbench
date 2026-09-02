"""Compatibility alias for the T1-D02 domain manifest."""
import sys
from domains.test_lab.diagnostics.t1_d02_feature_target_separation import manifest as _implementation
sys.modules[__name__] = _implementation
