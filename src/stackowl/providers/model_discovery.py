"""ModelDiscovery — live model listing per protocol; doubles as token validation.

Dispatches by protocol the same way ``providers.registry._build_provider``
does (anthropic / gemini / else-openai, so ``grok`` — OpenAI-compatible —
shares the openai branch). Used by the guided ``/provider add`` flow: the
SAME call that lists real models also proves the token/base_url are good.
"""

from __future__ import annotations

from stackowl.exceptions import ModelDiscoveryError
from stackowl.infra.observability import log


async def list_models(protocol: str, base_url: str | None, api_key: str) -> list[str]:
    """Return the provider's real, current model ids. Raises ModelDiscoveryError on failure."""
    log.engine.debug(
        "[model_discovery] list_models: entry",
        extra={"_fields": {"protocol": protocol, "has_base_url": base_url is not None}},
    )
    try:
        if protocol == "anthropic":
            models = await _list_anthropic(api_key)
        elif protocol == "gemini":
            models = await _list_gemini(api_key)
        else:
            models = await _list_openai(base_url, api_key)
    except Exception as exc:
        log.engine.warning(
            "[model_discovery] list_models: discovery failed",
            extra={"_fields": {"protocol": protocol, "error": str(exc)}},
        )
        raise ModelDiscoveryError(protocol, str(exc)) from exc
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
