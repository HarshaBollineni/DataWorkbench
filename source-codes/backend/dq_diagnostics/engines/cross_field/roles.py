"""Compatibility alias for the T2-D04 role resolver."""
import sys
from domains.test_lab.diagnostics.t2_d04_cross_field_business_rule import roles as _implementation
sys.modules[__name__] = _implementation
