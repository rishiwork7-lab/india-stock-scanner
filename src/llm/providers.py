"""LLM providers and a router with fallback across them (the original routes through LiteLLM; this replica calls each
provider directly).

  anthropic - Claude through the official `anthropic` SDK. Default model claude-opus-5-5 (thinking is always on for it;
              depth is set with output_config.effort). The large, fixed system prompt is marked for prompt caching so a
              watchlist run re-reads it cheaply. Server-side refusal fallback is enabled on models that support it.
  gemini    - Google Gemini REST generateContent.
  openai    - any OpenAI-compatible /chat/completions endpoint (OpenAI, DeepSeek, OpenRouter, Groq, Ollama, ...).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# models that accept the server-side refusal fallback parameter (fallbacks="default")
_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class ProviderError(Exception):
    pass


class AllProvidersFailed(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    usage: Dict = field(default_factory=dict)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, effort: str = "medium", timeout: float = 300.0, client=None):
        self.model, self.effort = model, effort
        self._client = client
        self._timeout = timeout

    @property
    def available(self) -> bool:
        return self._client is not None or bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))

    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(timeout=self._timeout)
        return self._client

    def generate(self, system: str, prompt: str, json_mode: bool, max_tokens: int = 16000) -> LLMResult:
        import anthropic
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            # stable system prompt first, cached; the per-stock prompt is the only part that changes between calls
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.effort},
        )
        try:
            if self.model in _FALLBACK_MODELS:
                resp = self.client().beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self.client().messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            raise ProviderError(f"Claude rate limited: {e}") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"Claude connection error: {e}") from e
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise ProviderError(f"Claude declined the request (category: {cat})")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason == "max_tokens" and not text:
            raise ProviderError("Claude hit max_tokens before writing an answer")
        u = resp.usage
        usage = {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None),
                 "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None)}
        return LLMResult(text, self.name, getattr(resp, "model", self.model), usage)


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, temperature: float = 0.4, timeout: float = 300.0, post=None):
        self.api_key, self.model, self.temperature, self.timeout = api_key, model, temperature, timeout
        self._post = post or requests.post

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def generate(self, system: str, prompt: str, json_mode: bool, max_tokens: int = 16000) -> LLMResult:
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": self.temperature, "maxOutputTokens": max_tokens}}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        try:
            r = self._post(url, params={"key": self.api_key}, json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise ProviderError(f"Gemini connection error: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        try:
            text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as e:
            raise ProviderError(f"Gemini returned no text: {str(d)[:300]}") from e
        return LLMResult(text, self.name, self.model, d.get("usageMetadata", {}))


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str, model: str, temperature: float = 0.4, timeout: float = 300.0, post=None):
        self.api_key, self.base_url, self.model, self.temperature, self.timeout = api_key, base_url.rstrip("/"), model, temperature, timeout
        self._post = post or requests.post

    @property
    def available(self) -> bool:
        return bool(self.model) and (bool(self.api_key) or "localhost" in self.base_url or "127.0.0.1" in self.base_url)

    def generate(self, system: str, prompt: str, json_mode: bool, max_tokens: int = 16000) -> LLMResult:
        body = {"model": self.model, "temperature": self.temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = self._post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise ProviderError(f"OpenAI-compatible connection error: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"OpenAI-compatible HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        try:
            text = d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise ProviderError(f"OpenAI-compatible endpoint returned no text: {str(d)[:300]}") from e
        return LLMResult(text, self.name, d.get("model", self.model), d.get("usage", {}))


class LLMRouter:
    """Tries the configured providers in order; one retry per provider on a transient error."""

    def __init__(self, providers: List):
        self.providers = providers

    @classmethod
    def from_config(cls, config) -> "LLMRouter":
        built = {
            "anthropic": AnthropicProvider(config.anthropic_model, config.anthropic_effort, config.llm_timeout_seconds),
            "gemini": GeminiProvider(config.gemini_api_key, config.gemini_model, config.llm_temperature, config.llm_timeout_seconds),
            "openai": OpenAICompatProvider(config.openai_api_key, config.openai_base_url, config.openai_model,
                                           config.llm_temperature, config.llm_timeout_seconds),
        }
        return cls([built[n] for n in config.llm_provider_order if n in built])

    @property
    def available(self) -> bool:
        return any(p.available for p in self.providers)

    def describe(self) -> str:
        live = [f"{p.name}:{p.model}" for p in self.providers if p.available]
        return ", ".join(live) if live else "none configured"

    def generate(self, system: str, prompt: str, json_mode: bool = True, max_tokens: int = 16000) -> LLMResult:
        errors = []
        for p in self.providers:
            if not p.available:
                continue
            for attempt in range(2):
                try:
                    return p.generate(system, prompt, json_mode, max_tokens)
                except ProviderError as e:
                    errors.append(f"{p.name}: {e}")
                    logger.warning("LLM provider %s failed (attempt %d): %s", p.name, attempt + 1, e)
                    transient = any(s in str(e) for s in ("rate limit", "connection", "HTTP 5", "error 5", "overloaded"))
                    if not transient:
                        break
                    time.sleep(3)
        raise AllProvidersFailed("; ".join(errors) or "no LLM provider configured")
