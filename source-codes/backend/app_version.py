"""Application version read from the repository release source of truth."""
from pathlib import Path


def _read_version() -> str:
    version_file = Path(__file__).resolve().parents[1] / "VERSION"
    return version_file.read_text(encoding="utf-8").strip()


APP_VERSION = _read_version()
