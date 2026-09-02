"""Compatibility alias for T1-D02 ROC/Gini analysis."""
import sys
from domains.test_lab.diagnostics.t1_d02_feature_target_separation.roc_gini import feature_analysis as _implementation
sys.modules[__name__] = _implementation
