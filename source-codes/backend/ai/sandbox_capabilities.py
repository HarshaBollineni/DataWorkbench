"""Shared, versioned capability contract for analytical code generation and execution."""
from __future__ import annotations

import ast
import symtable
import platform
from functools import lru_cache
from importlib import metadata

CONTRACT_VERSION = "analytics-sandbox-v1"
BUILTIN_NAMES = frozenset({
    "abs", "min", "max", "sum", "len", "round", "sorted", "range",
    "enumerate", "zip", "map", "filter", "list", "dict", "set", "tuple",
    "float", "int", "str", "bool", "object", "any", "all", "print", "isinstance",
    "Exception", "ValueError", "TypeError", "KeyError", "ZeroDivisionError",
    "divmod", "reversed", "frozenset", "repr", "next", "iter",
})
IMPORT_ROOTS = frozenset({
    "pandas", "numpy", "scipy", "math", "statistics", "datetime", "collections",
    "itertools", "functools", "statsmodels", "sklearn", "dq_tests",
})
BLOCKED_IMPORT_NAMES = frozenset({
    "os", "subprocess", "sys", "importlib", "shutil", "socket", "pathlib",
    "ctypes", "builtins", "pickle", "open",
})
BLOCKED_NAMES = frozenset({
    "__import__", "eval", "exec", "compile", "globals", "locals", "getattr",
    "setattr", "delattr", "vars", "open", "input",
})
BLOCKED_ATTRIBUTES = frozenset({
    "__globals__", "__builtins__", "__subclasses__", "__bases__", "__class__",
    "__mro__", "__code__", "__dict__", "__import__",
})
RCA_INPUT_NAMES = frozenset({"pd", "np", "df", "params"})


@lru_cache(maxsize=1)
def _environment_items() -> tuple:
    # Read package metadata once per process, without importing analytical libraries.
    packages = []
    for name in ("pandas", "numpy", "scipy", "statsmodels", "scikit-learn", "pyarrow"):
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = None
        packages.append((name, version))
    return (platform.python_version(), platform.python_implementation(),
            platform.system(), platform.machine(), tuple(packages))


def execution_metadata() -> dict:
    """Return a fresh, serializable record; never collect paths or environment secrets."""
    python, implementation, system, machine, packages = _environment_items()
    return {"sandbox_contract_version": CONTRACT_VERSION, "runtime_environment": {
        "python": python, "implementation": implementation,
        "system": system, "machine": machine, "packages": dict(packages),
    }}


def public_contract() -> dict:
    return {
        "version": CONTRACT_VERSION,
        "builtins": sorted(BUILTIN_NAMES), "import_roots": sorted(IMPORT_ROOTS),
        "provided_names": sorted(RCA_INPUT_NAMES),
        "blocked_names": sorted(BLOCKED_NAMES),
        "blocked_import_names": sorted(BLOCKED_IMPORT_NAMES),
        "blocked_attributes": sorted(BLOCKED_ATTRIBUTES),
        "constraints": ["No wildcard or relative imports", "No class definitions",
                        "No files, network, reflection or dynamic execution",
                        "RCA additionally enforces loop, output and execution-time limits"],
        "examples": ["pd.cut(df['value'], bins=[0, 10, 100]).astype(object)",
                     "pd.to_datetime(df['date'], errors='coerce').dt.year",
                     "from scipy.stats import spearmanr"],
    }


def validate_capabilities(code: str, provided_names: set[str] | frozenset[str]) -> list[str]:
    """Check policy and unresolved global names using Python's lexical scope rules.

    This is not definite-assignment or branch analysis: a declared name may still
    be uninitialized at runtime, and library attributes/data require execution.
    """
    try:
        tree = ast.parse(code)
        symbols = symtable.symtable(code, "<sandbox>", "exec")
    except SyntaxError as exc:
        return [f"SyntaxError: {exc.msg} (line {exc.lineno})"]
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in IMPORT_ROOTS:
                    errors.append(f"Import of '{alias.name}' is not permitted in the sandbox.")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in IMPORT_ROOTS:
                errors.append(f"Import of '{node.module}' is not permitted in the sandbox.")
            if any(alias.name == "*" for alias in node.names):
                errors.append("Wildcard imports are not permitted; import explicit names.")
            for alias in node.names:
                if alias.name.split(".")[0] in BLOCKED_IMPORT_NAMES:
                    errors.append(f"Import of '{alias.name}' is not permitted in the sandbox.")
        elif isinstance(node, ast.ClassDef):
            errors.append("Class definitions are not permitted; use functions or analytical containers.")
        elif isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRIBUTES:
            errors.append(f"Access to attribute '{node.attr}' is not permitted.")
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            errors.append(f"Use of '{node.id}' is not permitted in the sandbox.")

    tables = []

    def visit(table):
        tables.append(table)
        for child in table.get_children():
            visit(child)

    visit(symbols)
    bound_globals = {
        symbol.get_name() for table in tables for symbol in table.get_symbols()
        if (table is symbols or symbol.is_global())
        and (symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace())
    }
    available = BUILTIN_NAMES | set(provided_names) | bound_globals
    for table in tables:
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if (symbol.is_referenced() and symbol.is_global() and name not in available
                    and name not in BLOCKED_NAMES):
                errors.append(
                    f"Unsupported name '{name}' in {table.get_name()}: define or explicitly "
                    f"import it from an approved library. Sandbox contract: {CONTRACT_VERSION}."
                )
    return list(dict.fromkeys(errors))
