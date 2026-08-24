"""Suite-wide storage isolation.

Individual legacy modules still use narrower fixture databases where useful,
but these defaults are installed before test collection imports application
modules. A test that forgets its own sandbox can therefore never open the
developer's live ``backend/system_state.db`` or write into live upload roots.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


# Keep disposable test state outside the source tree. A run can be interrupted
# before pytest_sessionfinish, and Windows can retain file handles until the
# interpreter exits; either case must not leave runtime directories in the repo.
_SUITE_ROOT = Path(tempfile.mkdtemp(prefix="archimedes-pytest-"))
os.environ.setdefault("SYSTEM_DB_PATH", str(_SUITE_ROOT / "system_state.db"))
os.environ.setdefault("UPLOAD_DIR", str(_SUITE_ROOT / "uploads"))
os.environ.setdefault("KB_STORAGE_DIR", str(_SUITE_ROOT / "kb-storage"))
os.environ.setdefault("ANALYSIS_ARTIFACT_DIR", str(_SUITE_ROOT / "analysis-artifacts"))
os.environ.setdefault("ITEM_DB_DIR", str(_SUITE_ROOT / "item-dbs"))
os.environ.setdefault("ARCHIMEDES_ARTIFACT_ORIGIN", "development")


def _remove_suite_root() -> None:
    shutil.rmtree(_SUITE_ROOT, ignore_errors=True)


# Retry after pytest's own shutdown hooks. Windows can keep database handles
# alive past pytest_sessionfinish even though the test session is complete.
atexit.register(_remove_suite_root)


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Best-effort removal of the unique directory created by this process."""
    _remove_suite_root()
