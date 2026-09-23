"""Provider adapters. Every provider exposes the same `complete()` interface,
so the router never needs to know which vendor it is talking to.

The real adapters (OpenAI, Anthropic, Gemini) call the vendors' public HTTP APIs
with httpx. The mock providers let you test failover with no API keys, and can be
switched into failure modes at runtime from the dashboard.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

import httpx

from .config import Settings


class ProviderError(Exception):
    """A provider call failed. `retryable` controls whether the router retries
    the SAME provider; the router always falls back to the next provider."""

    def __init__(self, message: str, retryable: bool = True, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class Provider:
    name: str = "provider"
    model: str = ""
    kind: str = "real"
    # Example prices in USD per 1M tokens. Check each vendor's pricing page and edit.
    price_in_per_m: float = 0.0
    price_out_per_m: float = 0.0

    async def complete(self, messages: list[dict], max_tokens: int) -> Completion:
        raise NotImplementedError

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.price_in_per_m + completion_tokens * self.price_out_per_m) / 1_000_000

    async def aclose(self) -> None:
        return None


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code == 429 or resp.status_code >= 500
    raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=retryable, status=resp.status_code)


class _HttpProvider(Provider):
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _post(self, url: str, **kwargs) -> dict:
        try:
            resp = await self.client.post(url, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error: {exc.__class__.__name__}", retryable=True) from exc
        _raise_for_status(resp)
        return resp.json()


class OpenAIProvider(_HttpProvider):
    name = "openai"
    price_in_per_m, price_out_per_m = 0.15, 0.60

    def __init__(self, api_key: str, model: str):
        super().__init__()
        self.api_key, self.model = api_key, model

    async def complete(self, messages, max_tokens):
        data = await self._post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "messages": messages, "max_tokens": max_tokens},
        )
        try:
            usage = data.get("usage", {})
            return Completion(data["choices"][0]["message"]["content"] or "",
                              usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), self.model)
        except (KeyError, IndexError) as exc:
            raise ProviderError("unexpected response shape", retryable=False) from exc


class AnthropicProvider(_HttpProvider):
    name = "anthropic"
    price_in_per_m, price_out_per_m = 1.00, 5.00

    def __init__(self, api_key: str, model: str):
        super().__init__()
        self.api_key, self.model = api_key, model

    async def complete(self, messages, max_tokens):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        chat = [m for m in messages if m["role"] != "system"]
        body = {"model": self.model, "messages": chat, "max_tokens": max_tokens}
        if system:
            body["system"] = system
        data = await self._post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            json=body,
        )
        try:
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            usage = data.get("usage", {})
            return Completion(text, usage.get("input_tokens", 0), usage.get("output_tokens", 0), self.model)
        except (KeyError, TypeError) as exc:
            raise ProviderError("unexpected response shape", retryable=False) from exc


class GeminiProvider(_HttpProvider):
    name = "gemini"
    price_in_per_m, price_out_per_m = 0.10, 0.40

    def __init__(self, api_key: str, model: str):
        super().__init__()
        self.api_key, self.model = api_key, model

    async def complete(self, messages, max_tokens):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages if m["role"] != "system"
        ]
        body: dict = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        data = await self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json=body,
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            usage = data.get("usageMetadata", {})
            return Completion("".join(p.get("text", "") for p in parts),
                              usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), self.model)
        except (KeyError, IndexError) as exc:
            raise ProviderError("unexpected response shape (blocked or empty)", retryable=False) from exc


class MockProvider(Provider):
    """Fake provider for demos and tests. Modes:
    healthy -> answers after `latency_ms`
    down    -> fails immediately with HTTP 503
    slow    -> hangs until the router's timeout fires
    flaky   -> fails randomly with probability `failure_rate`
    """
    kind = "mock"
    price_in_per_m, price_out_per_m = 0.50, 1.50

    def __init__(self, name: str, latency_ms: float = 30.0):
        self.name = name
        self.model = f"{name}-model"
        self.latency_ms = latency_ms
        self.mode = "healthy"
        self.failure_rate = 0.0

    def set_mode(self, mode: str, failure_rate: float | None = None) -> None:
        if mode not in ("healthy", "down", "slow", "flaky"):
            raise ValueError(f"unknown mode: {mode}")
        self.mode = mode
        if failure_rate is not None:
            self.failure_rate = failure_rate
        elif mode == "flaky" and self.failure_rate == 0.0:
            self.failure_rate = 0.3

    async def complete(self, messages, max_tokens):
        if self.mode == "down":
            raise ProviderError("mock provider is down", retryable=True, status=503)
        if self.mode == "slow":
            await asyncio.sleep(60)  # cancelled by the router's timeout
        await asyncio.sleep(self.latency_ms / 1000)
        if self.mode == "flaky" and random.random() < self.failure_rate:
            raise ProviderError("mock provider random failure", retryable=True, status=500)
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        text = f"[{self.name}] You said: {last_user}"
        return Completion(text, sum(len(m["content"].split()) for m in messages) + 1,
                          min(len(text.split()), max_tokens), self.model)


def build_providers(settings: Settings) -> list[Provider]:
    providers: list[Provider] = []
    for name in settings.provider_order:
        if name == "openai" and settings.openai_api_key:
            providers.append(OpenAIProvider(settings.openai_api_key, settings.openai_model))
        elif name == "anthropic" and settings.anthropic_api_key:
            providers.append(AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model))
        elif name == "gemini" and settings.gemini_api_key:
            providers.append(GeminiProvider(settings.gemini_api_key, settings.gemini_model))
        elif name.startswith("mock"):
            providers.append(MockProvider(name, settings.mock_latency_ms))
        else:
            raise ValueError(f"Provider '{name}' is in PROVIDER_ORDER but has no API key configured")
    if not providers:
        raise ValueError("No providers configured")
    return providers
