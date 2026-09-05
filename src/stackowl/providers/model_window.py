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
# genuinely failing should assume a large modern-context model, not a small one.
#
# LOWERED 1_000_000 -> 100_000 on 2026-09-01 (Bakir, after the live incident
# below). THE DIRECTION OF THE ERROR IS WHAT MATTERS. This is a bound, and an
# optimistic bound does not degrade — it FAILS. With the fallback at 1,000,000
# against a real 262,144 window, `_output_cap` sized max_tokens against a window
# four times too large and the provider rejected the request outright: a
# three-character "Hey" came back as "I apologize, but I can't complete your
# request right now" (2026-09-01T21:56:19Z, trace d4e875f8). Measured over 68
# context-budget records that day: 39 turns got the true 262,144 from the probe
# and worked; the 7 that fell back to 1,000,000 are the ones that broke.
#
# 100_000 sits BELOW every window this platform has met, so a probe failure now
# costs a little unused capacity instead of the whole turn. It is still only a
# probe-failure floor, never a substitute for the real probe — and since
# `learn_window_from_error` corrects the belief from the provider's own
# rejection, being wrong here is now survivable in both directions.
DEFAULT_WINDOW_FALLBACK = 100_000
_CLOUD_DEFAULT = 100_000


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

#: Which (provider, model) pairs ANSWERED the native metadata endpoint. A measured
#: fact, replacing the URL guess that `openai_provider` used to make independently —
#: two copies of one rule, both wrong for a vLLM server on port 11434.
_NATIVE_WINDOW_API: dict[tuple[str, str], bool] = {}


def answered_native_window_api(provider_name: str, model: str) -> bool:
    """True iff this backend answered its native metadata endpoint when probed.

    The replacement for `":11434" in base_url or "ollama" in base_url.lower()`,
    which said "looks like a vendor" where the caller meant "accepts this option".
    """
    return _NATIVE_WINDOW_API.get((provider_name, model), False)


def reset_window_cache() -> None:
    """Drop both memos (test hygiene)."""
    _WINDOW_CACHE.clear()
    _NATIVE_WINDOW_API.clear()

#: Keys whose cached value is the PROBE-FAILURE FLOOR rather than a measurement.
#:
#: The failure path used to write into `_WINDOW_CACHE` exactly like a success, so
#: a window the code itself called "unknown" was indistinguishable from one it had
#: measured — and nothing brought it back: the rejection correction only ever
#: LOWERS a window, and `invalidate` fires solely on a config reload, which a
#: provider merely RECOVERING is not. MEASURED 2026-09-04 during an 18-hour
#: outage: every context-budget line reported model_window: 100000, the floor
#: exactly, and would have kept doing so after the provider returned.
#:
#: Same cause as the host_locality fix earlier the same day — a transient failure
#: written into a cache that only ever held determinations. A measurement still
#: caches for the life of the process; a floor is provisional.
_PROVISIONAL: set[tuple[str, str]] = set()

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


#: A window smaller than this is not a real model window — it is a parse
#: accident. Below it the correction is refused rather than believed.
_MIN_CREDIBLE_WINDOW = 1024

#: The number a provider states when it rejects an over-long request. Matched on
#: the SHAPE of the sentence, not on any vendor's name or product string: some
#: quantity of tokens described as the maximum context length/window. Kept
#: deliberately narrow — an unrelated 400 must teach nothing.
_STATED_WINDOW_RE = re.compile(
    r"(?:maximum|max)\s+context\s+(?:length|window)\s*(?:is|of|:)?\s*([0-9][0-9_,]{2,})\s*tokens?",
    re.IGNORECASE,
)


def learn_window_from_error(
    provider_name: str, model: str, error_text: object
) -> int | None:
    """Correct the cached window from a provider's own rejection. Never raises.

    THE PROVIDER IS THE ONLY AUTHORITY ON ITS WINDOW, and it says the number out
    loud when it refuses a request. Nothing read it, so a wrong window was reused
    on every following turn — eight recorded ContextWindowExceededError 400s,
    the last of which turned a three-character "Hey" into an apology while the
    platform believed the window was 1,000,000 and it was 262,144.

    THIS IS NOT A FIFTH REPAIR OF THE INPUT ESTIMATE. ``_output_cap`` already
    carries four dated fixes for this same 400, each making the estimate more
    accurate; none of them could help when the WINDOW was wrong by 738k. The
    estimate is a heuristic and may always be slightly wrong — the belief about
    the window is the thing that had no way to be corrected.

    IT ONLY EVER SHRINKS. A rejection proves a window is too small to hold the
    request; it can never prove one is larger. Accepting a LARGER value from an
    error string would let a parse bug manufacture an outage, so a parsed window
    is applied only when it is below what is currently believed.

    Independent of the fallback's VALUE (lowered to 100,000 the same day): this
    makes being wrong about the window survivable in either direction, correcting
    the belief once instead of repeating the rejection every turn.

    Args:
        provider_name: Provider whose window is being corrected.
        model: The resolved model name the request was sent to.
        error_text: The provider's error, in any form; non-strings are ignored.

    Returns:
        The newly learned window, or ``None`` when nothing was learned.
    """
    try:
        text = error_text if isinstance(error_text, str) else ""
        if not text:
            return None
        match = _STATED_WINDOW_RE.search(text)
        if match is None:
            return None
        stated = int(match.group(1).replace(",", "").replace("_", ""))
        if stated < _MIN_CREDIBLE_WINDOW:
            log.engine.warning(
                "[model_window] a rejection stated an implausible context window — "
                "refusing it rather than believing it",
                extra={"_fields": {
                    "provider": provider_name, "model": model, "stated": stated,
                    "floor": _MIN_CREDIBLE_WINDOW,
                }},
            )
            return None
        key = (provider_name, model)
        believed = _WINDOW_CACHE.get(key)
        if believed is not None and stated >= believed:
            # A rejection can prove a window is too SMALL, never too large.
            return None
        _WINDOW_CACHE[key] = stated
        _PROVISIONAL.discard(key)  # learned from the provider itself: measured
        log.engine.info(
            "[model_window] learned the real context window from the provider's "
            "own rejection — the belief that caused it is now corrected",
            extra={"_fields": {
                "provider": provider_name, "model": model,
                "believed": believed, "actual": stated,
            }},
        )
        return stated
    except Exception as exc:  # noqa: BLE001 — a correction may never cost a turn
        log.engine.warning(
            "[model_window] could not read a window from the rejection — leaving "
            "the cached value alone",
            exc_info=exc,
            extra={"_fields": {"provider": provider_name, "model": model}},
        )
        return None


def invalidate(provider_name: str) -> None:
    """Drop every memoized window for ``provider_name`` (all of its models).

    Called on hot config reload when a provider is added/changed/rotated so a
    new base_url or context_chars for an unchanged (name, model) does not keep
    serving the stale window for the life of the process (F123).
    """
    stale = [k for k in _WINDOW_CACHE if k[0] == provider_name]
    for k in stale:
        _WINDOW_CACHE.pop(k, None)
        _PROVISIONAL.discard(k)
    if stale:
        log.engine.debug(
            "[model_window] invalidate", extra={"_fields": {"provider": provider_name, "dropped": len(stale)}}
        )


async def _probe_native_window_api(base_url: str, model: str) -> int | None:
    """Ask a backend for the model's real context length via its native metadata
    endpoint. Returns None for any backend that does not answer.

    NAMED FOR THE CAPABILITY, NOT THE VENDOR. It was `_probe_ollama`, selected by
    `_looks_like_ollama` — a substring guess at a vendor from its URL. The standing
    rule is to dispatch on response SHAPE and declared CAPABILITY, and this function
    IS the capability test: it returns an int or None and swallows every error, so
    calling it costs one fast miss against a backend that does not speak it. There
    is no reason to guess first.
    """
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
    elif base_url is not None and (native := await _probe_native_window_api(base_url, model)):
        # PROBE, DO NOT GUESS. This used to run only when the URL contained
        # ":11434" or "ollama" — a vendor guessed from a substring, which matched a
        # gateway path like `gw.example.com/ollama/v1` and any vLLM server on that
        # port, and MISSED a backend that speaks the endpoint on any other address.
        _NATIVE_WINDOW_API[key] = True
        w = _clamp(native)
        log.engine.info(
            "[model_window] resolved via native metadata probe",
            extra={"_fields": {"model": model, "probed": native, "window": w}},
        )
    elif protocol in ("anthropic", "openai", "gemini", "grok") and base_url is None:
        w = _clamp(_CLOUD_DEFAULT)
        log.engine.debug("[model_window] cloud default", extra={"_fields": {"model": model, "window": w}})
    elif protocol == "openai" and base_url is not None:
        # A custom OpenAI-compatible endpoint that isn't ollama (e.g. a LiteLLM/
        # vLLM gateway) — actively discover its real window instead of assuming
        # the conservative fallback (see _probe_openai_compatible above).
        probed = await _probe_openai_compatible(base_url, model, api_key)
        if probed:
            w = _clamp(probed)
            log.engine.info(
                "[model_window] resolved via openai-compatible probe",
                extra={"_fields": {"model": model, "probed": probed, "window": w}},
            )
        else:
            # A FAILED PROBE IS NOT A RESOLUTION, and until 2026-09-02 it said it
            # was. Both outcomes emitted the same INFO line — "resolved via
            # openai-compatible probe" — differing only in a `probed: null` field,
            # so probe failure was invisible to anyone reading the log rather than
            # parsing it. MEASURED across every retained log: 8 of 210 probes
            # (3.8%) failed, all 8 reporting success.
            #
            # THAT INVISIBILITY IS WHY THE 2026-09-02 OUTAGE WAS POSSIBLE. The
            # floor was then 1,000,000 against a real 262,144 window, so a silent
            # failure produced a window four times too large, `_output_cap` sized
            # max_tokens against it, and a three-character "Hey" came back as an
            # apology. The floor is now 100,000 and a rejection self-corrects the
            # cache — but a failure that reports success would have hidden the
            # next cause just as well.
            w = DEFAULT_WINDOW_FALLBACK
            # PROVISIONAL, not measured — dropped when the provider is next
            # proven healthy so the real window is probed instead of assumed.
            _PROVISIONAL.add(key)
            log.engine.warning(
                "[model_window] probe FAILED — using the probe-failure floor, so "
                "this model's real window is unknown",
                extra={"_fields": {
                    "model": model, "base_url": str(base_url)[:80], "window": w,
                }},
            )
    else:
        w = DEFAULT_WINDOW_FALLBACK
        log.engine.info("[model_window] fallback window", extra={"_fields": {"model": model, "window": w}})
    _WINDOW_CACHE[key] = w
    return w


def remember_probe_failure(provider_name: str, model: str, window: int) -> None:
    """Record ``window`` as a PROVISIONAL floor for a probe that could not run."""
    key = (provider_name, model)
    _WINDOW_CACHE[key] = int(window)
    _PROVISIONAL.add(key)


def invalidate_provisional(provider_name: str) -> None:
    """Drop only the windows this provider GUESSED, keeping the ones it measured.

    Called when the circuit breaker proves the provider answered again
    (``HALF_OPEN -> CLOSED``). A measured window is a fact about the model and
    survives; a probe-failure floor is dropped so the next resolve re-probes.
    """
    stale = [k for k in _PROVISIONAL if k[0] == provider_name]
    for k in stale:
        _PROVISIONAL.discard(k)
        _WINDOW_CACHE.pop(k, None)
    if stale:
        log.engine.info(
            "[model_window] provider recovered — dropping windows that were the "
            "probe-failure floor so the real one is measured",
            extra={"_fields": {"provider": provider_name, "dropped": len(stale)}},
        )
