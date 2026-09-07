import pandas as pd

from functions.cell_rule_execution import _sentinel_norms
from functions.column_binding_workflow import resolve_column_bindings
from functions.dictionary_io import overlay_dictionary
from functions.snapshot_input import build_snapshot_input, dictionary_evidence_view


def _schema():
    return {
        "asset_id": "asset-1",
        "tables": [{
            "name": "portfolio",
            "columns": [
                {"name": "CURR_DPD", "description": "Current days past due", "data_type": "integer",
                 "role": "feature", "column_profile_artifact_id": "profile-1",
                 "profile": {"declared_special_values": [-999, -888], "special_values_confirmed": True}},
                {"name": "RPT_QTR", "description": "Reporting quarter", "data_type": "string",
                 "role": "period", "profile": {}},
                {"name": "COMMENT", "description": "Free text", "data_type": "string",
                 "role": "unknown", "profile": {}},
            ],
        }],
    }


def test_snapshot_adapter_filters_roles_and_generates_row_reference():
    result = build_snapshot_input(
        data=pd.DataFrame({"CURR_DPD": [0, -999], "RPT_QTR": ["2024-Q1", "2024-Q2"], "COMMENT": ["a", "b"]}),
        schema=_schema(), table="portfolio", snapshot_id="snapshot-1",
        eligible_roles={"feature", "period"},
    )
    assert result["row_reference_column"] == "__ROW_REFERENCE__"
    assert result["data"]["__ROW_REFERENCE__"].tolist() == ["portfolio:0", "portfolio:1"]
    assert set(result["selected_dictionary"]["column_name"]) == {"CURR_DPD", "RPT_QTR"}
    sentinel = result["dictionary"].set_index("column_name").at["CURR_DPD", "sentinel_value"]
    assert sentinel == "-999|-888"
    assert _sentinel_norms(sentinel) == {"-999", "-888"}


def test_dictionary_off_withholds_optional_evidence_but_preserves_schema_fields():
    result = build_snapshot_input(
        data=pd.DataFrame({"CURR_DPD": [0], "RPT_QTR": ["2024-Q1"], "COMMENT": ["a"]}),
        schema=_schema(), table="portfolio",
    )
    with_dictionary = dictionary_evidence_view(result["selected_dictionary"], include_dictionary_metadata=True)
    without_dictionary = dictionary_evidence_view(result["selected_dictionary"], include_dictionary_metadata=False)
    assert with_dictionary.loc[0, "description"] == "Current days past due"
    assert without_dictionary.loc[0, "description"] == ""
    assert without_dictionary.loc[0, "sentinel_value"] == ""
    assert with_dictionary[["column_name", "data_type", "role"]].equals(
        without_dictionary[["column_name", "data_type", "role"]]
    )


def test_aar_dictionary_is_exposed_but_not_used_when_disabled():
    result = build_snapshot_input(
        data=pd.DataFrame({"CURR_DPD": [0], "RPT_QTR": ["2024-Q1"], "COMMENT": ["a"]}),
        schema=_schema(), table="portfolio", include_dictionary_metadata=False,
    )
    aar = result["aar_sourced_dictionary"].set_index("column_name")
    active = result["dictionary"].set_index("column_name")
    assert aar.at["CURR_DPD", "description"] == "Current days past due"
    assert aar.at["CURR_DPD", "sentinel_value"] == "-999|-888"
    assert active.at["CURR_DPD", "description"] == ""
    assert active.at["CURR_DPD", "sentinel_value"] == ""


def test_binding_workflow_accepts_exact_matches_and_marks_unresolved(prepared):
    results, bindings = resolve_column_bindings([
        {"column_name": "CURR_DPD", "description": "", "data_type": "integer", "role": "feature"},
        {"column_name": "MYSTERY_X", "description": "", "data_type": "float", "role": "feature"},
    ], prepared)
    by_name = results.set_index("column_name")
    assert bindings["CURR_DPD"] == ["arrears_measure"]
    assert by_name.at["CURR_DPD", "decision"] == "MATCH"
    assert by_name.at["MYSTERY_X", "decision"] == "PENDING_ADJUDICATION"
    assert "MYSTERY_X" not in bindings


def test_binding_override_is_execution_ready(prepared):
    results, bindings = resolve_column_bindings(
        [{"column_name": "MYSTERY_X", "description": "", "data_type": "float", "role": "feature"}],
        prepared, binding_overrides={"MYSTERY_X": ["current_balance"]},
    )
    assert bindings["MYSTERY_X"] == ["current_balance"]
    assert results.iloc[0]["decision"] == "REVIEWED_OVERRIDE"


def test_external_dictionary_overlays_metadata_without_losing_table_alignment():
    base = pd.DataFrame([
        {"column_name": "X", "description": "", "data_type": "float", "role": "feature", "sentinel_value": ""},
        {"column_name": "Y", "description": "base", "data_type": "string", "role": "feature", "sentinel_value": ""},
    ])
    overlaid = overlay_dictionary(base, [
        {"name": "X", "description": "Current balance", "role": "Target", "sentinel_value": -999},
    ]).set_index("column_name")
    assert overlaid.at["X", "description"] == "Current balance"
    assert overlaid.at["X", "role"] == "target"
    assert overlaid.at["X", "sentinel_value"] == -999
    assert overlaid.at["Y", "description"] == "base"
