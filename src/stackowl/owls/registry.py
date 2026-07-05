"""OwlRegistry — holds all registered owl manifests; Secretary is always present."""

from __future__ import annotations

from collections.abc import Callable
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
            "You are the Secretary, the user's primary agent. You take ownership of "
            "each request and drive it to completion using your tools. You "
            "coordinate, research, act, and deliver — you do not just describe what "
            "could be done. Respond clearly and concisely in the language the user "
            "addresses you in."
        ),
        model_tier="powerful",
        tools=["web_fetch", "browser_extract", "browser_recall_url"],
    )


def _make_default_scout() -> OwlAgentManifest:
    """Roving research persona — full browser surface for multi-step web work."""
    return OwlAgentManifest(
        name="scout",
        role="research-scout",
        system_prompt=(
            "You are Scout, the research owl. You drive the browser to investigate "
            "questions that need fresh information from the open web. You "
            "relentlessly dig until you have the real, current facts; cite source "
            "URLs. Use atomic browser tools to navigate, click, type, extract, and "
            "screenshot, and prefer concrete extracted quotes over summaries."
        ),
        model_tier="powerful",
        tools=[
            "web_fetch",
            "browser_navigate", "browser_extract", "browser_click", "browser_type",
            "browser_screenshot", "browser_scroll", "browser_wait_for",
            "browser_tab_open", "browser_tab_list", "browser_tab_close",
            "browser_cookies_get", "browser_close", "browser_recall_url",
        ],
    )


def _make_default_librarian() -> OwlAgentManifest:
    """Knowledge curation persona — read-only browse + screenshot for archives."""
    return OwlAgentManifest(
        name="librarian",
        role="knowledge-curator",
        system_prompt=(
            "You are Librarian, the knowledge owl — an agentic curator of long-term "
            "knowledge from sources the user trusts. You read pages, extract "
            "structured notes, and archive screenshots, and you interact with sites "
            "(navigate, click, expand) whenever curation requires it to reach the "
            "content worth keeping."
        ),
        model_tier="standard",
        tools=["web_fetch", "browser_extract", "browser_screenshot", "browser_recall_url"],
    )


def _make_default_archivist() -> OwlAgentManifest:
    """Long-term preservation persona — screenshots + recall, minimal nav."""
    return OwlAgentManifest(
        name="archivist",
        role="long-term-preservation",
        system_prompt=(
            "You are Archivist, the preservation owl — an agentic preserver of pages "
            "that may change or disappear. You navigate to the target state and "
            "capture snapshots, interacting with the site as needed to reach what "
            "must be preserved, then store and recall those snapshots."
        ),
        model_tier="fast",
        tools=["web_fetch", "browser_screenshot", "browser_recall_url"],
    )


def _make_default_rca_gatherer() -> OwlAgentManifest:
    """RCA stage 1 (see ``stackowl.parliament.staged_rca``) — organizes
    already-gathered raw evidence into a structured brief. Analysis-only:
    NO tools, so it can only structure the evidence it's handed, never fetch
    new/unverified data (the module's "does not invent evidence" guarantee)."""
    return OwlAgentManifest(
        name="rca_gatherer",
        role="rca-evidence-gatherer",
        system_prompt=(
            "You are the evidence-gatherer in a fixed-stage incident root-cause "
            "analysis. You organize raw evidence you are given into a clear, "
            "factual brief. You never speculate about causes and never fetch or "
            "invent evidence beyond what is provided in the prompt."
        ),
        model_tier="standard",
        tools=[],
    )


def _make_default_hypothesis_owl() -> OwlAgentManifest:
    """RCA stage 2 — proposes a root cause + fix, citing stage 1's brief only.
    Analysis-only: no tools, reasons solely over the provided evidence brief."""
    return OwlAgentManifest(
        name="hypothesis",
        role="rca-hypothesis",
        system_prompt=(
            "You are the hypothesis owl in a fixed-stage incident root-cause "
            "analysis. Using ONLY the evidence brief you are given, propose the "
            "single most likely root cause and a reusable fix, citing the "
            "specific evidence for each claim. You never fetch new evidence."
        ),
        model_tier="powerful",
        tools=[],
    )


def _make_default_verifier() -> OwlAgentManifest:
    """RCA stage 3 — the skeptical check: verifies (or rejects) the
    hypothesis strictly against stage 1's evidence brief, never against how
    confident the hypothesis sounds. Analysis-only: no tools."""
    return OwlAgentManifest(
        name="verifier",
        role="rca-verifier",
        system_prompt=(
            "You are the verifier owl in a fixed-stage incident root-cause "
            "analysis. Your only job is to check whether a hypothesis is "
            "concretely supported by the evidence brief you are given — never "
            "by confidence or agreement. Reject any claim the evidence does not "
            "concretely support. You never fetch new evidence."
        ),
        model_tier="powerful",
        tools=[],
    )


_BUILTIN_PERSONA_FACTORIES = (
    _make_default_scout,
    _make_default_librarian,
    _make_default_archivist,
    _make_default_rca_gatherer,
    _make_default_hypothesis_owl,
    _make_default_verifier,
)


def internal_owl_requirements() -> dict[str, Callable[[], OwlAgentManifest]]:
    """Name -> fallback-factory for every owl an INTERNAL module dispatches to
    by fixed name (not user-configurable), fed to
    :func:`stackowl.startup.wiring_audit.audit_owl_wiring` at boot.

    Registering a persona in ``_BUILTIN_PERSONA_FACTORIES`` above already
    covers the common case (a visible, user-facing builtin). This dict is the
    generic safety net: any FUTURE internal module that references a fixed owl
    name (mirroring ``stackowl.parliament.staged_rca.RcaOwls``) adds its
    name/factory pair here, and the boot-time audit self-heals it if it's ever
    missing — instead of silently rerouting to secretary for weeks before
    anyone notices (the evidence_gatherer/hypothesis/verifier incident this
    closes).
    """
    return {
        "rca_gatherer": _make_default_rca_gatherer,
        "hypothesis": _make_default_hypothesis_owl,
        "verifier": _make_default_verifier,
    }


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

    def replace(self, manifest: OwlAgentManifest) -> None:
        """Atomically swap an existing owl's manifest (the edit path).

        Guards existence (the dual of ``register``'s duplicate guard). A single
        dict assignment — the owl is never absent mid-edit (no deregister+register
        empty window). The mandatory-Secretary policy is enforced one layer up at
        the command, not here (``replace`` is a general verb).

        ``_source_map`` provenance is intentionally NOT updated: an edited owl
        keeps its original source. A caller that changes an owl's source ownership
        must ``deregister`` + ``register`` instead."""
        log.startup.debug(
            "[owls] registry.replace: entry",
            extra={"_fields": {"name": manifest.name}},
        )
        if manifest.name not in self._owls:
            raise OwlNotFoundError(manifest.name)
        self._owls[manifest.name] = manifest
        log.startup.debug(
            "[owls] registry.replace: owl replaced",
            extra={"_fields": {"name": manifest.name, "role": manifest.role}},
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

    def secretary_name(self) -> str | None:
        """The mandatory generalist owl's name, or None if not registered.

        The canonical fallback target for delegation self-healing — reuses the
        single source of truth (``_SECRETARY_NAME``) so no caller hardcodes the literal."""
        return _SECRETARY_NAME if self.has_secretary() else None

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

    def register_builtin_personas(self) -> int:
        """Layer in scout, librarian, archivist if not already present.

        Returns the number of personas newly added. Existing user-configured
        owls with the same name are preserved (no override).
        """
        added = 0
        for factory in _BUILTIN_PERSONA_FACTORIES:
            manifest = factory()
            if manifest.name in self._owls:
                continue
            manifest = manifest.model_copy(update={"origin": "builtin"})
            self.register(manifest)
            added += 1
        log.startup.info(
            "[owls] registry.register_builtin_personas: layered defaults",
            extra={"_fields": {"added": added, "total": len(self._owls)}},
        )
        return added

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
