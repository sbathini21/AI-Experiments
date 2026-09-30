"""Provider-neutral LLM layer with cheap/strong routing.

Business logic calls `get_llm("cheap"|"strong").json(system, user, schema)` and never imports a
provider SDK directly. If no provider key is configured, `get_llm` returns None and callers use
their deterministic fallback — the product still works (rules-only extraction and templated reports).
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 8000) -> dict: ...


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError("model returned no JSON object")
    return json.loads(m.group(0))


@dataclass
class AnthropicLLM:
    model: str
    api_key: str
    tier: str

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 8000) -> dict:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key, max_retries=2, timeout=180)
        output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
        kwargs: dict = {}
        if not self.model.startswith("claude-haiku"):
            # Opus/Sonnet 5.x: effort controls depth/cost; extraction-style tasks don't need 'high'.
            output_config["effort"] = "medium" if self.tier == "strong" else "low"
        try:
            if self.model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"):
                # Server-side refusal fallback, routed by Anthropic by refusal category.
                resp = client.beta.messages.create(
                    model=self.model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": user}], output_config=output_config,
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs,
                )
            else:
                resp = client.messages.create(
                    model=self.model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": user}], output_config=output_config, **kwargs,
                )
        except anthropic.RateLimitError as e:
            raise LLMError(f"rate limited: {e}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"anthropic connection error: {e}") from e
        if resp.stop_reason == "refusal":
            raise LLMError("model declined the request")
        if resp.stop_reason == "max_tokens":
            raise LLMError("model output truncated (max_tokens)")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return json.loads(text)


@dataclass
class OpenAILLM:
    model: str
    api_key: str
    tier: str

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 8000) -> dict:
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema, "strict": True}},
                "max_completion_tokens": max_tokens,
            },
            timeout=180,
        )
        if r.status_code >= 400:
            raise LLMError(f"openai error {r.status_code}: {r.text[:300]}")
        return _extract_json(r.json()["choices"][0]["message"]["content"])


@dataclass
class GoogleLLM:
    model: str
    api_key: str
    tier: str

    @property
    def name(self) -> str:
        return f"google:{self.model}"

    def json(self, system: str, user: str, schema: dict, max_tokens: int = 8000) -> dict:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            params={"key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens},
            },
            timeout=180,
        )
        if r.status_code >= 400:
            raise LLMError(f"google error {r.status_code}: {r.text[:300]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        return _extract_json("".join(p.get("text", "") for p in parts))


def get_llm(tier: str) -> LLM | None:
    s = get_settings()
    spec = s.llm_cheap if tier == "cheap" else s.llm_strong
    provider, _, model = spec.partition(":")
    if provider == "anthropic" and s.anthropic_api_key:
        return AnthropicLLM(model, s.anthropic_api_key, tier)
    if provider == "openai" and s.openai_api_key:
        return OpenAILLM(model, s.openai_api_key, tier)
    if provider == "google" and s.google_api_key:
        return GoogleLLM(model, s.google_api_key, tier)
    return None
