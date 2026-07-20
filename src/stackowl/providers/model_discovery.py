"""ModelDiscovery — live model listing per protocol; doubles as token validation.

Dispatches by protocol the same way ``providers.registry._build_provider``
does (anthropic / gemini / else-openai, so ``grok`` — OpenAI-compatible —
shares the openai branch). Used by the guided ``/provider add`` flow: the
SAME call that lists real models also proves the token/base_url are good.
"""

from __future__ import annotations

from stackowl.exceptions import ModelDiscoveryError
from stackowl.infra.observability import log


def _redact(text: str, api_key: str) -> str:
    """Strip any literal occurrence of the raw api_key from an error string
    before it is logged or embedded in an exception (a bad key is the
    PRIMARY expected failure path here — this call doubles as token
    validation — so this is not a rare edge case)."""
    if not api_key:
        return text
    return text.replace(api_key, "***REDACTED***")


async def list_models(protocol: str, base_url: str | None, api_key: str) -> list[str]:
    """Return the provider's real, current model ids. Raises ModelDiscoveryError on failure."""
    log.engine.debug(
        "[model_discovery] list_models: entry",
        extra={"_fields": {"protocol": protocol, "has_base_url": base_url is not None}},
    )
    log.engine.debug(
        "[model_discovery] list_models: decision — dispatching by protocol",
        extra={
            "_fields": {
                "protocol": protocol,
                "openai_compatible": protocol not in ("anthropic", "gemini"),
            }
        },
    )
    try:
        log.engine.debug(
            "[model_discovery] list_models: step — calling provider",
            extra={"_fields": {"protocol": protocol}},
        )
        if protocol == "anthropic":
            models = await _list_anthropic(api_key)
        elif protocol == "gemini":
            models = await _list_gemini(api_key)
        else:
            models = await _list_openai(base_url, api_key)
    except Exception as exc:
        safe_reason = _redact(str(exc), api_key)
        log.engine.warning(
            "[model_discovery] list_models: discovery failed",
            extra={"_fields": {"protocol": protocol, "error": safe_reason}},
        )
        raise ModelDiscoveryError(protocol, safe_reason) from exc
    log.engine.debug(
        "[model_discovery] list_models: exit",
        extra={"_fields": {"protocol": protocol, "model_count": len(models)}},
    )
    return models


async def _list_openai(base_url: str | None, api_key: str) -> list[str]:
    import openai

    client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key or "no-key-needed")
    resp = await client.models.list()
    return [m.id for m in resp.data]


async def _list_anthropic(api_key: str) -> list[str]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    resp = await client.models.list()
    return [m.id for m in resp.data]


async def _list_gemini(api_key: str) -> list[str]:
    from google import genai

    client = genai.Client(api_key=api_key)
    models = await client.aio.models.list()
    return [m.name.removeprefix("models/") for m in models]  # type: ignore[attr-defined]
