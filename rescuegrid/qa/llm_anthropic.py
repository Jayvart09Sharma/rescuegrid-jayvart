"""Backend for the Claude API (prototyping alternative while the shared Nano is not available).
Needs ANTHROPIC_API_KEY (or an `ant auth login` profile). Written against the Anthropic Python
SDK 1.x reference; not exercised in this environment (no credentials)."""
from __future__ import annotations

from typing import Type, TypeVar

import anthropic
from pydantic import BaseModel

from .llm import LLMError

M = TypeVar("M", bound=BaseModel)


class AnthropicLLM:
    def __init__(self, model: str = "claude-opus-5", timeout: float = 120.0):
        self.name = f"anthropic:{model}"
        self.model = model
        self.client = anthropic.Anthropic(timeout=timeout)

    def complete(self, system: str, user: str, *, max_tokens: int = 1024, temperature: float = 0.0, thinking: bool | None = None) -> str:
        # temperature is intentionally not sent: sampling params are removed on Claude Opus 5+.
        # Server-side refusal fallbacks are on by default (routes a policy decline to another model).
        try:
            resp = self.client.beta.messages.create(
                model=self.model, max_tokens=max(max_tokens, 1024), system=system,
                messages=[{"role": "user", "content": user}],
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except anthropic.APIError as e:
            raise LLMError(f"{self.name}: {e}") from e
        if resp.stop_reason == "refusal":
            raise LLMError(f"{self.name}: request refused ({getattr(resp.stop_details, 'category', None)})")
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def complete_json(self, system: str, user: str, model_cls: Type[M], *, max_tokens: int = 1024, thinking: bool | None = None) -> M:
        try:
            resp = self.client.messages.parse(
                model=self.model, max_tokens=max(max_tokens, 1024), system=system,
                messages=[{"role": "user", "content": user}], output_format=model_cls)
        except anthropic.APIError as e:
            raise LLMError(f"{self.name}: {e}") from e
        if resp.stop_reason == "refusal":
            raise LLMError(f"{self.name}: request refused")
        if resp.parsed_output is None:
            raise LLMError(f"{self.name}: no structured output returned")
        return resp.parsed_output
