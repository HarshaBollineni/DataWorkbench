"""Compatibility alias for T2-D06 API contracts."""

import sys

from domains.test_lab.diagnostics.t2_d06_row_completeness import api as _implementation

sys.modules[__name__] = _implementation
