"""Replaceable LLM provider.

The copilot works fully without an LLM (deterministic grounded composer).
When ``LLM_PROVIDER=anthropic`` is configured, Claude rewrites the evidence
pack into a narrative answer; the result is validated (citations must exist
in the evidence pack) before it is shown, otherwise the deterministic answer
is used.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from platform_common import get_logger

log = get_logger("copilot.llm")


@dataclass
class LLMResult:
    text: str
    model: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> LLMResult: ...


class AnthropicProvider:
    """Claude via the official Anthropic SDK (optional dependency ``anthropic``)."""

    def __init__(
        self, api_key: str, model: str = "claude-opus-5-5", timeout_s: float = 60.0, effort: str = "medium"
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        # Empty key => SDK resolves credentials from its standard sources (ANTHROPIC_API_KEY, profile, ...).
        self._client = anthropic.AsyncAnthropic(api_key=api_key or None, timeout=timeout_s, max_retries=2)
        self.model = model
        self.effort = effort
        self.name = f"anthropic:{model}"

    async def complete(self, system: str, user: str, max_tokens: int = 4000) -> LLMResult:
        a = self._anthropic
        t0 = time.perf_counter()
        try:
            resp = await self._client.beta.messages.create(  # type: ignore[call-overload]
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort},
                # server-side refusal fallback (routes by refusal category)
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.RateLimitError as exc:
            raise LLMError("LLM rate limited") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"LLM API error {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("LLM unreachable") from exc
        latency = round((time.perf_counter() - t0) * 1000, 1)
        if resp.stop_reason == "refusal":
            raise LLMError("LLM declined the request")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise LLMError("LLM returned no text")
        usage = getattr(resp, "usage", None)
        log.info(
            "llm_call",
            extra={
                "llm_model": resp.model,
                "llm_latency_ms": latency,
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
        )
        return LLMResult(
            text=text,
            model=resp.model,
            latency_ms=latency,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )


def build_provider(kind: str, api_key: str, model: str, timeout_s: float, effort: str) -> LLMProvider | None:
    if kind == "none":
        return None
    if kind == "anthropic":
        try:
            return AnthropicProvider(api_key, model, timeout_s, effort)
        except ImportError:
            log.error("anthropic package not installed; falling back to deterministic composer")
            return None
    raise ValueError(f"unknown LLM provider {kind}")
