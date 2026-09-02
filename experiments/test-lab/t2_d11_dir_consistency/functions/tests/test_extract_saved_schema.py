from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
if str(EXPERIMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_ROOT))

from functions.extract_saved_schema import extract_saved_schema


class StubRepository:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def list(self, **filters):
        return [
            artifact
            for artifact in self.artifacts
            if all(value is None or getattr(artifact, key) == value for key, value in filters.items())
        ]


def test_table_mismatch_reports_available_tables():
    repository = StubRepository(
        [
            SimpleNamespace(
                artifact_type="table_profile",
                asset_id="asset_1",
                snapshot_id="item_1",
                status="active",
                identity={"table": "mr_pd_sample"},
            )
        ]
    )

    with pytest.raises(LookupError) as error:
        extract_saved_schema(repository, snapshot_id="item_1", table="Data")

    assert "table='Data'" in str(error.value)
    assert "Available tables: 'mr_pd_sample'." in str(error.value)


def test_missing_snapshot_does_not_claim_tables_are_available():
    with pytest.raises(LookupError) as error:
        extract_saved_schema(StubRepository([]), snapshot_id="missing", table="Data")

    assert "Available tables" not in str(error.value)
