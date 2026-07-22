"""Resolve a model's effective context window (tokens) for per-turn budgeting.

Precedence: per-provider config `context_chars` override → provider probe
(ollama /api/show, or an active max_tokens probe for any other OpenAI-
compatible endpoint) → known cloud default → conservative fallback. Clamped to
a ceiling so a huge-window model can't claim more KV-cache RAM than the host
has. Memoized per (provider_name, model). NEVER raises — any probe failure
logs and returns the fallback. A sync `cached_window` lets the provider read
the already-resolved value (to send num_ctx) without re-probing.
"""
from __future__ import annotations

import os
import re

import httpx

from stackowl.infra.observability import log

# Floor used only when the model reports nothing (probe failure / no info). NOT a
# cap — by default the window comes DYNAMICALLY from the model's own reported
# context length, with no platform-imposed upper bound. Raised 8192 -> 262144
# (256K) 2026-07-18, then -> 1_000_000 2026-07-22 (owner decision): probing
# genuinely failing should assume a large modern-context model, not a small
# one — this is a probe-failure floor, never a substitute for the real probe.
DEFAULT_WINDOW_FALLBACK = 1_000_000
_CLOUD_DEFAULT = 1_000_000


def _ceiling() -> int | None:
    """Optional upper bound on the resolved window.

    Returns ``None`` by default — NO platform cap, so the window is the model's
    own reported context length (the platform honors what the model supports).
    Set ``STACKOWL_CONTEXT_CEILING`` ONLY to opt into a host-specific cap (e.g. to
    bound KV-cache RAM on a constrained inference server).
    """
    raw = os.environ.get("STACKOWL_CONTEXT_CEILING")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            log.engine.warning(
                "[model_window] invalid STACKOWL_CONTEXT_CEILING — ignoring (no cap)",
                extra={"_fields": {"value": raw}},
            )
    return None
# Raised 4.0 -> 30.0 on 2026-07-22 (owner decision) — a slow-to-respond probe
# endpoint shouldn't fall back to the (now-generous) DEFAULT_WINDOW_FALLBACK
# just because it took a few seconds; this only runs once per (provider, model)
# and is cached, so a longer timeout costs nothing on the steady path.
_PROBE_TIMEOUT = 30.0

_WINDOW_CACHE: dict[tuple[str, str], int] = {}

#: Module-level pooled httpx client for ollama window probes (F129). Created once
#: and reused across every distinct (provider, model) probe so each probe does
#: NOT pay full client/connection-pool setup + teardown. Lazily built; lives for
#: the process. resolve_window memoizes per (provider, model), so the number of
#: probes is already bounded — this just avoids a fresh client per first-probe.
_PROBE_CLIENT: httpx.AsyncClient | None = None


def _new_probe_client() -> httpx.AsyncClient:
    """Construct the pooled probe client (its own seam so tests can override)."""
    return httpx.AsyncClient(timeout=_PROBE_TIMEOUT)


def _get_probe_client() -> httpx.AsyncClient:
    """Return the shared pooled probe client, creating it on first use."""
    global _PROBE_CLIENT
    if _PROBE_CLIENT is None:
        _PROBE_CLIENT = _new_probe_client()
        log.engine.debug("[model_window] pooled probe client created")
    return _PROBE_CLIENT


def _reset_probe_client() -> None:
    """Drop the pooled client (test hygiene; next probe rebuilds it)."""
    global _PROBE_CLIENT
    _PROBE_CLIENT = None


def _clamp(tokens: int) -> int:
    # No platform cap by default — honor the model's own window. An optional
    # STACKOWL_CONTEXT_CEILING bounds it only when a host opts in.
    t = max(1, int(tokens))
    ceil = _ceiling()
    return min(t, ceil) if ceil is not None else t


def window_from_config(*, context_chars: int) -> int:
    """Convert a configured CHAR budget to a TOKEN window (~4 chars/token), clamped."""
    return _clamp(context_chars // 4)


def cached_window(provider_name: str, model: str) -> int | None:
    """Sync read of an already-resolved window (None if not yet resolved)."""
    return _WINDOW_CACHE.get((provider_name, model))


def invalidate(provider_name: str) -> None:
    """Drop every memoized window for ``provider_name`` (all of its models).

    Called on hot config reload when a provider is added/changed/rotated so a
    new base_url or context_chars for an unchanged (name, model) does not keep
    serving the stale window for the life of the process (F123).
    """
    stale = [k for k in _WINDOW_CACHE if k[0] == provider_name]
    for k in stale:
        _WINDOW_CACHE.pop(k, None)
    if stale:
        log.engine.debug(
            "[model_window] invalidate", extra={"_fields": {"provider": provider_name, "dropped": len(stale)}}
        )


async def _probe_ollama(base_url: str, model: str) -> int | None:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/api/show"
    try:
        client = _get_probe_client()
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


# Live incident (2026-07-18): a custom OpenAI-compatible gateway (behind LiteLLM)
# had NO context_chars configured and isn't ollama, so every turn fell to
# what was THEN DEFAULT_WINDOW_FALLBACK=8192 — the model's REAL window turned
# out to be 262144 (32x more), needlessly triggering lean-mode degradation and
# the honest floor's "limited context window" disclaimer on nearly every turn.
# (The fallback itself was raised to 262144 the same day — see its own comment
# above — but probing the real value is still strictly better than any fallback.)
# Neither the
# plain OpenAI `/v1/models` shape nor LiteLLM's richer `/model/info` (scoped out
# for our virtual key) exposes the real window, so this discovers it the same
# way a human would: deliberately request an absurd `max_tokens` against a
# trivial prompt. LiteLLM/vLLM-style backends validate the output budget against
# the model's real context ceiling BEFORE generating anything, so the error
# message states the real number — and the probe costs near-zero tokens
# regardless of the actual window size (fails fast on validation, never
# generates). Some other stack might not pre-validate and instead just run a
# real (but tiny-prompt) generation — that only costs one wasted call, still
# safe, and still degrades to the fallback below if no limit is stated.
_CONTEXT_LIMIT_RE = re.compile(
    r"(?:max_model_len|max_total_tokens|maximum context length is)\D{0,20}?(\d{3,7})",
    re.IGNORECASE,
)
_PROBE_MAX_TOKENS = 999_999_999  # absurd but within int32 — avoids a server-side overflow error masking the real one


async def _probe_openai_compatible(
    base_url: str, model: str, api_key: str | None
) -> int | None:
    """Actively discover a non-ollama OpenAI-compatible endpoint's real context
    window — see the module comment above ``_CONTEXT_LIMIT_RE`` for why/how.
    Returns None (never raises) on any request failure or an unparseable/absent
    limit in the response, so the caller falls back to the conservative default."""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": _PROBE_MAX_TOKENS,
    }
    try:
        client = _get_probe_client()
        resp = await client.post(url, json=payload, headers=headers)
        body = resp.text
    except Exception as exc:
        log.engine.debug(
            "[model_window] openai-compatible probe request failed",
            exc_info=exc, extra={"_fields": {"url": url, "model": model}},
        )
        return None
    m = _CONTEXT_LIMIT_RE.search(body)
    if not m:
        log.engine.debug(
            "[model_window] openai-compatible probe — no context-limit stated "
            "in the response (endpoint may not pre-validate max_tokens)",
            extra={"_fields": {"url": url, "model": model}},
        )
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


async def resolve_window(
    *,
    provider_name: str,
    base_url: str | None,
    model: str,
    context_chars: int | None,
    protocol: str,
    api_key: str | None = None,
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
    elif protocol == "openai" and base_url is not None:
        # A custom OpenAI-compatible endpoint that isn't ollama (e.g. a LiteLLM/
        # vLLM gateway) — actively discover its real window instead of assuming
        # the conservative fallback (see _probe_openai_compatible above).
        probed = await _probe_openai_compatible(base_url, model, api_key)
        w = _clamp(probed) if probed else DEFAULT_WINDOW_FALLBACK
        log.engine.info(
            "[model_window] resolved via openai-compatible probe",
            extra={"_fields": {"model": model, "probed": probed, "window": w}},
        )
    else:
        w = DEFAULT_WINDOW_FALLBACK
        log.engine.info("[model_window] fallback window", extra={"_fields": {"model": model, "window": w}})
    _WINDOW_CACHE[key] = w
    return w
