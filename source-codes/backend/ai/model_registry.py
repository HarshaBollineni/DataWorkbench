"""Governed primary/fallback model configuration for product AI workloads.

Secrets stay in the shared backend ``.env`` and never appear in public model
metadata or AAR evidence.  Values are resolved at call time so tests and
deployed secret mounts can override the local dotenv without global mutation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class ModelConfigurationError(RuntimeError):
    """Raised when the governed model route is incomplete or inconsistent."""


@dataclass(frozen=True)
class ModelDeployment:
    model_id: str
    provider_id: str
    deployment: str
    model_name: str
    model_version: str
    deployment_type: str
    lifecycle_status: str
    retirement_date: str
    version_upgrade_policy: str
    tpm_limit: int
    rpm_limit: int

    def public_metadata(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "deployment": self.deployment,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "deployment_type": self.deployment_type,
            "lifecycle_status": self.lifecycle_status,
            "retirement_date": self.retirement_date,
            "version_upgrade_policy": self.version_upgrade_policy,
            "tpm_limit": self.tpm_limit,
            "rpm_limit": self.rpm_limit,
        }


@dataclass(frozen=True)
class ModelPolicy:
    endpoint: str
    api_key: str
    primary: ModelDeployment
    fallback: ModelDeployment | None
    fallback_on: tuple[str, ...]
    max_fallbacks: int
    request_timeout: float
    request_retries: int

    def public_metadata(self) -> dict[str, object]:
        return {
            "api_style": "azure_openai_v1",
            "primary": self.primary.public_metadata(),
            "fallback": self.fallback.public_metadata() if self.fallback else None,
            "fallback_on": list(self.fallback_on),
            "max_fallbacks": self.max_fallbacks,
        }


def configuration_values(*, env_path: Path = BACKEND_ENV_PATH,
                         environ: Mapping[str, str] | None = None) -> dict[str, str]:
    file_values = {
        key: str(value) for key, value in (
            dotenv_values(env_path).items() if env_path.is_file() else []
        ) if value is not None
    }
    runtime = dict(os.environ if environ is None else environ)
    return {**file_values, **runtime}


def _required(values: Mapping[str, str], key: str) -> str:
    value = str(values.get(key) or "").strip()
    if not value:
        raise ModelConfigurationError(f"{key} is required")
    return value


def _integer(values: Mapping[str, str], key: str, default: int) -> int:
    try:
        value = int(str(values.get(key) or default))
    except ValueError as exc:
        raise ModelConfigurationError(f"{key} must be an integer") from exc
    if value < 0:
        raise ModelConfigurationError(f"{key} cannot be negative")
    return value


def _deployment(values: Mapping[str, str], prefix: str) -> ModelDeployment:
    return ModelDeployment(
        model_id=_required(values, f"AI_{prefix}_MODEL_ID"),
        provider_id=_required(values, f"AI_{prefix}_PROVIDER_ID"),
        deployment=_required(values, f"AI_{prefix}_DEPLOYMENT"),
        model_name=_required(values, f"AI_{prefix}_MODEL_NAME"),
        model_version=_required(values, f"AI_{prefix}_MODEL_VERSION"),
        deployment_type=str(values.get(f"AI_{prefix}_DEPLOYMENT_TYPE") or ""),
        lifecycle_status=str(values.get(f"AI_{prefix}_LIFECYCLE_STATUS") or ""),
        retirement_date=str(values.get(f"AI_{prefix}_RETIREMENT_DATE") or ""),
        version_upgrade_policy=str(
            values.get(f"AI_{prefix}_VERSION_UPGRADE_POLICY") or ""
        ),
        tpm_limit=_integer(values, f"AI_{prefix}_TPM_LIMIT", 0),
        rpm_limit=_integer(values, f"AI_{prefix}_RPM_LIMIT", 0),
    )


def load_model_policy(workload: str, *, values: Mapping[str, str] | None = None) -> ModelPolicy:
    resolved = dict(values) if values is not None else configuration_values()
    endpoint = _required(resolved, "AZURE_OPENAI_ENDPOINT")
    api_key = str(
        resolved.get("AZURE_OPENAI_API_KEY") or resolved.get("OPENAI_API_KEY") or ""
    ).strip()
    if not api_key:
        raise ModelConfigurationError("AZURE_OPENAI_API_KEY is required")

    primary = _deployment(resolved, "PRIMARY")
    fallback_workloads = {
        item.strip() for item in str(resolved.get("AI_FALLBACK_WORKLOADS") or "").split(",")
        if item.strip()
    }
    fallback = (_deployment(resolved, "FALLBACK")
                if "all" in fallback_workloads or workload in fallback_workloads else None)
    if fallback and fallback.provider_id != primary.provider_id:
        raise ModelConfigurationError(
            "Primary and fallback must use the shared provider configuration"
        )
    max_fallbacks = min(_integer(resolved, "AI_MAX_MODEL_FALLBACKS", 0), 1)
    fallback_on = tuple(
        item.strip() for item in str(resolved.get("AI_FALLBACK_ON") or "").split(",")
        if item.strip()
    )
    if fallback and max_fallbacks and not fallback_on:
        raise ModelConfigurationError("AI_FALLBACK_ON must identify allowed failure categories")
    try:
        timeout = float(str(resolved.get("AI_REQUEST_TIMEOUT") or "90"))
    except ValueError as exc:
        raise ModelConfigurationError("AI_REQUEST_TIMEOUT must be numeric") from exc
    if timeout <= 0:
        raise ModelConfigurationError("AI_REQUEST_TIMEOUT must be positive")
    return ModelPolicy(
        endpoint=endpoint, api_key=api_key, primary=primary,
        fallback=fallback if max_fallbacks else None,
        fallback_on=fallback_on, max_fallbacks=max_fallbacks,
        request_timeout=timeout,
        request_retries=_integer(resolved, "AI_MAX_RETRIES", 1),
    )


def rca_llm_enabled(*, values: Mapping[str, str] | None = None) -> bool:
    resolved = dict(values) if values is not None else configuration_values()
    return str(resolved.get("AI_RCA_LLM_ENABLED") or "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
