"""ConfigSection ABC and ConfigSectionRegistry — extensible settings sections.

Plugins implement :class:`ConfigSection` to declare a new top-level key under
``stackowl.yaml`` (for example ``budget`` or ``providers``).  The
:class:`ConfigSectionRegistry` is a process-wide singleton — sections register
themselves at import time, exactly like slash commands.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from stackowl.infra.observability import log


class ConfigSection(ABC):
    """Abstract base for a settings section. Plugins implement this contract."""

    @property
    @abstractmethod
    def section_name(self) -> str:
        """Top-level key in ``stackowl.yaml`` (e.g. ``budget``, ``providers``)."""
        ...

    @abstractmethod
    def schema(self) -> type[BaseModel]:
        """Pydantic model that validates this section's payload."""
        ...

    @abstractmethod
    def defaults(self) -> BaseModel:
        """Default instance returned when the section is missing from YAML."""
        ...


class ConfigSectionRegistry:
    """Singleton registry of all config sections.

    Open for extension — plugins call :meth:`register` at import time and the
    instance survives until process exit.  Only the test suite is allowed to
    call :meth:`reset` (mirrors the pattern used by :class:`CommandRegistry`).
    """

    _instance: ConfigSectionRegistry | None = None

    def __init__(self) -> None:
        log.config.debug("[commands] config_section_registry.init: entry")
        self._sections: dict[str, ConfigSection] = {}
        log.config.debug("[commands] config_section_registry.init: exit")

    @classmethod
    def instance(cls) -> ConfigSectionRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — test use only."""
        log.config.debug("[commands] config_section_registry.reset: clearing singleton")
        cls._instance = None

    def register(self, section: ConfigSection) -> None:
        """Register a section, idempotent on ``section_name`` collisions."""
        log.config.debug(
            "[commands] config_section_registry.register: entry",
            extra={"_fields": {"section_name": section.section_name}},
        )
        if section.section_name in self._sections:
            log.config.warning(
                "[commands] config_section_registry.register: overriding existing section",
                extra={"_fields": {"section_name": section.section_name}},
            )
        self._sections[section.section_name] = section
        log.config.debug(
            "[commands] config_section_registry.register: exit",
            extra={"_fields": {"section_name": section.section_name, "total": len(self._sections)}},
        )

    def get(self, section_name: str) -> ConfigSection | None:
        return self._sections.get(section_name)

    def all(self) -> list[ConfigSection]:
        """Return all registered sections sorted by ``section_name``."""
        return sorted(self._sections.values(), key=lambda s: s.section_name)


def register_section(section: ConfigSection) -> ConfigSection:
    """Helper used by plugins to self-register a section at import time."""
    ConfigSectionRegistry.instance().register(section)
    return section
