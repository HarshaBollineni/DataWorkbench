"""Validate DataWorkbench documentation and report documentation impact.

The checker is intentionally deterministic: it validates facts that can be
derived from the workspace without calling an external service.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    ".e2e",
    ".runtime",
    ".pytest-state",
    "fixtures",
}
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCED_BLOCK = re.compile(r"^\s*(```|~~~).*?^\s*\1\s*$", re.MULTILINE | re.DOTALL)
ROUTE_PATH = re.compile(r'<Route\s+path=["\']([^"\']+)["\']')
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

REQUIRED_PATHS = (
    "README.md",
    "documentation.ps1",
    "documentation/README.md",
    "documentation/documentation-maintenance.md",
    "documentation/product/aegis-labs-product-spec.md",
    "documentation/history/TSD-pre-0.4.0.md",
    "documentation/releases/0.4.0/requirements-0.4.0.md",
    "documentation/releases/0.5.0/requirements-0.5.0.md",
    "source-codes/README.md",
    "source-codes/USER_GUIDE.md",
    "source-codes/TSD.md",
    "source-codes/TODO.md",
    "source-codes/VERSION",
    "source-codes/backend/requirements.txt",
    "source-codes/docs/README.md",
    "source-codes/docs/development/git-guide.md",
    "source-codes/docs/development/versioning.md",
    "source-codes/docs/diagnostics/psi/phase-plan.md",
    "source-codes/docs/diagnostics/psi/implementation-report.md",
    "source-codes/ui/package.json",
    "source-codes/ui/package-lock.json",
    "source-codes/ui/README.md",
    "tools/check_documentation.py",
    "tools/tests/test_check_documentation.py",
)

CURRENT_DOCS = (
    "source-codes/README.md",
    "source-codes/USER_GUIDE.md",
    "source-codes/TSD.md",
    "source-codes/TODO.md",
    "documentation/product/aegis-labs-product-spec.md",
)

V2_ROUTER_MODULES = (
    "v2.py",
    "sourcing.py",
    "asset_catalogue.py",
    "diagnostics.py",
    "issues.py",
    "framework.py",
    "v2_common.py",
)

RETIRED_CURRENT_ASSERTIONS = (
    re.compile(r"14-test library", re.IGNORECASE),
    re.compile(r"0\.4\.0 notice", re.IGNORECASE),
    re.compile(r"active 0\.4\.0", re.IGNORECASE),
    re.compile(r"three panes in sequence", re.IGNORECASE),
    re.compile(r"Coverage\s*→\s*Run\s*→\s*Findings tabs", re.IGNORECASE),
)

IMPACT_RULES = (
    (re.compile(r"^source-codes/VERSION$"), (
        "source-codes/README.md", "source-codes/USER_GUIDE.md", "source-codes/TSD.md",
        "source-codes/TODO.md", "documentation/product/aegis-labs-product-spec.md",
    )),
    (re.compile(r"^source-codes/ui/src/App\.jsx$"), (
        "source-codes/USER_GUIDE.md", "source-codes/TSD.md",
        "documentation/product/aegis-labs-product-spec.md",
    )),
    (re.compile(r"^source-codes/ui/src/(pages|components)/"), (
        "source-codes/USER_GUIDE.md", "documentation/product/aegis-labs-product-spec.md",
    )),
    (re.compile(r"^source-codes/ui/(package(?:-lock)?\.json|vite\.config\.js)$"), (
        "source-codes/README.md", "source-codes/TSD.md", "source-codes/ui/README.md",
    )),
    (re.compile(r"^source-codes/backend/routers/"), (
        "source-codes/README.md", "source-codes/TSD.md", "source-codes/USER_GUIDE.md",
        "documentation/product/aegis-labs-product-spec.md",
    )),
    (re.compile(r"^source-codes/backend/(system_db|main|app_config|app_version)\.py$"), (
        "source-codes/TSD.md", "documentation/product/aegis-labs-product-spec.md",
    )),
    (re.compile(r"^source-codes/backend/requirements\.txt$"), (
        "source-codes/README.md", "source-codes/TSD.md",
    )),
    (re.compile(r"^source-codes/(setup|app|ci-local|deploy)\.ps1$"), (
        "source-codes/README.md", "source-codes/USER_GUIDE.md", "source-codes/TSD.md",
    )),
)


class DocumentationValidator:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.errors: list[str] = []

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            try:
                name = self.relative(path)
            except ValueError:
                name = str(path)
            self.errors.append(f"{name}: cannot read as UTF-8 ({exc})")
            return None

    def markdown_files(self) -> list[Path]:
        files: list[Path] = []
        scan_roots = (
            self.root,
            self.root / "documentation",
            self.root / "source-codes",
        )
        seen: set[Path] = set()
        for scan_root in scan_roots:
            if not scan_root.exists():
                continue
            for current, dirs, names in os.walk(scan_root, onerror=lambda _exc: None):
                dirs[:] = [
                    name for name in dirs
                    if name not in EXCLUDED_DIRS and not name.startswith("verify-")
                ]
                folder = Path(current)
                for name in names:
                    path = folder / name
                    if path.suffix.lower() == ".md" and path not in seen:
                        seen.add(path)
                        files.append(path)
        return sorted(files)

    def check_required_structure(self) -> None:
        for relative_path in REQUIRED_PATHS:
            if not (self.root / relative_path).is_file():
                self.errors.append(f"{relative_path}: required documentation structure is missing")

    def check_markdown(self) -> None:
        for path in self.markdown_files():
            content = self.read_text(path)
            if content is None:
                continue
            for marker in ("```", "~~~"):
                if len(re.findall(rf"^\s*{re.escape(marker)}", content, re.MULTILINE)) % 2:
                    self.errors.append(f"{self.relative(path)}: unbalanced {marker} code fence")
            # Code samples can legitimately contain expressions such as
            # ``mapping["key"](value)`` that resemble Markdown links.
            link_content = FENCED_BLOCK.sub("", content)
            for match in MARKDOWN_LINK.finditer(link_content):
                target = match.group(1).strip().strip("<>")
                if target.startswith(("http://", "https://", "mailto:", "#", "{")):
                    continue
                target_without_fragment = target.split("#", 1)[0]
                if not target_without_fragment:
                    continue
                if re.match(r"^[A-Za-z]:[\\/]", target_without_fragment):
                    self.errors.append(
                        f"{self.relative(path)}: local Markdown link must be relative {target!r}"
                    )
                    continue
                resolved = (path.parent / unquote(target_without_fragment)).resolve()
                if not resolved.exists():
                    self.errors.append(
                        f"{self.relative(path)}: broken relative link {target!r}"
                    )

    def check_versions(self) -> None:
        version_path = self.root / "source-codes/VERSION"
        package_path = self.root / "source-codes/ui/package.json"
        if not version_path.is_file() or not package_path.is_file():
            return
        version_text = self.read_text(version_path)
        if version_text is None:
            return
        version = version_text.strip()
        if not SEMVER.fullmatch(version):
            self.errors.append("source-codes/VERSION: expected semantic version x.y.z")
            return
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.errors.append(f"source-codes/ui/package.json: invalid JSON ({exc})")
            return
        if package.get("version") != version:
            self.errors.append(
                "source-codes/ui/package.json: version does not match source-codes/VERSION "
                f"({package.get('version')!r} != {version!r})"
            )

        expected_version_markers = {
            "source-codes/README.md": f"Current release: {version}",
            "source-codes/USER_GUIDE.md": f"Application version:** {version}",
            "source-codes/TSD.md": f"Application version:** {version}",
            "source-codes/TODO.md": f"Application version:** {version}",
            "documentation/product/aegis-labs-product-spec.md": f"Archimedes, v{version}",
        }
        for relative_path, marker in expected_version_markers.items():
            path = self.root / relative_path
            if not path.is_file():
                continue
            content = self.read_text(path)
            if content is not None and marker not in content:
                self.errors.append(f"{relative_path}: missing current version marker {marker!r}")

    def check_current_documents(self) -> None:
        for relative_path in CURRENT_DOCS:
            path = self.root / relative_path
            if not path.is_file():
                continue
            content = self.read_text(path)
            if content is None:
                continue
            if relative_path != "source-codes/README.md" and "**Status:** Current" not in content:
                self.errors.append(f"{relative_path}: current-document status marker is missing")
            for pattern in RETIRED_CURRENT_ASSERTIONS:
                match = pattern.search(content)
                if match:
                    line = content.count("\n", 0, match.start()) + 1
                    self.errors.append(
                        f"{relative_path}:{line}: retired current-state assertion {match.group(0)!r}"
                    )

    def check_routes(self) -> None:
        app_path = self.root / "source-codes/ui/src/App.jsx"
        design_path = self.root / "source-codes/TSD.md"
        if not app_path.is_file() or not design_path.is_file():
            return
        app = self.read_text(app_path)
        design = self.read_text(design_path)
        if app is None or design is None:
            return
        routes = {route for route in ROUTE_PATH.findall(app) if route != "*"}
        for route in sorted(routes):
            if f"`{route}`" not in design:
                self.errors.append(f"source-codes/TSD.md: UI route {route!r} is undocumented")

    def check_router_boundary(self) -> None:
        router_root = self.root / "source-codes/backend/routers"
        docs = []
        for relative_path in ("source-codes/README.md", "source-codes/TSD.md"):
            content = self.read_text(self.root / relative_path)
            if content is not None:
                docs.append(content)
        combined = "\n".join(docs)
        for filename in V2_ROUTER_MODULES:
            path = router_root / filename
            if not path.is_file():
                self.errors.append(f"source-codes/backend/routers/{filename}: expected v2 router module is missing")
            if f"routers/{filename}" not in combined:
                self.errors.append(f"source-codes/README.md or TSD.md: routers/{filename} is undocumented")

    def check_dependency_manifests(self) -> None:
        requirements = self.root / "source-codes/backend/requirements.txt"
        package_lock = self.root / "source-codes/ui/package-lock.json"
        content = self.read_text(requirements) if requirements.is_file() else None
        if content is not None:
            normalized = content.lower().replace("_", "-")
            for dependency in ("fastapi", "uvicorn", "pytest"):
                if not re.search(rf"^{re.escape(dependency)}(?:\[.*?\])?\s*[<>=~!]", normalized, re.MULTILINE):
                    self.errors.append(
                        f"source-codes/backend/requirements.txt: required dependency {dependency!r} is missing"
                    )
        if package_lock.is_file():
            try:
                json.loads(package_lock.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.errors.append(f"source-codes/ui/package-lock.json: invalid JSON ({exc})")

    def validate(self) -> int:
        self.check_required_structure()
        self.check_markdown()
        self.check_versions()
        self.check_current_documents()
        self.check_routes()
        self.check_router_boundary()
        self.check_dependency_manifests()
        if self.errors:
            print("Documentation validation failed:")
            for error in sorted(set(self.errors)):
                print(f"- {error}")
            return 1
        print("Documentation validation passed.")
        return 0


def normalize_changed_path(root: Path, raw_path: str) -> str:
    path = Path(raw_path)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix().removeprefix("./")


def documentation_impact(root: Path, changed_paths: list[str]) -> int:
    if not changed_paths:
        print("Provide at least one changed path after --impact.")
        return 2
    recommendations: dict[str, set[str]] = {}
    for raw_path in changed_paths:
        changed = normalize_changed_path(root, raw_path)
        owners: set[str] = set()
        for pattern, documents in IMPACT_RULES:
            if pattern.search(changed):
                owners.update(documents)
        if changed.endswith(".md"):
            owners.add(changed)
        recommendations[changed] = owners

    print("Documentation impact review:")
    for changed, owners in recommendations.items():
        print(f"- {changed}")
        if owners:
            for document in sorted(owners):
                print(f"    review: {document}")
        else:
            print("    no mapped owner; apply the maintenance policy manually")
    print("Impact output is advisory; run the documentation check before completion.")
    return 0


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate DataWorkbench documentation.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="workspace root (defaults to the parent of tools/)",
    )
    parser.add_argument(
        "--impact",
        nargs="*",
        metavar="PATH",
        help="report current documents that should be reviewed for changed paths",
    )
    return parser.parse_args()


def main() -> int:
    options = arguments()
    if options.impact is not None:
        return documentation_impact(options.root, options.impact)
    return DocumentationValidator(options.root).validate()


if __name__ == "__main__":
    sys.exit(main())
