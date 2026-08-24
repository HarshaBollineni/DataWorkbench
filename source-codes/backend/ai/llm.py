"""Lazy OpenAI client + shared helpers. Importing this never requires a key;
the key is read only when an LLM call is actually made.

Phase 2 / PLT-08 note: this pass only adds type hints, docstrings, and debug
logging (through ``ai/_helper_log.py``'s shared logger) at the two points
where this module does real work — resolving a client and building a
schema/stats prompt block — no behavioural change. This module is LIVE
(``routers/v2.py`` and ``ai/effort.py`` both depend on it); the API key/secret
itself is never logged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

from ai._helper_log import logger

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv optional
    def load_dotenv(*_a: Any, **_k: Any) -> bool:
        return False


def get_model() -> str:
    """The model/deployment name, resolved at call time (after .env is loaded).

    On Azure OpenAI the "model" passed to chat.completions is the DEPLOYMENT name.
    """
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    load_dotenv()
    return os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv("OPENAI_MODEL", "gpt-4o")


def get_client(house: str | None = None) -> Any:
    """Construct an (Azure) OpenAI client on demand. Raises a clear error if no key.

    Uses AzureOpenAI when AZURE_OPENAI_ENDPOINT is set, otherwise standard OpenAI.

    Plan 8 seam: ``house`` is accepted but ignored today — only the single Azure
    OpenAI endpoint is provisioned. When a second house (e.g. Claude via Azure AI
    Foundry) is added, route here:
    # FUTURE: route by house -> {endpoint, key, deployment} from a multi-endpoint
    # credential map; control_plane.resolve()['house'] selects the credential set.
    """
    _ = house  # reserved for the future multi-endpoint router
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    load_dotenv()
    # Feedback R5 (item-4 RCA): bound every request so a slow/unresponsive Azure
    # call can no longer hang an SSE worker indefinitely (the SDK default is 600s
    # x 2 retries ~= 20 min). A tighter timeout + a single retry surfaces an error
    # frame the UI can react to, and pairs with the client-side Stop button.
    timeout = float(os.getenv("AI_REQUEST_TIMEOUT", "90"))
    max_retries = int(os.getenv("AI_MAX_RETRIES", "1"))
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if endpoint:
        from openai import AzureOpenAI

        key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("AZURE_OPENAI_API_KEY is not set — required for AI features.")
        logger.debug("helper=llm.get_client context_id=- outcome=ok provider=azure")
        return AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
            timeout=timeout,
            max_retries=max_retries,
        )

    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set — required for AI features.")
    logger.debug("helper=llm.get_client context_id=- outcome=ok provider=openai")
    return OpenAI(api_key=key, timeout=timeout, max_retries=max_retries)


def schema_and_stats(df: pd.DataFrame, max_cols: int = 30) -> str:
    """Compact schema + per-column statistics block for prompting."""
    lines: list[str] = []
    for col in df.columns[:max_cols]:
        s = df[col]
        null_pct = float(s.isna().mean()) * 100
        if pd.api.types.is_numeric_dtype(s):
            lines.append(
                f"- {col} ({s.dtype}): null {null_pct:.1f}%, "
                f"min {s.min():.3g}, max {s.max():.3g}, mean {s.mean():.3g}"
            )
        else:
            top = ", ".join(map(str, s.dropna().unique()[:5]))
            lines.append(f"- {col} ({s.dtype}): null {null_pct:.1f}%, top: {top}")
    return "\n".join(lines)
