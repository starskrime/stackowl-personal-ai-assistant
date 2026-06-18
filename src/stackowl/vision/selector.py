"""VisionSelector — pick a healthy vision-capable provider, LOCAL-FIRST (E10-S1).

Self-hosted-first policy ([[feedback_self_hosted_only]]): a local vision provider
(one whose configured ``base_url`` host is loopback/private — locality is derived
from the URL, NOT the routing tier, since a local Ollama is tier ``fast``) is
preferred over any cloud one, so the image stays on the box whenever a local
vision model is configured; cloud is the fallback. When
no vision-capable, healthy provider exists the selector returns a structured
"unavailable" — it NEVER raises (B5), so the S2 tool degrades gracefully on a
host with no vision backend installed.

The returned :class:`VisionSelection` also exposes ``is_local`` so S2 can disclose
egress (a cloud selection means the image leaves the machine), mirroring the pdf
Mode B disclosure precedent.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.infra.observability import log
from stackowl.providers.base import ModelProvider
from stackowl.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class VisionSelection:
    """The outcome of vision-provider selection.

    Exactly one of ``provider`` (available) or ``reason`` (unavailable) is set.
    ``is_local`` tells S2 whether the chosen backend is self-hosted (no egress)
    or cloud (the image leaves the box).
    """

    provider: ModelProvider | None
    is_local: bool
    reason: str | None

    @property
    def available(self) -> bool:
        return self.provider is not None

    @classmethod
    def found(cls, provider: ModelProvider, *, is_local: bool) -> VisionSelection:
        return cls(provider=provider, is_local=is_local, reason=None)

    @classmethod
    def unavailable(cls, reason: str) -> VisionSelection:
        return cls(provider=None, is_local=False, reason=reason)


class VisionSelector:
    """Selects a vision-capable provider from the existing roster, local-first."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def select(self) -> VisionSelection:
        """Return the best vision-capable, healthy provider (local before cloud).

        Order: among providers reporting ``supports_vision`` and whose circuit is
        not OPEN, pick a LOCAL one first, else a cloud one. Structured unavailable
        when none qualify. Never raises.
        """
        # 1. ENTRY
        try:
            providers = self._registry.all()
        except Exception as exc:  # B5 — a broken registry must not crash selection.
            log.engine.error(
                "[vision_selector] select: registry.all() failed — unavailable",
                exc_info=exc,
            )
            return VisionSelection.unavailable("provider registry unavailable")
        log.engine.debug(
            "[vision_selector] select: entry",
            extra={"_fields": {"total_providers": len(providers)}},
        )

        local: list[ModelProvider] = []
        cloud: list[ModelProvider] = []
        for prov in providers:
            try:
                if not prov.supports_vision:
                    continue
                if self._registry.is_open(prov):
                    log.engine.debug(
                        "[vision_selector] select: skipping (circuit open)",
                        extra={"_fields": {"provider": prov.name}},
                    )
                    continue
                # Locality is the base_url-derived self-hosted signal, NOT the
                # routing tier — a local Ollama is tier ``fast`` yet on-box.
                is_local = self._registry.is_local(prov)
                (local if is_local else cloud).append(prov)
            except Exception as exc:  # one odd provider must not abort the scan (B5).
                log.engine.error(
                    "[vision_selector] select: provider probe failed — skipping",
                    exc_info=exc,
                )
                continue

        # 2. DECISION — local-first, cloud fallback.
        if local:
            chosen = local[0]
            log.engine.info(
                "[vision_selector] select: chose LOCAL vision provider",
                extra={"_fields": {"provider": chosen.name}},
            )
            return VisionSelection.found(chosen, is_local=True)
        if cloud:
            chosen = cloud[0]
            log.engine.info(
                "[vision_selector] select: chose CLOUD vision provider (egress)",
                extra={"_fields": {"provider": chosen.name}},
            )
            return VisionSelection.found(chosen, is_local=False)

        # 3. EXIT — none available.
        log.engine.info("[vision_selector] select: no vision-capable provider available")
        return VisionSelection.unavailable(
            "No vision-capable model is configured. Install a local vision model "
            "(e.g. an Ollama llava / llama3.2-vision tag) or configure a "
            "vision-capable cloud provider."
        )
