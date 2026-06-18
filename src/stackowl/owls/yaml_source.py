"""YamlOwlSource — exposes owls from the ``Settings.owls`` YAML block."""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.owls.base import OwlSource
from stackowl.owls.manifest import OwlAgentManifest


class YamlOwlSource(OwlSource):
    """Reads the ``owls:`` list from ``stackowl.yaml`` via :class:`Settings`.

    Construction is decoupled from :class:`Settings` so callers (tests,
    boot orchestrators, plugin hosts) can supply manifests from any
    parsed source.
    """

    def __init__(self, owls: list[OwlAgentManifest]) -> None:
        self._owls: list[OwlAgentManifest] = list(owls)

    @property
    def source_name(self) -> str:
        return "yaml"

    def list_owls(self) -> list[OwlAgentManifest]:
        log.startup.debug(
            "[owls] yaml_source.list_owls: entry",
            extra={"_fields": {"count": len(self._owls)}},
        )
        # Return a defensive copy so callers cannot mutate the source list.
        result = list(self._owls)
        log.startup.debug(
            "[owls] yaml_source.list_owls: exit",
            extra={"_fields": {"returned": len(result)}},
        )
        return result
