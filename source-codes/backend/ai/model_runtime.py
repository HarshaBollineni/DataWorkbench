"""Azure OpenAI v1 execution with one governed deployment fallback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from ai.model_registry import ModelDeployment, ModelPolicy, load_model_policy


@dataclass(frozen=True)
class ModelExecution:
    response: Any
    deployment: ModelDeployment
    attempts: list[dict[str, Any]]


class ModelExecutionError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]):
        super().__init__(message)
        self.attempts = attempts


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _client(policy: ModelPolicy) -> Any:
    from openai import OpenAI

    parsed = urlsplit(policy.endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelExecutionError("The configured Azure OpenAI endpoint is invalid", [])
    base_url = urlunsplit((parsed.scheme, parsed.netloc, "/openai/v1/", "", ""))
    return OpenAI(
        base_url=base_url, api_key=policy.api_key,
        timeout=policy.request_timeout, max_retries=policy.request_retries,
    )


def failure_category(exc: Exception) -> str | None:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    if status == 429:
        return "rate_limit"
    if status == 404:
        return "deployment_unavailable"
    if status == 408 or "timeout" in name:
        return "timeout"
    return None


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        key: int(value) for key, value in {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }.items() if value is not None
    }


def execute_with_fallback(
    workload: str,
    operation: Callable[[Any, ModelDeployment], Any],
    *,
    policy: ModelPolicy | None = None,
    client_factory: Callable[[ModelPolicy], Any] = _client,
) -> ModelExecution:
    resolved = policy or load_model_policy(workload)
    client = client_factory(resolved)
    deployments = [resolved.primary]
    if resolved.fallback and resolved.max_fallbacks:
        deployments.append(resolved.fallback)
    attempts: list[dict[str, Any]] = []
    last_exc: Exception | None = None

    for index, deployment in enumerate(deployments, start=1):
        started_at = _timestamp()
        try:
            response = operation(client, deployment)
        except Exception as exc:  # noqa: BLE001 - provider exceptions vary by SDK version
            last_exc = exc
            category = failure_category(exc)
            attempts.append({
                "attempt": index, "started_at": started_at, "completed_at": _timestamp(),
                "status": "failed", "failure_category": category or "non_retryable",
                "error_type": type(exc).__name__, **deployment.public_metadata(),
            })
            may_fallback = (
                index == 1 and len(deployments) > 1
                and category is not None and category in resolved.fallback_on
            )
            if may_fallback:
                continue
            raise ModelExecutionError("The governed model route did not complete", attempts) from exc
        attempts.append({
            "attempt": index, "started_at": started_at, "completed_at": _timestamp(),
            "status": "completed", "response_id": getattr(response, "id", None),
            "response_model": getattr(response, "model", None), "usage": _usage(response),
            **deployment.public_metadata(),
        })
        return ModelExecution(response=response, deployment=deployment, attempts=attempts)

    raise ModelExecutionError("The governed model route did not complete", attempts) from last_exc
