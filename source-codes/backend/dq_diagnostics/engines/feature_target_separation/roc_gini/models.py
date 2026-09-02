"""Compatibility alias for T1-D02 ROC/Gini models."""
import sys
from domains.test_lab.diagnostics.t1_d02_feature_target_separation.roc_gini import models as _implementation
sys.modules[__name__] = _implementation
