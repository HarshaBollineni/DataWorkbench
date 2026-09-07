from pathlib import Path

import pytest

from functions.kb_loader import load_terminology, load_value_semantics_kb
from functions.role_matching import prepare_role_matcher


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def kb():
    return load_value_semantics_kb(EXPERIMENT_ROOT / "kb" / "value_semantics_kb_v0_2.yaml")


@pytest.fixture(scope="session")
def terminology():
    return load_terminology(EXPERIMENT_ROOT / "kb" / "credit_risk_abbreviations_v0_3.yaml")


@pytest.fixture(scope="session")
def prepared(kb, terminology):
    return prepare_role_matcher(kb, terminology)
