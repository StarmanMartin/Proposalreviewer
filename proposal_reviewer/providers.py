"""AI endpoint adapters. Each returns the agent's raw JSON answer plus usage info."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urldefrag, urljoin, urlparse

import anthropic
import httpx

from .config import AIConfig

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Server-side web tools (run by Anthropic) used for context generation
WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]
# A long server-tool turn stops with "pause_turn" and is resumed by re-sending it
MAX_CONTINUATIONS = 5
# Linked pages longer than this (publication lists, ...) are left out; pages named in the prompt never are
MAX_LINKED_PAGE_CHARS = 50_000
URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+")


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


@dataclass
class AIText:
    text: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    name: str

    async def evaluate(self, system: str, user: str, schema: dict) -> AIAnswer: ...

    async def generate(self, system: str, user: str) -> AIText:
        """Free-text answer (no JSON schema); may read web pages named in the prompt."""
        ...


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

    async def _request(self, system: str, messages: list[dict], what: str, **params: Any) -> anthropic.types.Message:
        if self.config.effort:
            params.setdefault("output_config", {})["effort"] = self.config.effort
        if self.config.refusal_fallback:
            params |= {"extra_headers": {"anthropic-beta": FALLBACK_BETA}, "extra_body": {"fallbacks": "default"}}

        try:
            # Streaming avoids HTTP timeouts on long reviews with large max_tokens.
            async with self.client.messages.stream(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                # The system prompt (base rules + skills) is identical across requests -> cache it.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                **params,
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
            raise AIError(f"The AI model declined to {what} (category: {category})", 422)
        if message.stop_reason == "max_tokens":
            raise AIError("The AI answer was cut off; increase ai.max_tokens")
        log.info("Anthropic request %s used %s", message._request_id, _usage(message))
        return message

    async def evaluate(self, system: str, user: str, schema: dict) -> AIAnswer:
        message = await self._request(
            system,
            [{"role": "user", "content": user}],
            "evaluate this proposal",
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return AIAnswer(data=parse_json_answer(text), model=message.model, usage=_usage(message))

    async def generate(self, system: str, user: str) -> AIText:
        params: dict[str, Any] = {"tools": WEB_TOOLS} if self.config.web_tools else {}
        messages: list[dict] = [{"role": "user", "content": user}]
        for _ in range(MAX_CONTINUATIONS + 1):
            message = await self._request(system, messages, "answer this prompt", **params)
            if message.stop_reason != "pause_turn":
                break
            # The API resumes the turn from the trailing server tool use; no extra user message.
            messages = [messages[0], {"role": "assistant", "content": message.content}]
        else:
            raise AIError("The AI agent did not finish reading the web pages; narrow the prompt")

        # Only the text after the last tool result is the answer; earlier text is commentary.
        parts: list[str] = []
        for block in message.content:
            if block.type == "text":
                parts.append(block.text)
            elif block.type.endswith("tool_result"):
                parts = []
        return AIText(text="".join(parts), model=message.model, usage=_usage(message))


def _usage(message: anthropic.types.Message) -> dict[str, Any]:
    return message.usage.to_dict() if message.usage else {}


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "a" and (href := dict(attrs).get("href")):
            self.links.append(href)

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return "\n".join(parser.parts)


async def fetch_pages(prompt: str, max_linked: int = 0, timeout: float = 30) -> str:
    """Text of the pages whose URLs appear in the prompt, as <page> blocks (for endpoints without web tools).

    Up to max_linked pages linked from those pages on the same site are included as well, so that
    an overview page ("all technologies") also brings the pages it points to.
    """
    urls = list(dict.fromkeys(u.rstrip(".,;:!?") for u in URL_PATTERN.findall(prompt)))
    if not urls:
        return ""
    limit = asyncio.Semaphore(5)

    async def get(client: httpx.AsyncClient, url: str) -> tuple[str, list[str]]:
        async with limit:
            response = await client.get(url)
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", ""):
            return response.text, []
        parser = _TextExtractor()
        parser.feed(response.text)
        links = [urldefrag(urljoin(str(response.url), href)).url for href in parser.links]
        host = response.url.host
        return "\n".join(parser.parts), [link for link in links if urlparse(link).hostname == host]

    # Separate client: the AI endpoint's credentials must not be sent to these URLs.
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        pages: dict[str, str] = {}
        linked: list[str] = []
        for url in urls:
            try:
                pages[url], links = await get(client, url)
            except httpx.HTTPError as e:
                raise AIError(f"Cannot fetch {url}: {e}") from e
            linked += links
        linked = [u for u in dict.fromkeys(linked) if u not in pages][:max_linked]
        results = await asyncio.gather(*(get(client, u) for u in linked), return_exceptions=True)
        for url, result in zip(linked, results):
            if isinstance(result, BaseException):
                log.warning("Skipping linked page %s: %s", url, result)
            elif len(result[0]) > MAX_LINKED_PAGE_CHARS:
                log.warning("Skipping linked page %s: %d characters", url, len(result[0]))
            elif result[0].strip():
                pages[url] = result[0]
    return "\n\n".join(f'<page url="{url}">\n{text}\n</page>' for url, text in pages.items())


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

    async def _chat(self, system: str, user: str, json_mode: bool) -> AIText:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
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
        return AIText(text=text, model=body.get("model", self.config.model), usage=body.get("usage") or {})

    async def evaluate(self, system: str, user: str, schema: dict) -> AIAnswer:
        answer = await self._chat(system, user, self.config.json_mode)
        return AIAnswer(data=parse_json_answer(answer.text), model=answer.model, usage=answer.usage)

    async def generate(self, system: str, user: str) -> AIText:
        # No server-side web tools here: fetch the pages named in the prompt and hand them over.
        pages = await fetch_pages(user, self.config.web_max_linked_pages)
        return await self._chat(system, f"{pages}\n\n{user}" if pages else user, json_mode=False)


def create_provider(config: AIConfig) -> Provider:
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    return OpenAICompatibleProvider(config)
