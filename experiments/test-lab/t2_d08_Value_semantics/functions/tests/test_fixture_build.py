import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from functions.generate_test_data import DICTIONARY_COLUMNS, generate_all
from functions.dictionary_io import load_dictionary


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def built():
    output = EXPERIMENT_ROOT / ".runtime" / "fixture-build-test"
    counts = generate_all(output)
    return output, counts


def test_fixture_counts(built):
    output, counts = built
    assert counts == {"benchmark_cases": 75, "pd_rows": 1200, "lgd_rows": 400, "ead_rows": 1200}
    assert len(pd.read_csv(output / "role_matching_benchmark_v0_2.csv")) == 75


@pytest.mark.parametrize(("name", "rows"), [("pd", 1200), ("lgd", 400), ("ead", 1200)])
def test_data_dictionary_and_expected_outputs(built, name, rows):
    output, _ = built
    data = pd.read_csv(output / f"{name}_data_v0_2.csv")
    dictionary = pd.read_csv(output / f"{name}_dictionary_v0_2.csv", keep_default_na=False)
    yaml_dictionary = yaml.safe_load((output / f"{name}_dictionary_v0_2.yaml").read_text(encoding="utf-8"))
    bindings = yaml.safe_load((output / f"{name}_expected_role_bindings_v0_2.yaml").read_text(encoding="utf-8"))
    tags = pd.read_csv(output / f"{name}_expected_cell_tags_v0_2.csv")
    assert len(data) == rows
    assert list(dictionary.columns) == DICTIONARY_COLUMNS
    assert "role_notes" not in dictionary.columns
    assert set(dictionary["role"]) <= {"identifier", "period", "target", "feature", "score", "weight", "date", "ignore", "group"}
    assert len(yaml_dictionary["columns"]) == len(dictionary)
    assert bindings["bindings"]
    assert not tags.empty


def test_manifest_is_reproducible(built):
    output, _ = built
    manifest = json.loads((output / "fixture_manifest_v0_2.json").read_text(encoding="utf-8"))
    assert manifest["seed_policy"] == "deterministic formulas; no random state"


def test_csv_and_yaml_dictionary_adapters_agree(built):
    output, _ = built
    csv_rows = load_dictionary(output / "pd_dictionary_v0_2.csv")
    yaml_rows = load_dictionary(output / "pd_dictionary_v0_2.yaml")
    assert [row["name"] for row in csv_rows] == [row["name"] for row in yaml_rows]
    assert csv_rows[0]["role"] == "ignore"


def test_generated_build_matches_committed_fixture_artifacts(built):
    output, _ = built
    committed = EXPERIMENT_ROOT / "inputs" / "test_fixtures"
    generated_names = {path.name for path in output.iterdir() if path.is_file()}
    assert generated_names
    for name in generated_names:
        assert (committed / name).read_bytes() == (output / name).read_bytes(), name
