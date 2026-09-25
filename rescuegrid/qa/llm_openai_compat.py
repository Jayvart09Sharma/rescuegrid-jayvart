"""Backend for any OpenAI-compatible chat endpoint: the ZGX box's ZRT/vLLM Nemotron-3-Nano-30B
(default), Ollama, or a hosted OpenAI-compatible service. Local inference is the master-plan
default, so this is the primary backend."""
from __future__ import annotations

import json
from typing import Type, TypeVar

from openai import APIError, APITimeoutError, OpenAI
from pydantic import BaseModel

from .llm import LLMError, strip_thinking

M = TypeVar("M", bound=BaseModel)


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, api_key: str = "not-needed", thinking: bool = False, timeout: float = 120.0):
        self.name = f"openai_compatible:{model}@{base_url}"
        self.model, self.thinking = model, thinking
        self.client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=timeout, max_retries=1)

    def _extra_body(self, thinking: bool | None = None) -> dict:
        # Nemotron-specific: reasoning mode is a chat-template switch. Harmless on other servers.
        return {"chat_template_kwargs": {"enable_thinking": self.thinking if thinking is None else thinking}}

    def complete(self, system: str, user: str, *, max_tokens: int = 1024, temperature: float = 0.0, thinking: bool | None = None) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=temperature, max_tokens=max_tokens, extra_body=self._extra_body(thinking),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        except (APIError, APITimeoutError) as e:
            raise LLMError(f"{self.name}: {e}") from e
        return strip_thinking(resp.choices[0].message.content or "")

    def complete_json(self, system: str, user: str, model_cls: Type[M], *, max_tokens: int = 1024, thinking: bool | None = None) -> M:
        schema = model_cls.model_json_schema()
        try:
            resp = self.client.chat.completions.create(
                model=self.model, temperature=0.0, max_tokens=max_tokens, extra_body=self._extra_body(thinking),
                response_format={"type": "json_schema", "json_schema": {"name": model_cls.__name__, "schema": schema}},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        except (APIError, APITimeoutError) as e:
            raise LLMError(f"{self.name}: {e}") from e
        text = strip_thinking(resp.choices[0].message.content or "")
        try:
            return model_cls.model_validate(json.loads(text))
        except Exception as e:
            raise LLMError(f"{self.name}: invalid JSON output: {e}: {text[:200]}") from e
