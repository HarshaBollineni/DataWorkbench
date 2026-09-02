"""Compatibility alias for the T2-D04 governed rules."""
import sys
from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import rules as _implementation
sys.modules[__name__] = _implementation
