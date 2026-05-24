"""OwlRegistry — holds all registered owl manifests; Secretary is always present."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.exceptions import ManifestValidationError, OwlNotFoundError
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.owls.base import OwlSource
from stackowl.owls.manifest import OwlAgentManifest

if TYPE_CHECKING:
    from stackowl.config.settings import Settings

_SECRETARY_NAME = "secretary"


def _make_default_secretary() -> OwlAgentManifest:
    """Construct the default language-neutral Secretary manifest."""
    return OwlAgentManifest(
        name=_SECRETARY_NAME,
        role="primary-assistant",
        system_prompt=(
            "You are a helpful personal assistant. "
            "Respond clearly and concisely in the language the user addresses you in. "
            "On the first message of a session, briefly state that you are an AI assistant."
        ),
        model_tier="powerful",
    )


class OwlRegistry:
    """Holds loaded owl manifests. Secretary is mandatory and cannot be removed.

    Implements ``HealthContributor`` structurally.
    """

    def __init__(self) -> None:
        self._owls: dict[str, OwlAgentManifest] = {}
        self._sources: list[OwlSource] = []
        self._source_map: dict[str, list[str]] = {}

    def register(self, manifest: OwlAgentManifest, source_name: str | None = None) -> None:
        if manifest.name in self._owls:
            raise ManifestValidationError("name", f"duplicate owl name: {manifest.name!r}")
        self._owls[manifest.name] = manifest
        if source_name:
            self._source_map.setdefault(source_name, []).append(manifest.name)
        log.startup.debug(
            "[owls] registry.register: owl registered",
            extra={"_fields": {"name": manifest.name, "role": manifest.role, "source": source_name}},
        )

    def unregister_source(self, source_name: str) -> int:
        """Remove all owls registered under source_name. Returns count removed."""
        log.startup.debug(
            "[owls] registry.unregister_source: entry",
            extra={"_fields": {"source": source_name}},
        )
        names = self._source_map.pop(source_name, [])
        removed = 0
        for name in names:
            if name == _SECRETARY_NAME:
                log.startup.warning(
                    "[owls] registry.unregister_source: skipping mandatory secretary",
                    extra={"_fields": {"source": source_name}},
                )
                continue
            if self._owls.pop(name, None) is not None:
                removed += 1
        log.startup.debug(
            "[owls] registry.unregister_source: exit",
            extra={"_fields": {"source": source_name, "removed": removed}},
        )
        return removed

    def deregister(self, name: str) -> None:
        """Remove an owl from the registry.

        Secretary is mandatory and cannot be removed (raises
        :class:`ManifestValidationError`).  Removing an unknown owl raises
        :class:`OwlNotFoundError`.
        """
        log.startup.debug(
            "[owls] registry.deregister: entry",
            extra={"_fields": {"name": name}},
        )
        if name == _SECRETARY_NAME:
            log.startup.warning(
                "[owls] registry.deregister: refused — secretary is mandatory",
                extra={"_fields": {"name": name}},
            )
            raise ManifestValidationError("name", "Secretary cannot be removed")
        if name not in self._owls:
            log.startup.warning(
                "[owls] registry.deregister: unknown owl",
                extra={"_fields": {"name": name, "known": sorted(self._owls)}},
            )
            raise OwlNotFoundError(name)
        del self._owls[name]
        log.startup.info(
            "[owls] registry.deregister: exit",
            extra={"_fields": {"name": name, "remaining": len(self._owls)}},
        )

    def register_source(self, source: OwlSource) -> None:
        """Register an :class:`OwlSource` (called at startup and by plugins)."""
        log.startup.debug(
            "[owls] registry.register_source: entry",
            extra={"_fields": {"source": source.source_name}},
        )
        self._sources.append(source)
        log.startup.debug(
            "[owls] registry.register_source: exit",
            extra={"_fields": {"source": source.source_name, "total_sources": len(self._sources)}},
        )

    def sources(self) -> list[OwlSource]:
        """Return the registered owl sources (read-only copy)."""
        return list(self._sources)

    def get(self, name: str) -> OwlAgentManifest:
        if name not in self._owls:
            raise OwlNotFoundError(name)
        return self._owls[name]

    def all(self) -> list[OwlAgentManifest]:
        return list(self._owls.values())

    def list(self) -> list[OwlAgentManifest]:
        """Return all registered owls sorted by name."""
        return sorted(self._owls.values(), key=lambda m: m.name)

    def has_secretary(self) -> bool:
        return _SECRETARY_NAME in self._owls

    @property
    def contributor_name(self) -> str:
        return "owl_registry"

    async def health_check(self) -> HealthStatus:
        log.startup.debug(
            "[owls] registry.health_check: entry",
            extra={"_fields": {"owl_count": len(self._owls)}},
        )
        if not self.has_secretary():
            log.startup.warning(
                "[owls] registry.health_check: secretary missing",
                extra={"_fields": {"owl_count": len(self._owls)}},
            )
            return HealthStatus(
                name=self.contributor_name,
                status="down",
                message="Secretary owl is not registered",
                latency_ms=0,
            )

        degraded: list[str] = [name for name, manifest in self._owls.items() if manifest.max_concurrent_requests <= 0]
        if degraded:
            log.startup.warning(
                "[owls] registry.health_check: degraded owls detected",
                extra={"_fields": {"owls": degraded}},
            )
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=f"Owls with non-positive concurrency: {', '.join(sorted(degraded))}",
                latency_ms=0,
            )

        log.startup.debug(
            "[owls] registry.health_check: exit",
            extra={"_fields": {"status": "ok", "owl_count": len(self._owls)}},
        )
        return HealthStatus(
            name=self.contributor_name,
            status="ok",
            message=None,
            latency_ms=0,
        )

    @classmethod
    def with_default_secretary(cls) -> OwlRegistry:
        """Bootstrap a registry with a language-neutral Secretary manifest."""
        registry = cls()
        registry.register(_make_default_secretary())
        return registry

    @classmethod
    def from_settings(cls, settings: Settings) -> OwlRegistry:
        """Build a registry from ``Settings.owls``; injects Secretary if absent."""
        log.startup.debug(
            "[owls] registry.from_settings: entry",
            extra={"_fields": {"owls_in_settings": len(settings.owls)}},
        )
        registry = cls()
        owl_names = {owl.name for owl in settings.owls}
        if _SECRETARY_NAME not in owl_names:
            log.startup.debug(
                "[owls] registry.from_settings: injecting default secretary",
                extra={"_fields": {"reason": "missing_from_settings"}},
            )
            registry.register(_make_default_secretary())
        for manifest in settings.owls:
            registry.register(manifest)
        log.startup.info(
            "[owls] registry.from_settings: loaded",
            extra={"_fields": {"count": len(registry._owls)}},
        )
        return registry
