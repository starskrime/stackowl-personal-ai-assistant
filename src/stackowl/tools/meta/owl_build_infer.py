"""S5 — goal→type inference for owl_build (ADR-A UX).

Turns a plain-language GOAL ("watch my tax deadlines") into a capability preset +
a one-line specialty, and suggests a human display name, with a single fast-tier
LLM call — so a non-technical user is never asked "what type of agent?". The
preset vocabulary is derived from :data:`PRESETS` (data-driven, not a hardcoded
keyword list); the model does the classification, so it is multilingual.

Fail-open is the contract: ANY failure (no provider, provider raises, empty /
unparseable / out-of-vocabulary response) returns ``None`` and the caller falls
back to ASKING the user (the S4 clarify path). Inference never blocks or raises.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.owls.tool_presets import PRESETS
from stackowl.pipeline.services import get_services
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.providers.registry import ProviderRegistry

_FAST_TIER = "fast"
#: Re-extract the first JSON object from a chatty model reply (weak models wrap it).
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _resolve_providers(providers: ProviderRegistry | None) -> ProviderRegistry | None:
    """The injected registry, else the live one from services (None when unwired)."""
    if providers is not None:
        return providers
    return get_services().provider_registry


async def _complete(providers: ProviderRegistry | None, prompt: str) -> str | None:
    """Run ONE fast-tier completion, fail-open to None. Never raises."""
    reg = _resolve_providers(providers)
    if reg is None:
        log.tool.debug("owl_build.infer: no provider registry — skip (ask instead)")
        return None
    try:
        provider = reg.get_by_tier(_FAST_TIER)
        result = await provider.complete([Message(role="user", content=prompt)], model="")
    except Exception as exc:  # fail-open — caller falls back to asking
        log.tool.warning("owl_build.infer: provider call failed — ask instead", exc_info=exc)
        return None
    content = getattr(result, "content", "")
    return content if isinstance(content, str) and content.strip() else None


def _extract_json(text: str) -> dict[str, object] | None:
    """Parse the first JSON object out of a (possibly chatty) reply, else None."""
    for candidate in (text, *( [m.group(0)] if (m := _JSON_RE.search(text)) else [])):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _preset_catalog() -> str:
    """Human-readable preset menu derived from PRESETS — never a hardcoded list."""
    return "\n".join(f"- {name}: {p.specialty}" for name, p in PRESETS.items())


async def infer_capability(
    goal: str, providers: ProviderRegistry | None = None,
) -> tuple[str, str] | None:
    """Infer ``(preset, specialty)`` from a goal sentence, or None (→ ask).

    Returns a preset that is GUARANTEED to be a key of :data:`PRESETS` and a
    one-sentence specialty. Out-of-vocabulary / empty / unparseable replies are
    treated as low-confidence and return None so the caller asks instead.
    """
    goal = (goal or "").strip()
    if not goal:
        return None
    log.tool.debug("owl_build.infer: infer_capability entry", extra={"_fields": {"goal_len": len(goal)}})
    prompt = (
        "Map the user's goal to ONE capability preset from this menu and write a "
        "single concise sentence describing the standing role.\n\n"
        f"Presets:\n{_preset_catalog()}\n\n"
        f'Goal: "{goal}"\n\n'
        'Reply with ONLY JSON: {"preset": "<one preset name>", '
        '"specialty": "<one sentence>"}'
    )
    reply = await _complete(providers, prompt)
    if reply is None:
        return None
    data = _extract_json(reply)
    if data is None:
        log.tool.debug("owl_build.infer: capability reply not JSON — ask instead")
        return None
    preset = str(data.get("preset", "")).strip().lower()
    specialty = str(data.get("specialty", "")).strip()
    if preset not in PRESETS or not specialty:
        log.tool.debug(
            "owl_build.infer: low-confidence capability — ask instead",
            extra={"_fields": {"preset": preset, "has_specialty": bool(specialty)}},
        )
        return None
    log.tool.info(
        "owl_build.infer: capability inferred",
        extra={"_fields": {"preset": preset, "specialty_len": len(specialty)}},
    )
    return preset, specialty


async def suggest_display_name(
    goal: str,
    providers: ProviderRegistry | None = None,
    *,
    avoid: tuple[str, ...] = (),
) -> str | None:
    """Suggest one short human display name for an owl serving ``goal``.

    ``avoid`` is the "suggest another" capability: pass already-rejected names to
    get a fresh one. Returns a cleaned single-token-ish name, or None (→ ask).
    """
    goal = (goal or "").strip()
    if not goal:
        return None
    log.tool.debug(
        "owl_build.infer: suggest_display_name entry",
        extra={"_fields": {"goal_len": len(goal), "avoid": len(avoid)}},
    )
    avoid_clause = (
        f" Do not suggest any of these: {', '.join(avoid)}." if avoid else ""
    )
    prompt = (
        "Suggest a single short, friendly first name (one word) for a personal "
        "assistant that will handle this for the user. Reply with ONLY the name."
        f"{avoid_clause}\n\n"
        f'Goal: "{goal}"'
    )
    reply = await _complete(providers, prompt)
    if reply is None:
        return None
    # Take the first whitespace-delimited token; strip surrounding punctuation/quotes.
    token = reply.strip().split()[0] if reply.strip().split() else ""
    name = token.strip("\"'.,:;!?()[]{}").strip()
    if not name or len(name) > 40 or name.lower() in {a.lower() for a in avoid}:
        log.tool.debug("owl_build.infer: unusable name suggestion — ask instead")
        return None
    log.tool.info("owl_build.infer: name suggested", extra={"_fields": {"name": name}})
    return name


if __name__ == "__main__":  # pragma: no cover — runnable self-check
    import asyncio

    class _FakeProvider:
        def __init__(self, reply: str) -> None:
            self._reply = reply

        def get_by_tier(self, tier: str) -> _FakeProvider:
            return self

        async def complete(self, messages: list[Message], model: str) -> object:
            return type("R", (), {"content": self._reply})()

    async def _demo() -> None:
        good = _FakeProvider('{"preset": "analyst", "specialty": "tracks tax deadlines"}')
        out = await infer_capability("watch my tax deadlines", good)  # type: ignore[arg-type]
        assert out == ("analyst", "tracks tax deadlines"), out
        junk = _FakeProvider("I cannot help with that")
        assert await infer_capability("x", junk) is None  # type: ignore[arg-type]
        oov = _FakeProvider('{"preset": "wizard", "specialty": "magic"}')
        assert await infer_capability("x", oov) is None  # type: ignore[arg-type]
        nm = _FakeProvider("Tony")
        assert await suggest_display_name("watch taxes", nm) == "Tony"  # type: ignore[arg-type]
        assert await infer_capability("", good) is None  # type: ignore[arg-type]
        print("owl_build_infer self-check OK")

    asyncio.run(_demo())
