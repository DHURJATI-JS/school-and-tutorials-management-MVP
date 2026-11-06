"""Claude backend (Anthropic API) - the cloud alternative to running Gemma 3 locally.

Selected with AI_PROVIDER=anthropic (or AI_PROVIDER=auto when ANTHROPIC_API_KEY is set and no local GPU model is
running). It has the same interface as the OpenAI-compatible ``AIService`` (chat / stream / chat_json / embed /
transcribe / health), so tests, notes, grading, the doubt solver and the assistant work unchanged.

Notes on the Claude API as used here
* official ``anthropic`` SDK, async client, streaming for every request (``get_final_message()`` for non-streamed
  callers) - long answers never hit HTTP timeouts
* Claude Opus 5.5 by default (ANTHROPIC_MODEL); thinking is always on for current models, so ``max_tokens`` is
  sized for thinking + answer and the depth is controlled with ``output_config.effort`` (ANTHROPIC_EFFORT)
* no ``temperature``: current Claude models reject sampling parameters
* refusals: ``fallbacks="default"`` (beta ``server-side-fallback-2026-07-01``) lets the API re-run a request its
  safety filter declined on Anthropic's recommended fallback model; if the whole chain declines, AIDeclined is
  raised and the feature uses its normal offline fallback. (Haiku has no server-side fallback - not sent there.)
* no embeddings / transcription endpoints: the knowledge base uses the built-in lexical vectors and live classes
  use browser captions, exactly as when the AI is offline
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anthropic

from ..config import settings
from .ai import AIBackend, AIDeclined, AIUnavailable, CircuitBreaker, flatten

log = logging.getLogger("classnest.ai.claude")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
DATA_URL = re.compile(r"^data:(image/(?:jpeg|png|webp|gif));base64,(.+)$", re.S)
JSON_RULE = "Reply with ONLY one valid JSON value - no Markdown code fences and no text before or after it."


def describe(exc: BaseException | None) -> str:
    """Human-readable cause for the admin AI page (most specific SDK error class first)."""
    if exc is None:
        return "Unknown error"
    if isinstance(exc, anthropic.AuthenticationError):
        return "The Claude API key was rejected - check ANTHROPIC_API_KEY in .env"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return f"This API key may not use {settings.ANTHROPIC_MODEL}"
    if isinstance(exc, anthropic.NotFoundError):
        return f"Claude model '{settings.ANTHROPIC_MODEL}' was not found - check ANTHROPIC_MODEL"
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude is rate limiting requests - try again shortly"
    if isinstance(exc, anthropic.BadRequestError):
        return f"Claude rejected the request: {str(getattr(exc, 'message', exc))[:160]}"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Claude returned HTTP {exc.status_code}"
    if isinstance(exc, anthropic.APITimeoutError):
        return "Claude timed out"
    if isinstance(exc, anthropic.APIConnectionError):
        return "Can't reach the Claude API - check the internet connection"
    if isinstance(exc, anthropic.AnthropicError):
        return f"Claude is not configured: {str(exc)[:160]}"
    return str(exc)[:200] or exc.__class__.__name__


def to_claude(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """OpenAI-style chat messages -> (system prompt, Claude messages). Data-URL images become base64 image
    blocks placed before the text; system messages are merged into the top-level system prompt."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "system":
            system_parts.append(flatten(content))
            continue
        if role not in ("user", "assistant"):
            continue
        if isinstance(content, list):
            images, texts = [], []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and str(part.get("text") or "").strip():
                    texts.append({"type": "text", "text": str(part["text"])})
                elif part.get("type") == "image_url":
                    match = DATA_URL.match(str((part.get("image_url") or {}).get("url") or ""))
                    if match:
                        images.append({"type": "image", "source": {"type": "base64", "media_type": match.group(1),
                                                                   "data": match.group(2)}})
            blocks: Any = images + texts
            if not blocks:
                continue
        else:
            blocks = str(content or "")
            if not blocks.strip():
                continue  # empty text blocks are rejected by the API
        out.append({"role": role, "content": blocks})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "(conversation start)"})
    if out[-1]["role"] != "user":  # current models don't accept a trailing assistant turn (prefill)
        out.append({"role": "user", "content": "Please continue."})
    return "\n\n".join(p for p in system_parts if p.strip()), out


def claude_schema(schema: Any) -> Any:
    """JSON Schema for structured outputs: object schemas need additionalProperties: false; array-length keywords
    are dropped (the prompt states the count) to stay within the subset structured outputs accepts."""
    if isinstance(schema, dict):
        out = {k: claude_schema(v) for k, v in schema.items() if k not in ("minItems", "maxItems")}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [claude_schema(v) for v in schema]
    return schema


def answer_text(message: Any) -> str:
    """Text of the final message. After a mid-answer fallback the next model continues the partial text, so all
    text blocks together form the answer; `fallback` marker blocks and (empty) thinking blocks are skipped."""
    return "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()


class ClaudeService(AIBackend):
    provider = "anthropic"

    def __init__(self) -> None:
        self._model = settings.ANTHROPIC_MODEL.strip() or "claude-opus-5-5"
        self.provider_label = f"Claude (Anthropic API · {self._model})"
        self.effort = settings.ANTHROPIC_EFFORT.strip().lower()
        if self.effort not in EFFORTS:
            self.effort = "medium"
        self._config_error: str | None = None
        try:
            # An empty key lets the SDK use ANTHROPIC_API_KEY from the environment or an `ant auth login` profile.
            self.client = anthropic.AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY or None,
                timeout=anthropic.Timeout(settings.LLM_TIMEOUT_SECONDS, connect=5.0), max_retries=2)
        except anthropic.AnthropicError as exc:  # e.g. no credentials at all
            self.client = None
            self._config_error = describe(exc)
        self.breaker = CircuitBreaker(settings.AI_CIRCUIT_FAILURES, settings.AI_CIRCUIT_COOLDOWN_SECONDS)
        self._sem = asyncio.Semaphore(max(1, settings.ANTHROPIC_MAX_CONCURRENCY))
        self.available_models = [self._model]
        self.supports_system = self.supports_json_mode = self.supports_vision = True
        self.last_error: str | None = self._config_error
        self.last_ok_at: float | None = None
        self.last_latency_ms: int | None = None
        self.calls = 0
        self.failed_calls = 0

    # ---------------------------------------------------------------- bookkeeping
    @property
    def online(self) -> bool:
        return self.client is not None and self.breaker.allow()

    async def model(self) -> str:
        return self._model

    def _check(self) -> None:
        if self.client is None:
            raise AIUnavailable(self._config_error or "Claude is not configured")
        if not self.breaker.allow():
            raise AIUnavailable(f"AI paused after repeated failures (retry in {self.breaker.retry_in}s)")

    def _ok(self, started: float) -> None:
        self.breaker.success()
        self.last_ok_at = time.time()
        self.last_latency_ms = int((time.monotonic() - started) * 1000)

    def _failure(self, exc: BaseException) -> AIUnavailable:
        """Map an SDK error to AIUnavailable and update the circuit breaker (the SDK already retried 429/5xx)."""
        self.failed_calls += 1
        self.last_error = describe(exc)
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                            anthropic.NotFoundError)) or not isinstance(exc, anthropic.APIError):
            self.breaker.trip()  # configuration problem: retrying won't help until it is fixed
        elif isinstance(exc, anthropic.APIConnectionError) and not isinstance(exc, anthropic.APITimeoutError):
            self.breaker.trip()  # no internet / DNS failure: fail fast instead of stalling every page
        elif not isinstance(exc, anthropic.BadRequestError):  # 400s concern this request only
            self.breaker.failure()  # generation timeout, rate limit, overload, 5xx
        log.warning("Claude call failed: %s", self.last_error)
        return AIUnavailable(self.last_error)

    def _params(self, messages: list[dict[str, Any]], max_tokens: int | None, json_mode: bool,
                schema: dict[str, Any] | None = None) -> dict[str, Any]:
        system, msgs = to_claude(messages)
        output_config: dict[str, Any] = {"effort": self.effort}
        if json_mode and schema:  # structured outputs: the reply is guaranteed to be JSON of this shape
            output_config["format"] = {"type": "json_schema", "schema": claude_schema(schema)}
        elif json_mode:
            system = f"{system}\n\n{JSON_RULE}".strip()
        params: dict[str, Any] = {
            "model": self._model,
            # thinking counts toward max_tokens: callers' limits are sized for answers, so never go below the floor
            "max_tokens": max(int(max_tokens or 0), settings.ANTHROPIC_MAX_TOKENS),
            "messages": msgs,
            "output_config": output_config,
        }
        if system:
            params["system"] = system
        if "haiku" not in self._model:  # Haiku has no server-side fallback
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _client_for(self, timeout: float | None):
        assert self.client is not None
        if timeout:
            return self.client.with_options(timeout=anthropic.Timeout(timeout, connect=5.0))
        return self.client

    # ---------------------------------------------------------------- chat
    async def chat(self, messages: list[dict[str, Any]], *, temperature: float | None = None,
                   max_tokens: int | None = None, json_mode: bool = False, timeout: float | None = None,
                   schema: dict[str, Any] | None = None) -> str:
        """`temperature` is accepted for interface compatibility and ignored (Claude rejects sampling params)."""
        self._check()
        self.calls += 1
        started = time.monotonic()
        try:
            async with self._sem:
                async with self._client_for(timeout).beta.messages.stream(
                        **self._params(messages, max_tokens, json_mode, schema)) as stream:
                    final = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise self._failure(exc) from exc
        if final.stop_reason == "refusal":
            raise AIDeclined("Claude's safety filter declined this request")
        text = answer_text(final)
        if not text:
            raise AIUnavailable("Claude returned an empty answer"
                                + (" (max_tokens reached)" if final.stop_reason == "max_tokens" else ""))
        self._ok(started)
        return text

    async def stream(self, messages: list[dict[str, Any]], *, temperature: float | None = None,
                     max_tokens: int | None = None, timeout: float | None = None) -> AsyncIterator[str]:
        """Yield text deltas. Raises AIUnavailable (possibly after some output) on failure, AIDeclined on refusal."""
        self._check()
        self.calls += 1
        started = time.monotonic()
        produced = False
        try:
            async with self._sem:
                async with self._client_for(timeout).beta.messages.stream(
                        **self._params(messages, max_tokens, False)) as stream:
                    async for event in stream:
                        if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                            if event.delta.text:
                                produced = True
                                yield event.delta.text
                    final = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise self._failure(exc) from exc
        if final.stop_reason == "refusal":
            raise AIDeclined("Claude's safety filter declined this request")
        if not produced:
            raise AIUnavailable("Claude returned an empty answer")
        self._ok(started)

    # ---------------------------------------------------------------- not offered by the Claude API
    async def embed(self, texts: list[str]) -> tuple[None, None]:
        return None, None  # knowledge search uses the built-in lexical vectors

    async def transcribe(self, path: Path) -> None:
        return None  # live classes keep the browser captions as the transcript

    # ---------------------------------------------------------------- health
    async def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "provider": self.provider_label, "base_url": str(self.client.base_url) if self.client else "—",
            "configured_model": self._model, "resolved_model": self._model, "circuit": self.breaker.state,
            "retry_in": self.breaker.retry_in, "last_error": self.last_error, "last_latency_ms": self.last_latency_ms,
            "last_ok_at": self.last_ok_at, "supports_system": True, "supports_json_mode": True,
            "supports_vision": True, "calls": self.calls, "failed_calls": self.failed_calls,
            "embedding_model": "built-in lexical vectors (Claude has no embeddings API)",
            "transcribe_model": "browser captions", "models": [self._model], "reachable": False,
            "effort": self.effort,
        }
        if self.client is None:
            info["error"] = self._config_error
            return info
        started = time.monotonic()
        try:
            model = await asyncio.wait_for(self.client.models.retrieve(self._model), timeout=8)
            info["reachable"] = True
            info["ping_ms"] = int((time.monotonic() - started) * 1000)
            info["models"] = [getattr(model, "id", self._model)]
            if getattr(model, "max_input_tokens", None):
                info["context_window"] = model.max_input_tokens
            if not self.breaker.allow():
                self.breaker.success()  # the API answers again: close the circuit early
                info["circuit"] = self.breaker.state
        except (anthropic.AnthropicError, asyncio.TimeoutError) as exc:
            info["error"] = describe(exc) if isinstance(exc, anthropic.AnthropicError) else "Claude timed out"
        return info
