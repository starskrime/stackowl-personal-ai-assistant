"""OwlSource ABC — pluggable provider of owl manifests for the registry."""

from __future__ import annotations

from abc import ABC, abstractmethod

from stackowl.owls.manifest import OwlAgentManifest


class OwlSource(ABC):
    """Abstract source that provides owl manifests.

    Plugins implement this to inject owls from external manifests,
    MCP servers, or community packs (Epic 10). The registry collects
    sources and materializes their manifests at startup or on demand.
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Unique name for this source (used for namespacing and logging)."""
        ...

    @abstractmethod
    def list_owls(self) -> list[OwlAgentManifest]:
        """Return all owl manifests produced by this source."""
        ...
