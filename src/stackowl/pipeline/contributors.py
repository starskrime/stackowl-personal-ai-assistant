"""PromptContributor — the extension point through which a plugin ADDS to the prompt.

Designed in ``docs/reference-mapping/designs/D16.3.md``. Bakir answered E2 on
2026-08-21 with "everything, including prompt contributors", against the
recommendation, and the design doc keeps the argument that was traded under a
SUPERSEDED banner rather than deleting it.

WHY THIS IS THE HEAVIEST EXTENSION POINT ON THE PLATFORM, stated plainly because the
next person to touch it should know: a contributor writes into the SYSTEM PROMPT. A
tool has to be called by the model; this simply speaks, on every turn, uncovered by
consent, riding the frozen prefix for the life of an incarnation. That is why it is
capability-gated (``prompt_contributor``), why a contributor may only ADD, and why it
runs exactly once per incarnation rather than per turn.

WHAT THE PLATFORM ALREADY DID FOR THE SEVEN BUILT-INS, and what this names rather than
invents. Each of `base`, `capabilities`, `persona`, `owls`, `skills`, `profile` and
`stable_context` already has a stable name, renders to a string where ``""`` means
"contribute nothing", is individually fail-open, and holds a fixed position. The
Protocol is that contract, written down.

THREE GUARDS, each for a reason rather than by reflex:

* A contributor that RAISES contributes ``""`` and is logged at ERROR. Prompt building
  must never cost a turn — the same contract the seven inline blocks already have.
* A contributor that HANGS is abandoned. Third-party code on the cold-build path must
  never be able to hold a conversation open.
* A contributor cannot overwrite a built-in part, cannot reorder the prefix, and cannot
  read per-turn state. The last one is D01.1's lesson: the prompt is frozen for the
  life of an incarnation, so a per-turn conditional becomes a session-long falsehood.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from stackowl.infra.observability import log

#: How long ONE contributor gets to render. Short on purpose: this is on the cold-build
#: path of a real conversation, and anything slow belongs in a cache the contributor
#: fills elsewhere, not in the turn the user is waiting on.
DEFAULT_RENDER_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class PromptContext:
    """The read-only view a contributor may see.

    DELIBERATELY NARROWER THAN ``PipelineState``. A contributor that can reach the whole
    state can reach ``state.intent_class`` — and D01.1 already paid for that once: a
    per-turn conditional was frozen into a session-long prompt, so a conversation that
    opened with "hi" carried a capability-less prompt for its entire life. Nothing
    volatile is exposed here, which makes that mistake unavailable rather than
    discouraged.
    """

    owl_name: str
    channel: str
    session_key: str
    lean: bool = False


class PromptContributor:
    """Add one named part to the system prompt. Never replace one.

    A PLAIN CLASS, not a Protocol, and the difference is load-bearing. The design doc
    argued for a Protocol on the grounds that `LifecycleHook` is in ``_ABC_NAMES`` and
    is not an ABC either — true, but it is a plain CLASS. The loader discovers extension
    points with ``issubclass``, and a ``runtime_checkable`` Protocol carrying a DATA
    member (``name: str``) raises at that call:

        TypeError: Protocols with non-method members don't support issubclass()

    Found by implementation: three unrelated plugin tests started failing because every
    plugin load raised. Corrected here rather than worked around, and recorded in
    designs/D16.3.md — a Protocol would have made the eighth point undiscoverable by the
    very mechanism that defines it.

    Subclass and set ``name``; override ``render``. The default contributes nothing, so
    a half-written contributor is inert rather than broken.
    """

    #: Audit key AND log field stem (``{name}_len``). One string, three uses.
    name: str = ""

    async def render(self, ctx: PromptContext) -> str:
        """Return this part's text. ``""`` contributes nothing."""
        return ""


class PromptContributorRegistry:
    """Process-wide registry of plugin-contributed prompt parts."""

    def __init__(self) -> None:
        self._by_name: dict[str, PromptContributor] = {}
        #: name -> the plugin that registered it, so an unload can drop exactly its
        #: own contributors. Without this a contributor outlives its plugin and keeps
        #: writing into the prompt with nothing owning it.
        self._source_of: dict[str, str] = {}

    def register(
        self, contributor: PromptContributor, source_name: str | None = None
    ) -> None:
        """Register a contributor. ``source_name`` is the owning plugin.

        THE KEYWORD IS THE LOADER'S CONTRACT, not a convenience. `LocalPluginLoader`
        calls ``registry.register(instance, source_name=manifest.name)`` for EVERY
        extension point, and the first version of this method did not accept it — so a
        real plugin failed at boot with a TypeError while twenty unit tests passed,
        because every one of them called ``register(contributor)`` directly. Found by
        installing a real plugin, which is the D16.1 lesson repeating on the item that
        recorded it.
        """
        name = str(getattr(contributor, "name", "") or "").strip()
        if not name:
            log.engine.error(
                "[plugins] prompt contributor has no name — refusing to register; "
                "the name is its audit key and its log field, so an unnamed part "
                "could move prompt_hash with nothing able to say which part moved",
            )
            return
        self._by_name[name] = contributor
        if source_name:
            self._source_of[name] = source_name
        log.engine.info(
            "[plugins] prompt contributor registered",
            extra={"_fields": {"name": name, "source": source_name or "",
                               "total": len(self._by_name)}},
        )

    def unregister(self, name: str) -> None:
        if self._by_name.pop(name, None) is not None:
            self._source_of.pop(name, None)
            log.engine.info(
                "[plugins] prompt contributor unregistered",
                extra={"_fields": {"name": name}},
            )

    def unregister_by_source(self, source_name: str) -> int:
        """Drop every contributor a plugin registered. Returns how many.

        Mirrors ChannelRegistry's method of the same name — the shape the plugin
        unload path already expects of a registry.
        """
        names = [n for n, src in self._source_of.items() if src == source_name]
        for name in names:
            self.unregister(name)
        if names:
            log.engine.info(
                "[plugins] prompt contributors dropped with their plugin",
                extra={"_fields": {"source": source_name, "dropped": names}},
            )
        return len(names)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_name))

    async def render_all(self, ctx: PromptContext) -> dict[str, str]:
        """Render every contributor. Never raises; a failure contributes nothing.

        Returns ``{name: text}``, which the composer appends AFTER the built-ins in
        sorted order. An empty dict — every deployment today — leaves the composed
        prompt byte-identical.
        """
        out: dict[str, str] = {}
        for name in self.names():
            contributor = self._by_name.get(name)
            if contributor is None:  # pragma: no cover — defensive
                continue
            try:
                text = await asyncio.wait_for(
                    contributor.render(ctx),
                    timeout=DEFAULT_RENDER_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                log.engine.error(
                    "[plugins] prompt contributor timed out — contributing nothing; "
                    "the turn proceeds without it",
                    extra={"_fields": {
                        "name": name, "timeout_s": DEFAULT_RENDER_TIMEOUT_SECONDS,
                    }},
                )
                continue
            except Exception as exc:  # noqa: BLE001 — must never cost the turn
                log.engine.error(
                    "[plugins] prompt contributor raised — contributing nothing",
                    exc_info=exc, extra={"_fields": {"name": name}},
                )
                continue
            if not isinstance(text, str):
                log.engine.error(
                    "[plugins] prompt contributor returned a non-string — ignored",
                    extra={"_fields": {"name": name, "got": type(text).__name__}},
                )
                continue
            if text:
                out[name] = text
        return out


_registry: PromptContributorRegistry | None = None


def get_registry() -> PromptContributorRegistry:
    """The process-wide registry (created on first use)."""
    global _registry  # noqa: PLW0603 — one process-wide registry, like HookRegistry
    if _registry is None:
        _registry = PromptContributorRegistry()
    return _registry


def reset_registry() -> None:
    """Drop every registered contributor. For tests and plugin unload."""
    global _registry  # noqa: PLW0603
    _registry = None


__all__ = [
    "DEFAULT_RENDER_TIMEOUT_SECONDS",
    "PromptContext",
    "PromptContributor",
    "PromptContributorRegistry",
    "get_registry",
    "reset_registry",
]
