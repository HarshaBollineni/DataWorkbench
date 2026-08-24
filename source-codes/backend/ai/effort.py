"""Plan 3 / F5 — EffortPolicy over Azure gpt-4.1.

Emulates "effort" by tiered sampling + multi-pass reasoning. There is NO "fast"
or short-circuit path by design (asserted in tests). A ``model_for(tier)`` seam
returns the deployment name (today always the configured gpt-4.1 deployment) so
a reasoning model can be added later via env without code change.

Tiers:
  low    -> temp 0.2, 1 pass
  medium -> temp 0.4, 1 pass, larger max_tokens
  high   -> temp 0.3, 2-3 passes + a self-critique merge pass
"""
from __future__ import annotations

import os

from .control_plane import resolve as resolve_role
from .llm import get_client, get_model

# Locked tier table — note there is NO "fast" entry.
TIERS: dict[str, dict] = {
    "low": {"temperature": 0.2, "passes": 1, "max_tokens": 1200},
    "medium": {"temperature": 0.4, "passes": 1, "max_tokens": 2400},
    "high": {"temperature": 0.3, "passes": 3, "max_tokens": 3000},
}

_CRITIQUE = (
    "You are reviewing {n} independent draft answers to the SAME task. "
    "Critically merge them into one superior final answer: keep what is correct "
    "and well-justified, discard hallucinations or unsupported claims, and "
    "resolve contradictions in favour of the most rigorous, domain-grounded "
    "reasoning. Return ONLY the final merged answer in the requested format."
)


def model_for(tier: str) -> str:
    """Deployment name for a tier. Today always gpt-4.1; an env override
    (``AI_MODEL_<TIER>``) lets a reasoning model be slotted in later."""
    return os.getenv(f"AI_MODEL_{tier.upper()}") or get_model()


class EffortPolicy:
    """Effort-tiered LLM caller. Never has a fast path.

    Plan 8 (Aspect b): when ``agent_key`` is supplied, model / temperature /
    effort all derive from the system-owned control plane
    (``ai/control_plane.py``) instead of inline tier defaults. Today every role
    resolves to gpt-4.1, so behaviour is unchanged; the knobs are now centralized
    and a future model swap is a one-line config edit. An explicit ``tier``
    still overrides the role's effort when a call site needs to.
    """

    def __init__(self, tier: str | None = None, agent_key: str | None = None):
        self.agent_key = agent_key
        temp_override: float | None = None
        model_override: str | None = None
        if agent_key:
            rc = resolve_role(agent_key)
            tier = tier or rc["effort"]
            temp_override = rc["temperature"]
            model_override = rc["model"]
        tier = tier or "medium"
        # No-LLM roles ("none") never run a pass; map defensively so construction
        # doesn't explode if a call site builds one anyway.
        if tier == "none":
            tier = "low"
        if tier not in TIERS:
            raise ValueError(f"Unknown effort tier: {tier!r} (no 'fast' tier exists)")
        self.tier = tier
        self.cfg = TIERS[tier]
        # Token accounting (Feedback R4.2): accumulate real usage across passes;
        # fall back to a ~4-chars/token estimate when the API omits usage so the
        # UI can always show a figure (flagged as estimated).
        self._tokens = {"prompt": 0, "completion": 0, "total": 0, "estimated": False}
        # Role temperature (3-bucket policy) overrides the tier's default temp.
        self.temperature = temp_override if temp_override is not None else self.cfg["temperature"]
        # Role model override resolves through the control plane; today gpt-4.1.
        self.model = model_for(tier) if model_override is None else (
            os.getenv(f"AI_MODEL_{tier.upper()}") or model_override or get_model()
        )

    def _one_pass(self, messages: list[dict], json_mode: bool, temperature: float,
                  on_event=None) -> str:
        client = get_client()
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.cfg["max_tokens"],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        content = ""
        usage_obj = None
        # Plan 6 — when a listener is attached, stream the completion so the Agent
        # Console renders the model's words live (modern-chatbot 'thinking'). Each
        # delta is emitted as a {phase:'token', text:...} event. `include_usage`
        # asks the API for a final usage chunk (empty choices) so we can report
        # real token counts. Any streaming failure (e.g. a deployment that rejects
        # stream+json_object) falls back to a single blocking call below.
        if on_event is not None:
            try:
                stream = client.chat.completions.create(
                    stream=True, stream_options={"include_usage": True}, **kwargs)
                parts: list[str] = []
                for chunk in stream:
                    u = getattr(chunk, "usage", None)
                    if u is not None:
                        usage_obj = u
                    choices = getattr(chunk, "choices", None)
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    piece = getattr(delta, "content", None) if delta else None
                    if piece:
                        parts.append(piece)
                        on_event({"phase": "token", "text": piece})
                content = "".join(parts)
            except Exception:  # noqa: BLE001 — degrade gracefully to non-streaming
                content = ""
                usage_obj = None
        if not content:
            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            usage_obj = getattr(resp, "usage", None)
        self._record_usage(messages, content, usage_obj)
        if on_event:
            on_event({"phase": "pass", "tier": self.tier, "chars": len(content)})
        return content

    def _record_usage(self, messages: list[dict], content: str, usage_obj) -> None:
        """Accumulate token usage from one pass — real if the API reported it,
        else a ~4-chars/token estimate (flagged)."""
        if usage_obj is not None:
            self._tokens["prompt"] += int(getattr(usage_obj, "prompt_tokens", 0) or 0)
            self._tokens["completion"] += int(getattr(usage_obj, "completion_tokens", 0) or 0)
            self._tokens["total"] += int(getattr(usage_obj, "total_tokens", 0) or 0)
        else:
            ptxt = sum(len(str(m.get("content", ""))) for m in messages)
            self._tokens["prompt"] += ptxt // 4
            self._tokens["completion"] += len(content) // 4
            self._tokens["total"] += (ptxt + len(content)) // 4
            self._tokens["estimated"] = True

    def _emit_usage(self, on_event) -> None:
        if on_event:
            on_event({"phase": "usage", "total_tokens": self._tokens["total"],
                      "prompt_tokens": self._tokens["prompt"],
                      "completion_tokens": self._tokens["completion"],
                      "estimated": self._tokens["estimated"]})

    def run(self, messages: list[dict], json_mode: bool = False, on_event=None) -> str:
        """Execute the tier's policy and return the final answer string.

        ``on_event`` (optional) is called with small dicts for SSE streaming.
        For ``high`` we draft N passes (slightly perturbed temperature) then run
        a self-critique merge pass.
        """
        passes = self.cfg["passes"]
        base_t = self.temperature
        if passes <= 1:
            out = self._one_pass(messages, json_mode, base_t, on_event)
            self._emit_usage(on_event)
            return out

        drafts: list[str] = []
        for i in range(passes):
            t = min(0.9, base_t + 0.1 * i)  # diversify drafts
            if on_event:
                on_event({"phase": "draft", "index": i + 1, "of": passes})
            drafts.append(self._one_pass(messages, json_mode, t, on_event))

        if on_event:
            on_event({"phase": "merge", "drafts": len(drafts)})
        critique = _CRITIQUE.format(n=len(drafts))
        if json_mode:
            critique += " Return the final merged answer as a single STRICT JSON object."
        merge_msgs = [
            {"role": "system", "content": critique},
            {"role": "user", "content": "\n\n".join(
                f"--- DRAFT {i + 1} ---\n{d}" for i, d in enumerate(drafts))},
        ]
        # Preserve the original task as context for the merger.
        if messages and messages[0].get("role") == "system":
            merge_msgs.insert(1, {"role": "user",
                                  "content": f"ORIGINAL TASK:\n{messages[-1].get('content','')}"})
        out = self._one_pass(merge_msgs, json_mode, base_t, on_event)
        self._emit_usage(on_event)
        return out


def has_fast_path() -> bool:
    """Guard for the structural 'never fast' guarantee — always False."""
    return "fast" in TIERS
