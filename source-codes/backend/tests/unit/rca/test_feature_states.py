from __future__ import annotations

import pandas as pd

from domains.rca import feature_states


def test_analysis_frame_excludes_confirmed_specials_and_retains_explicit_states():
    frame = pd.DataFrame({"NOI": [1000, None, -999, 4000]})
    snapshot = feature_states.snapshot_from_inventory([{
        "column_name": "NOI", "missing_value_codes_json": [-999],
        "missing_codes_confirmed": True,
    }])

    governed, context = feature_states.analysis_frame(frame, snapshot)

    assert governed["NOI"].dropna().tolist() == [1000, 4000]
    assert governed["__rca_state__NOI__physical_missing"].tolist() == [0, 1, 0, 0]
    special_indicator = context["indicator_columns"]["NOI"]["special: -999"]
    assert governed[special_indicator].tolist() == [0, 0, 1, 0]
    assert context["columns"]["NOI"]["regular_rows"] == 2
    assert context["columns"]["NOI"]["accounted_rows"] == 4
    assert context["columns"]["NOI"]["reconciles"] is True


def test_unconfirmed_inventory_proposal_is_not_applied():
    frame = pd.DataFrame({"NOI": [-999, 1000]})
    snapshot = feature_states.snapshot_from_inventory([{
        "column_name": "NOI", "missing_value_codes_json": [-999],
        "missing_codes_confirmed": False,
    }])

    governed, context = feature_states.analysis_frame(frame, snapshot)

    assert governed["NOI"].tolist() == [-999, 1000]
    assert context["columns"]["NOI"]["governance_status"] == "proposed_unconfirmed"
    assert context["columns"]["NOI"]["proposed_special_values"] == [-999]
    assert context["columns"]["NOI"]["special_rows"] == {}
