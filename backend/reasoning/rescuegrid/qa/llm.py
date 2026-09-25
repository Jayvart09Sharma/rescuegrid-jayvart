"""Minimal LLM interface for the Q&A layer plus a factory that picks the backend from settings.
Two calls are all the layer needs: free text, and JSON validated against a pydantic model."""
from __future__ import annotations

import re
from typing import Any, Optional, Protocol, Type, TypeVar

from pydantic import BaseModel

from ..config import Settings, settings as default_settings

M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class BaseLLM(Protocol):
    name: str

    def complete(self, system: str, user: str, *, max_tokens: int = 1024, temperature: float = 0.0, thinking: bool | None = None) -> str: ...

    def complete_json(self, system: str, user: str, model_cls: Type[M], *, max_tokens: int = 1024, thinking: bool | None = None) -> M: ...


_THINK_CLOSE = "</think>"


def strip_thinking(text: str) -> str:
    """Nemotron-3 (via vLLM without a reasoning parser) returns 'reasoning</think>final' with NO
    opening tag when thinking is on; with thinking off there are no tags. Keep only the final part."""
    if _THINK_CLOSE in text:
        text = text.split(_THINK_CLOSE)[-1]
    return re.sub(r"^\s*<think>\s*", "", text).strip()


def make_llm(cfg: Optional[Settings] = None) -> Optional[BaseLLM]:
    cfg = cfg or default_settings
    provider = (cfg.llm_provider or "none").lower()
    if provider in ("none", "off", ""):
        return None
    if provider == "anthropic":
        from .llm_anthropic import AnthropicLLM
        return AnthropicLLM(model=cfg.llm_model if cfg.llm_model.startswith("claude") else "claude-opus-5", timeout=cfg.llm_timeout_s)
    from .llm_openai_compat import OpenAICompatLLM
    return OpenAICompatLLM(base_url=cfg.llm_base_url, model=cfg.llm_model, api_key=cfg.llm_api_key,
                           thinking=cfg.llm_thinking, timeout=cfg.llm_timeout_s)
