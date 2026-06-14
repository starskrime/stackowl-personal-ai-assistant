"""Resolve a model's effective context window (tokens) for per-turn budgeting.

Precedence: per-provider config `context_chars` override → provider probe
(ollama /api/show) → known cloud default → conservative fallback. Clamped to a
ceiling so a huge-window model can't claim more KV-cache RAM than the host has.
Memoized per (provider_name, model). NEVER raises — any probe failure logs and
returns the fallback. A sync `cached_window` lets the provider read the already-
resolved value (to send num_ctx) without re-probing.
"""
from __future__ import annotations

import httpx

from stackowl.infra.observability import log

DEFAULT_WINDOW_FALLBACK = 8192
WINDOW_CEILING_DEFAULT = 16384
_CLOUD_DEFAULT = 200_000
_PROBE_TIMEOUT = 4.0

_WINDOW_CACHE: dict[tuple[str, str], int] = {}


def _clamp(tokens: int) -> int:
    return max(1, min(int(tokens), WINDOW_CEILING_DEFAULT))


def window_from_config(*, context_chars: int) -> int:
    """Convert a configured CHAR budget to a TOKEN window (~4 chars/token), clamped."""
    return _clamp(context_chars // 4)


def cached_window(provider_name: str, model: str) -> int | None:
    """Sync read of an already-resolved window (None if not yet resolved)."""
    return _WINDOW_CACHE.get((provider_name, model))


async def _probe_ollama(base_url: str, model: str) -> int | None:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/api/show"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.post(url, json={"name": model})
            resp.raise_for_status()
            info = resp.json().get("model_info", {}) or {}
        for key, val in info.items():
            if key.endswith("context_length") and isinstance(val, int) and val > 0:
                return val
        return None
    except Exception as exc:
        log.engine.debug(
            "[model_window] ollama probe failed",
            exc_info=exc, extra={"_fields": {"url": url, "model": model}},
        )
        return None


def _looks_like_ollama(base_url: str | None) -> bool:
    if not base_url:
        return False
    return ":11434" in base_url or "ollama" in base_url.lower()


async def resolve_window(
    *,
    provider_name: str,
    base_url: str | None,
    model: str,
    context_chars: int | None,
    protocol: str,
) -> int:
    """Resolve + memoize the effective window (tokens). Never raises."""
    key = (provider_name, model)
    cached = _WINDOW_CACHE.get(key)
    if cached is not None:
        return cached
    if context_chars is not None and context_chars > 0:
        w = window_from_config(context_chars=context_chars)
        log.engine.debug("[model_window] config override", extra={"_fields": {"model": model, "window": w}})
    elif _looks_like_ollama(base_url) and base_url is not None:
        probed = await _probe_ollama(base_url, model)
        w = _clamp(probed) if probed else DEFAULT_WINDOW_FALLBACK
        log.engine.info(
            "[model_window] resolved via probe",
            extra={"_fields": {"model": model, "probed": probed, "window": w}},
        )
    elif protocol in ("anthropic", "openai", "gemini", "grok") and base_url is None:
        w = _clamp(_CLOUD_DEFAULT)
        log.engine.debug("[model_window] cloud default", extra={"_fields": {"model": model, "window": w}})
    else:
        w = DEFAULT_WINDOW_FALLBACK
        log.engine.info("[model_window] fallback window", extra={"_fields": {"model": model, "window": w}})
    _WINDOW_CACHE[key] = w
    return w
