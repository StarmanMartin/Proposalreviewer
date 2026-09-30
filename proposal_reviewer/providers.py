"""AI endpoint adapters. Each returns the agent's raw JSON answer plus usage info."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx

from .config import AIConfig

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AIError(RuntimeError):
    """The AI endpoint failed or returned an unusable answer."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AIAnswer:
    data: dict[str, Any]
    model: str
    usage: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    name: str

    async def evaluate(self, system: str, user: str, schema: dict) -> AIAnswer: ...


def parse_json_answer(text: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating markdown fences or prose around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise AIError("The AI agent did not return valid JSON")


class AnthropicProvider:
    """Claude via the Anthropic Messages API (official SDK)."""

    name = "anthropic"

    def __init__(self, config: AIConfig):
        self.config = config
        self.client = anthropic.AsyncAnthropic(
            api_key=config.api_key or None,
            base_url=config.base_url or None,
            timeout=config.timeout_seconds,
        )

    async def evaluate(self, system: str, user: str, schema: dict) -> AIAnswer:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.config.effort:
            output_config["effort"] = self.config.effort
        extra: dict[str, Any] = {}
        if self.config.refusal_fallback:
            extra = {"extra_headers": {"anthropic-beta": FALLBACK_BETA}, "extra_body": {"fallbacks": "default"}}

        try:
            # Streaming avoids HTTP timeouts on long reviews with large max_tokens.
            async with self.client.messages.stream(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                # The system prompt (base rules + skills) is identical across requests -> cache it.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                **extra,
            ) as stream:
                message = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise AIError(f"AI endpoint rejected the credentials: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise AIError("AI endpoint rate limit reached, retry later", status_code=503) from e
        except anthropic.BadRequestError as e:
            raise AIError(f"AI endpoint rejected the request: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise AIError(f"AI endpoint error ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AIError(f"Cannot reach AI endpoint: {e}", status_code=504) from e

        if message.stop_reason == "refusal":
            category = getattr(message.stop_details, "category", None) if message.stop_details else None
            raise AIError(f"The AI model declined to evaluate this proposal (category: {category})", 422)
        if message.stop_reason == "max_tokens":
            raise AIError("The AI answer was cut off; increase ai.max_tokens")

        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage.to_dict() if message.usage else {}
        log.info("Anthropic request %s used %s", message._request_id, usage)
        return AIAnswer(data=parse_json_answer(text), model=message.model, usage=usage)


class OpenAICompatibleProvider:
    """Any endpoint implementing POST {base_url}/chat/completions (OpenAI, Ollama, vLLM, LiteLLM, ...)."""

    name = "openai"

    def __init__(self, config: AIConfig):
        if not config.base_url:
            raise ValueError("ai.base_url is required for provider 'openai'")
        self.config = config
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        self.client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"), headers=headers, timeout=config.timeout_seconds
        )

    async def evaluate(self, system: str, user: str, schema: dict) -> AIAnswer:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = await self.client.post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise AIError(f"Cannot reach AI endpoint: {e}", status_code=504) from e
        if response.status_code >= 400:
            raise AIError(f"AI endpoint error ({response.status_code}): {response.text[:500]}")

        body = response.json()
        try:
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise AIError("Unexpected response format from AI endpoint") from e
        if choice.get("finish_reason") == "length":
            raise AIError("The AI answer was cut off; increase ai.max_tokens")
        return AIAnswer(data=parse_json_answer(text), model=body.get("model", self.config.model), usage=body.get("usage") or {})


def create_provider(config: AIConfig) -> Provider:
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    return OpenAICompatibleProvider(config)
