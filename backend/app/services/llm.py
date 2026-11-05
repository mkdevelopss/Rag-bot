"""Provider-agnostic LLM client.

Groq, Gemini and OpenAI all expose the OpenAI chat-completions protocol
(including streaming and tool calling), so a single client covers them.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Lock
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


class LLMClient:
    def __init__(self, settings_provider):
        self.settings_provider = settings_provider
        self._clients: dict[tuple, Any] = {}
        self._lock = Lock()

    def is_configured(self) -> bool:
        settings: Settings = self.settings_provider()
        return bool(settings.llm_api_key)

    def _client(self, settings: Settings):
        if not settings.llm_api_key:
            raise LLMError(
                f"{settings.llm_provider.title()} is not configured. "
                f"Add {settings.llm_api_key_variable} to backend/.env.",
                status_code=503,
            )
        key = (
            settings.resolved_llm_base_url,
            settings.llm_api_key,
            settings.llm_timeout_seconds,
            settings.llm_max_retries,
        )
        with self._lock:
            if key not in self._clients:
                from openai import OpenAI

                self._clients[key] = OpenAI(
                    api_key=settings.llm_api_key,
                    base_url=settings.resolved_llm_base_url,
                    timeout=settings.llm_timeout_seconds,
                    max_retries=settings.llm_max_retries,
                )
            return self._clients[key]

    @staticmethod
    def _translate(provider: str, exc: Exception) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        status = getattr(exc, "status_code", None)
        name = provider.title()
        logger.warning("%s request failed: %s", name, exc)
        if status == 429:
            return LLMError(f"{name} rate limit reached. Wait briefly, then try again.", 429)
        if status in (401, 403):
            return LLMError(f"{name} rejected the API key. Check it in backend/.env.", 503)
        if status == 400:
            return LLMError(f"{name} rejected the request: {exc}", 400)
        if exc.__class__.__name__ in {"APITimeoutError", "APIConnectionError"}:
            return LLMError(f"Could not reach {name}. Check the network and try again.", 504)
        return LLMError(f"{name} generation failed: {exc}", 502)

    @staticmethod
    def _sampling(settings: Settings, temperature: float) -> dict[str, Any]:
        # OpenAI reasoning models reject a custom temperature.
        model = settings.llm_model.lower()
        if settings.llm_provider == "openai" and model.startswith(("gpt-5", "o1", "o3", "o4")):
            return {}
        return {"temperature": temperature}

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[tuple[str, Any]]:
        """Yield ("text", str) deltas, then ("tool_calls", list[ToolCall]) if the model called tools."""
        settings: Settings = self.settings_provider()
        request: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "stream": True,
            **self._sampling(settings, settings.llm_temperature),
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"
        pending: dict[int, dict[str, str]] = {}
        try:
            response = self._client(settings).chat.completions.create(**request)
            for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    yield "text", delta.content
                for position, call in enumerate(getattr(delta, "tool_calls", None) or []):
                    index = call.index if call.index is not None else position
                    slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if call.id:
                        slot["id"] = call.id
                    function = getattr(call, "function", None)
                    if function is not None:
                        if function.name:
                            slot["name"] = function.name
                        if function.arguments:
                            slot["arguments"] += function.arguments
        except Exception as exc:
            raise self._translate(settings.llm_provider, exc) from exc
        if pending:
            yield "tool_calls", [
                ToolCall(
                    id=slot["id"] or f"call_{index}",
                    name=slot["name"],
                    arguments=slot["arguments"] or "{}",
                )
                for index, slot in sorted(pending.items())
                if slot["name"]
            ]

    def complete(self, messages: list[dict[str, Any]], max_tokens: int = 1024) -> str:
        settings: Settings = self.settings_provider()
        limit_key = "max_completion_tokens" if settings.llm_provider == "openai" else "max_tokens"
        try:
            response = self._client(settings).chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                **{limit_key: max_tokens},
                **self._sampling(settings, 0),
            )
        except Exception as exc:
            raise self._translate(settings.llm_provider, exc) from exc
        return (response.choices[0].message.content or "").strip() if response.choices else ""

    def stream_text(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        for kind, payload in self.stream_chat(messages):
            if kind == "text":
                yield payload
