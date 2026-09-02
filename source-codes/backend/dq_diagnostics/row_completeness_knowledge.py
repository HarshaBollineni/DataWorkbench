"""Compatibility alias for the T2-D06 governed knowledge resolver."""

import sys

from domains.test_lab.diagnostics.t2_d06_row_completeness import knowledge as _implementation

sys.modules[__name__] = _implementation
