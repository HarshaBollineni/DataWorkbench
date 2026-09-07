from pathlib import Path

import pandas as pd
import pytest
import yaml

from functions.action_summary import build_action_focused_summary
from functions.cell_rule_execution import execute_value_semantics
from functions.result_summary import summarize_value_semantics


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "inputs" / "test_fixtures"


@pytest.fixture(scope="module")
def executions(kb):
    results = {}
    for name in ("pd", "lgd", "ead"):
        data = pd.read_csv(FIXTURES / f"{name}_data_v0_2.csv")
        dictionary = pd.read_csv(FIXTURES / f"{name}_dictionary_v0_2.csv", keep_default_na=False)
        bindings = yaml.safe_load(
            (FIXTURES / f"{name}_expected_role_bindings_v0_2.yaml").read_text(encoding="utf-8")
        )["bindings"]
        declarations = yaml.safe_load(
            (FIXTURES / f"{name}_runtime_declarations_v0_2.yaml").read_text(encoding="utf-8")
        )
        execution = execute_value_semantics(data, dictionary, bindings, declarations, kb)
        summaries = summarize_value_semantics(data, dictionary, bindings, kb, execution)
        results[name] = (data, dictionary, bindings, execution, summaries)
    return results


@pytest.mark.parametrize("name", ["pd", "lgd", "ead"])
def test_existing_expected_claims_are_preserved(name, executions):
    execution = executions[name][3]
    expected = pd.read_csv(FIXTURES / f"{name}_expected_cell_tags_v0_2.csv").rename(columns={
        "row_id": "row_reference", "column_name": "input_variable", "expected_tag": "tag",
    })
    keys = ["row_reference", "input_variable", "tag"]
    comparison = expected[keys].merge(execution.raw_cell_claims[keys].drop_duplicates(), on=keys, how="left", indicator=True)
    assert comparison["_merge"].eq("both").all()


@pytest.mark.parametrize(
    ("name", "total", "counts"),
    [
        ("pd", 3550, {"NOT_APPLICABLE": 3096, "CENSORED": 400, "STALE_FROZEN": 54}),
        ("lgd", 858, {"NOT_APPLICABLE": 472, "CENSORED": 386}),
        ("ead", 4320, {"CENSORED": 2790, "NOT_APPLICABLE": 1500, "STALE_FROZEN": 30}),
    ],
)
def test_resolved_cell_counts_are_stable(name, total, counts, executions):
    resolved = executions[name][3].resolved_cell_tags
    assert not resolved.duplicated(["row_reference", "input_variable"]).any()
    assert len(resolved) == total
    assert resolved["tag"].value_counts().to_dict() == counts


def test_lgd_precedence_resolves_overlapping_claims(executions):
    execution = executions["lgd"][3]
    overlaps = execution.resolved_cell_tags.loc[execution.resolved_cell_tags["claim_count"].gt(1)]
    assert len(overlaps) == 14
    assert overlaps["tag"].eq("NOT_APPLICABLE").all()
    assert overlaps["suppressed_tags"].eq("CENSORED").all()


def test_executor_finds_kb_defined_cases_missing_from_old_expectations(executions):
    pd_tags = executions["pd"][3].resolved_cell_tags
    assert len(pd_tags.loc[pd_tags["input_variable"].eq("MAT_BALLOON_IND")]) == 1200
    assert len(pd_tags.loc[pd_tags["input_variable"].eq("PRIN_PAYDOWN_AMT")]) == 300
    assert len(pd_tags.loc[pd_tags["input_variable"].eq("INT_RT") & pd_tags["tag"].eq("STALE_FROZEN")]) == 51

    ead_tags = executions["ead"][3].resolved_cell_tags
    assert len(ead_tags.loc[
        ead_tags["input_variable"].isin(["UNDRAWN_AMT", "AVAIL_CR"])
        & ead_tags["tag"].eq("STALE_FROZEN")
    ]) == 18
    assert len(ead_tags.loc[ead_tags["row_reference"].astype(str).str.startswith("EAD078_")]) == 36


def test_required_summary_views_and_denominators(executions):
    _, _, _, execution, summaries = executions["ead"]
    assert set(summaries) == {
        "dataset_summary", "field_summary", "rule_summary", "group_summary",
        "assessment_outcomes", "precedence_summary", "populated_where_not_applicable",
        "unexamined_fields",
    }
    dataset = summaries["dataset_summary"].iloc[0]
    assert dataset["distinct_tagged_cells"] == len(execution.resolved_cell_tags)
    assert dataset["multi_claim_cells"] == len(summaries["precedence_summary"])
    assert summaries["group_summary"]["assessed_group_share"].between(0, 1).all()
    assert "ROW_ID" in set(summaries["unexamined_fields"]["input_variable"])


def test_row_reference_must_be_unique(kb):
    data = pd.DataFrame({"ROW_ID": ["A", "A"], "X": [1, 2]})
    dictionary = pd.DataFrame({"column_name": ["ROW_ID", "X"], "sentinel_value": ["", ""]})
    with pytest.raises(ValueError, match="unique"):
        execute_value_semantics(data, dictionary, {}, {}, kb)


def test_action_summary_separates_rca_configuration_and_expected_treatment(executions):
    data, _, bindings, execution, summaries = executions["pd"]
    actions = build_action_focused_summary(data, bindings, execution, summaries)
    executive = actions["executive_action_summary"].iloc[0]
    queue = actions["action_queue"]
    assert executive["overall_user_decision"] == "RAISE_ISSUE_FOR_RCA"
    assert {"RAISE_ISSUE_FOR_RCA", "REVIEW_CONFIGURATION", "APPLY_EXPECTED_TREATMENT"} <= set(queue["user_decision"])
    assert queue.loc[queue["tag"].eq("STALE_FROZEN"), "rca_warranted"].all()
    assert not queue.loc[queue["tag"].eq("CENSORED"), "rca_warranted"].any()
    populated_na = queue.loc[queue["tag"].eq("NOT_APPLICABLE") & queue["rca_warranted"]]
    assert not populated_na.empty
    assert not actions["rca_evidence"].empty


def test_assessed_fields_without_tags_are_reported_as_expected_path(executions):
    data, _, bindings, execution, summaries = executions["pd"]
    actions = build_action_focused_summary(data, bindings, execution, summaries)
    expected = actions["expected_path_summary"]
    assert set(expected["user_decision"]) <= {"NO_ACTION_EXPECTED_PATH"}
    assert expected["tagged_cells"].eq(0).all() if "tagged_cells" in expected else True
