"""Compatibility alias for T2-D06 domain models."""

import sys

from domains.test_lab.diagnostics.t2_d06_row_completeness import models as _implementation

sys.modules[__name__] = _implementation
