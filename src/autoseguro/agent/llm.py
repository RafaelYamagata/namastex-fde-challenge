"""Thin wrapper over the Anthropic Messages API.

Every call is stateless: the prompt is rebuilt from the conversation state each time, so no
assistant turns (or thinking blocks) are replayed. Two kinds of call:
- `complete(..., schema=...)`: structured output (JSON schema) for understanding a message;
- `complete(...)`: plain text, for writing the reply.

Like the quote API, the LLM is a dependency that can fail. The SDK retries 429/5xx and
connection errors; anything that still fails (or a safety refusal) raises `LLMError`, and
the agent answers with a fixed message and hands the conversation to a human.
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol

import anthropic

from autoseguro.obs import log_event

DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    pass


class LLM(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        conversation_id: str,
        schema: dict[str, Any] | None = None,
    ) -> str: ...


class ClaudeLLM:
    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        # Short WhatsApp turns: low effort keeps latency down. Tune per deployment.
        self.effort = effort or os.environ.get("ANTHROPIC_EFFORT", "low")
        # Keys that aren't scoped to a workspace must name one on every request.
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        headers = {"anthropic-workspace-id": workspace} if workspace else None
        self._client = anthropic.Anthropic(
            timeout=timeout_s, max_retries=max_retries, default_headers=headers
        )

    def complete(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        conversation_id: str,
        schema: dict[str, Any] | None = None,
    ) -> str:
        output_config: dict[str, Any] = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        t0 = time.monotonic()
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=8000,
                # The system prompt never changes between calls, so it can be cached.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as exc:
            log_event(
                "llm_call_failed",
                conversation_id=conversation_id,
                purpose=purpose,
                error=type(exc).__name__,
                latency_ms=round((time.monotonic() - t0) * 1000),
            )
            raise LLMError(type(exc).__name__) from exc

        usage = response.usage
        log_event(
            "llm_call",
            conversation_id=conversation_id,
            purpose=purpose,
            model=response.model,
            request_id=getattr(response, "_request_id", None),
            stop_reason=response.stop_reason,
            latency_ms=round((time.monotonic() - t0) * 1000),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
        )
        if response.stop_reason == "refusal":
            raise LLMError("refusal")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMError(f"empty response (stop_reason={response.stop_reason})")
        return text
