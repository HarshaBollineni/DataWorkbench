import pandas as pd
import pytest
import json
import subprocess
from types import SimpleNamespace

from ai import code_sandbox, sandbox_capabilities as capabilities
from domains.rca import investigation_agent, investigation_runtime


def test_environment_metadata_is_cached_and_returns_independent_records(monkeypatch):
    capabilities._environment_items.cache_clear()
    calls = []

    def version(name):
        calls.append(name)
        return "test-version"

    monkeypatch.setattr(capabilities.metadata, "version", version)
    try:
        first = capabilities.execution_metadata()
        second = capabilities.execution_metadata()
        assert len(calls) == 6
        assert first == second
        first["runtime_environment"]["packages"]["pandas"] = "changed"
        assert second["runtime_environment"]["packages"]["pandas"] == "test-version"
        json.dumps(second)
    finally:
        capabilities._environment_items.cache_clear()


@pytest.mark.parametrize("status", ["completed", "failed", "timed_out", "rejected", "launch_failed"])
def test_all_runtime_outcomes_retain_environment(status, monkeypatch):
    def run(*args, **kwargs):
        if status == "timed_out":
            raise subprocess.TimeoutExpired("sandbox", 1)
        if status == "launch_failed":
            raise OSError("process unavailable")
        payload = ({"ok": False, "error": "test failure"} if status == "failed"
                   else {"ok": True, "result_obj": {"summary": "done"}})
        return SimpleNamespace(stdout=json.dumps(payload), returncode=0)

    monkeypatch.setattr(investigation_runtime.subprocess, "run", run)
    code = "result = unknown" if status == "rejected" else "result = {}"
    outcome = investigation_runtime.run_generated_code(code, pd.DataFrame(), {})
    assert outcome["status"] == ("failed" if status == "launch_failed" else status)
    assert outcome["sandbox_contract_version"] == capabilities.CONTRACT_VERSION
    assert outcome["runtime_environment"]["packages"]["pandas"] == pd.__version__
    assert outcome["runtime_environment"]["python"]


@pytest.mark.parametrize("code", [
    "result = missing_value + 1",
    "def f(x):\n    return x + unknown\nresult = f(1)",
    "result = [misspelled(x) for x in range(3)]",
    "result = list(range(3))\nresult = x",  # comprehension/locals cannot leak
    "result = [x for x in range(3)]\nresult = x",
    "def f(x=unavailable):\n    return x\nresult = f()",
    "result = type(df)",  # real Python builtin, deliberately not supplied
])
def test_missing_names_are_rejected_before_subprocess(code, monkeypatch):
    def unexpected_execution(*args, **kwargs):
        pytest.fail("Invalid names must be rejected before starting the subprocess")

    monkeypatch.setattr(investigation_runtime.subprocess, "run", unexpected_execution)
    outcome = investigation_runtime.run_generated_code(code, pd.DataFrame(), {})
    assert outcome["status"] == "rejected"
    assert any("Unsupported name" in error for error in outcome["errors"])
    assert outcome["sandbox_contract_version"] == capabilities.CONTRACT_VERSION


@pytest.mark.parametrize("code", [
    "def outer(x):\n    def inner(y):\n        return x + y\n    return inner(2)\nresult = outer(1)",
    "import math as m\nresult = m.sqrt(9)",
    "from math import sqrt as root\nresult = root(9)",
    "result = sum(x * y for x in range(2) for y in range(3))",
    "result = list(map(lambda value: value + 1, [1, 2]))",
    "try:\n    raise ValueError('expected')\nexcept ValueError as error:\n    result = str(error)",
    "def even(x):\n    return x == 0 or odd(x - 1)\ndef odd(x):\n    return x != 0 and even(x - 1)\nresult = even(2)",
    "result = pd.Series([1, None], dtype=object).isna().sum()",
])
def test_valid_scopes_and_imports_match_runtime(code):
    assert capabilities.validate_capabilities(code, capabilities.RCA_INPUT_NAMES) == []
    outcome = code_sandbox.run(code, pd.DataFrame())
    assert outcome["ok"], outcome


@pytest.mark.parametrize("code", [
    "import os\nresult = {}", "from pathlib import Path\nresult = {}",
    "from math import *\nresult = {}", "from . import helper\nresult = {}",
    "result = object.__subclasses__()", "result = eval('1')",
    "from pandas import os as system\nresult = {}",
    "class Example:\n    pass\nresult = {}",
])
def test_restricted_capabilities_are_rejected_by_preflight_and_runtime(code):
    assert investigation_runtime.validate_generated_code(code)
    assert not code_sandbox.run(code, pd.DataFrame())["ok"]


def test_registry_is_the_runtime_builtin_source_and_accepts_explicit_extra_names():
    assert set(code_sandbox._SAFE_BUILTINS) - {"__import__"} == capabilities.BUILTIN_NAMES
    outcome = code_sandbox.run("result = supplied + 1", pd.DataFrame(), {"supplied": 2})
    assert outcome["result_obj"] == 3


def test_codegen_receives_contract_and_execution_artifact_retains_it(monkeypatch):
    received = {}

    def structured(workload, output_type, instructions, payload):
        received.update(payload)
        return {"output": {"python_code": "result = {}"}}

    monkeypatch.setattr(investigation_agent, "_structured", structured)
    investigation_agent.generate_code({"question": "A bounded analysis"})
    contract = received["sandbox_capabilities"]
    assert contract == capabilities.public_contract()
    assert "object" in contract["builtins"]
    assert "open" not in contract["builtins"]
    artifact = investigation_runtime.generated_execution_artifact("result = {}", {})
    assert artifact["sandbox_contract"] == contract


def test_representative_analytics_execute_with_declared_capabilities():
    code = """
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression
years = pd.to_datetime(df['date']).dt.year
bins = pd.cut(df['value'], [0, 2, 5]).astype(object)
groups = df.assign(year=years).groupby('group')['value'].mean()
joined = df.merge(pd.DataFrame({'group': ['A', 'B'], 'weight': [1, 2]}), on='group')
model = LinearRegression().fit(df[['value']], df['target'])
rho = spearmanr(df['value'], df['target']).statistic
result = {'summary': 'Compatibility examples completed.', 'metrics': {
    'rows': len(joined), 'groups': len(groups), 'bins': int(bins.notna().sum()),
    'rho': float(rho), 'slope': float(model.coef_[0])}}
"""
    frame = pd.DataFrame({"date": ["2020-01-01"] * 4, "value": [1, 2, 3, 4],
                          "target": [2, 4, 6, 8], "group": ["A", "A", "B", "B"]})
    outcome = investigation_runtime.run_generated_code(code, frame, {})
    assert outcome["ok"], outcome
    assert outcome["result"]["metrics"]["rows"] == 4
    assert outcome["result"]["metrics"]["rho"] == pytest.approx(1)
    assert outcome["result"]["metrics"]["slope"] == pytest.approx(2)
