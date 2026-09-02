"""Compatibility alias for T2-D06 period handling."""

import sys

from domains.test_lab.diagnostics.t2_d06_row_completeness import periods as _implementation

sys.modules[__name__] = _implementation
