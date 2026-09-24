from __future__ import annotations

from types import SimpleNamespace

from ai.model_registry import ModelDeployment, ModelPolicy
from ai.model_runtime import ModelExecution
from domains.rca import investigation_agent


def _deployment() -> ModelDeployment:
    return ModelDeployment(
        model_id="gpt-5-6-sol", provider_id="azure-primary",
        deployment="gpt-5.6-sol", model_name="gpt-5.6-sol",
        model_version="2026-07-09", deployment_type="GlobalStandard",
        lifecycle_status="GenerallyAvailable", retirement_date="2028-01-11",
        version_upgrade_policy="OnceCurrentVersionExpired", tpm_limit=20000, rpm_limit=20,
    )


def test_planner_schema_closes_helper_parameters_for_azure_strict_output():
    schema = investigation_agent.InvestigationPlan.model_json_schema()
    helper_schema = schema["$defs"]["HelperParameters"]
    assert helper_schema["additionalProperties"] is False
    assert "column" in helper_schema["properties"]
    assert "segment_col" in helper_schema["properties"]


def test_plan_uses_closed_structured_contract_and_returns_parameter_object(monkeypatch):
    deployment = _deployment()
    policy = ModelPolicy(
        endpoint="https://unit-test.openai.azure.com/", api_key="not-a-real-key",
        primary=deployment, fallback=None, fallback_on=(), max_fallbacks=0,
        request_timeout=30, request_retries=1,
    )
    parsed = investigation_agent.InvestigationPlan.model_validate({
        "question": "Does missingness dominate retained PSI?",
        "rationale": "Use the authoritative retained bins.",
        "analysis_kind": "psi_decomposition",
        "preferred_helper_ids": ["psi_evidence_decomposition"],
        "helper_params": {"column": "debt_yield"},
        "expected_output": ["dominant contribution"],
        "supports_hypothesis_when": "The missing bin dominates.",
        "rejects_hypothesis_when": "Non-missing bins dominate.",
    })
    request = {}

    class Responses:
        @staticmethod
        def parse(**kwargs):
            request.update(kwargs)
            return SimpleNamespace(id="resp-plan", model="gpt-5.6-sol", output_parsed=parsed)

    def execute(workload, operation, *, policy):
        assert workload == "rca_investigation_planner"
        response = operation(SimpleNamespace(responses=Responses()), deployment)
        return ModelExecution(response, deployment, [{"attempt": 1, "status": "completed"}])

    monkeypatch.setattr(investigation_agent, "load_model_policy", lambda _workload: policy)
    monkeypatch.setattr(investigation_agent, "execute_with_fallback", execute)

    result = investigation_agent.plan({"case": {"case_id": "synthetic"}})

    assert request["text_format"] is investigation_agent.InvestigationPlan
    assert "coarsest aggregation" in request["instructions"]
    assert "prefer year before quarter" in request["instructions"]
    assert result["output"]["helper_params"]["column"] == "debt_yield"
    assert result["output"]["helper_params"]["date_col"] is None


def test_agent_unavailable_maps_to_retryable_service_response():
    from domains.rca import service
    from routers.v3 import _rca_error_map

    response = _rca_error_map(service.RcaAgentUnavailable("Planner unavailable; retry."))

    assert response.status_code == 503
    assert response.detail == "Planner unavailable; retry."


def test_driver_target_uses_structured_non_executable_contract(monkeypatch):
    deployment = _deployment()
    policy = ModelPolicy(
        endpoint="https://unit-test.openai.azure.com/", api_key="not-a-real-key",
        primary=deployment, fallback=None, fallback_on=(), max_fallbacks=0,
        request_timeout=30, request_retries=1,
    )
    parsed = investigation_agent.DriverTargetProblem.model_validate({
        "problem_statement": "Which inputs separate baseline and current rows?",
        "target_mode": "population_membership", "affected_column": "debt",
        "positive_class_definition": "row belongs to current population",
        "candidate_columns": ["origination_year", "product_type"],
        "excluded_columns": ["debt", "year"],
        "rationale": "Use the frozen population labels.", "unavailable_reason": None,
    })
    request = {}

    class Responses:
        @staticmethod
        def parse(**kwargs):
            request.update(kwargs)
            return SimpleNamespace(id="resp-target", model="gpt-5.6-sol", output_parsed=parsed)

    def execute(workload, operation, *, policy):
        assert workload == "rca_driver_target"
        response = operation(SimpleNamespace(responses=Responses()), deployment)
        return ModelExecution(response, deployment, [{"attempt": 1, "status": "completed"}])

    monkeypatch.setattr(investigation_agent, "load_model_policy", lambda _workload: policy)
    monkeypatch.setattr(investigation_agent, "execute_with_fallback", execute)

    result = investigation_agent.define_driver_target({"available_schema": {"debt": "numeric"}})

    assert request["text_format"] is investigation_agent.DriverTargetProblem
    assert "Do not claim causality or write code" in request["instructions"]
    assert result["output"]["target_mode"] == "population_membership"
