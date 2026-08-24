from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "check_documentation.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SPEC = importlib.util.spec_from_file_location("check_documentation", MODULE_PATH)
assert SPEC and SPEC.loader
check_documentation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_documentation)


def test_markdown_check_rejects_broken_relative_link() -> None:
    validator = check_documentation.DocumentationValidator(FIXTURES / "broken")
    validator.check_markdown()

    assert validator.errors == ["README.md: broken relative link 'docs/missing.md'"]


def test_markdown_check_accepts_existing_external_and_code_links() -> None:
    validator = check_documentation.DocumentationValidator(FIXTURES / "valid")
    validator.check_markdown()

    assert validator.errors == []


def test_impact_maps_ui_route_changes_to_current_documents(capsys) -> None:
    result = check_documentation.documentation_impact(
        FIXTURES, ["source-codes/ui/src/App.jsx"]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "source-codes/USER_GUIDE.md" in output
    assert "source-codes/TSD.md" in output
    assert "documentation/product/aegis-labs-product-spec.md" in output
