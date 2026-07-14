"""Minimal, injectable DeepSeek client built on the OpenAI-compatible SDK."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"


class LLMConfigurationError(RuntimeError):
    """Raised when a real DeepSeek client cannot be configured."""


class LLMRequestError(RuntimeError):
    """Raised after both DeepSeek request attempts fail."""


@dataclass(frozen=True)
class TokenUsage:
    """Token counts retained for cost reporting."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResult:
    """Normalized completion text and token usage."""

    content: str
    usage: TokenUsage


class DeepSeekClient:
    """Small synchronous client with one explicit retry and dependency injection."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is None:
            key = api_key or os.getenv("DEEPSEEK_API_KEY")
            if not key:
                raise LLMConfigurationError(
                    "DEEPSEEK_API_KEY is missing; set it in the repository .env or environment"
                )
            client = OpenAI(
                api_key=key,
                base_url=base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                max_retries=0,
            )
        self.model = (
            model
            if model is not None
            else os.getenv("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL
        )
        self._client = client
        self._sleep = sleep

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.0,
        timeout: float = 30,
    ) -> LLMResult:
        """Create a deterministic completion, retrying once after a short backoff."""
        if temperature != 0.0:
            raise ValueError("DeepSeek temperature must remain fixed at 0.0")
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=list(messages),
                    temperature=0.0,
                    timeout=timeout,
                )
                content = response.choices[0].message.content or ""
                raw_usage = getattr(response, "usage", None)
                usage = TokenUsage(
                    prompt_tokens=int(getattr(raw_usage, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(raw_usage, "completion_tokens", 0) or 0),
                )
                return LLMResult(content=content, usage=usage)
            except Exception as exc:  # SDK exceptions vary by transport and status.
                last_error = exc
                if attempt == 0:
                    self._sleep(0.5 * (2**attempt))
        raise LLMRequestError(f"DeepSeek request failed after 2 attempts: {last_error}") from last_error


def chat(
    messages: Sequence[dict[str, str]],
    *,
    temperature: float = 0.0,
    timeout: float = 30,
    client: Any | None = None,
) -> LLMResult:
    """Convenience function; pass an SDK-compatible client for offline tests."""
    return DeepSeekClient(client=client).chat(
        messages, temperature=temperature, timeout=timeout
    )
