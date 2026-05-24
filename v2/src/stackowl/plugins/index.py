"""PluginIndex — local plugin discovery from ~/.stackowl/plugin-index.yaml."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger("stackowl.plugins")

try:
    from platformdirs import user_config_dir  # type: ignore[import]

    _CONFIG_BASE = Path(user_config_dir("stackowl"))
except ImportError:
    _CONFIG_BASE = Path.home() / ".stackowl"


@dataclass(frozen=True)
class PluginIndexEntry:
    """A single entry in the local plugin index."""

    name: str
    url: str
    version: str
    description: str
    type: str


class PluginIndex:
    """Reads the local plugin index YAML and looks up plugins by name."""

    def __init__(self, index_path: Path | None = None) -> None:
        # 1. ENTRY
        log.debug("plugins.index.__init__: entry")
        self._path = index_path or (_CONFIG_BASE / "plugin-index.yaml")
        # 4. EXIT
        log.debug(
            "plugins.index.__init__: exit",
            extra={"_fields": {"path": str(self._path)}},
        )

    def lookup(self, name: str) -> PluginIndexEntry | None:
        """Return the entry for *name*, or ``None`` if not found."""
        # 1. ENTRY
        log.debug("plugins.index.lookup: entry", extra={"_fields": {"name": name}})
        entries = self._load()
        # 2. DECISION
        result = entries.get(name)
        # 4. EXIT
        log.debug(
            "plugins.index.lookup: exit",
            extra={"_fields": {"found": result is not None}},
        )
        return result

    def all(self) -> list[PluginIndexEntry]:
        """Return all entries in the index."""
        # 1. ENTRY
        log.debug("plugins.index.all: entry")
        result = list(self._load().values())
        # 4. EXIT
        log.debug(
            "plugins.index.all: exit",
            extra={"_fields": {"count": len(result)}},
        )
        return result

    def _load(self) -> dict[str, PluginIndexEntry]:
        # 1. ENTRY
        log.debug("plugins.index._load: entry")
        if not self._path.exists():
            # 2. DECISION
            log.debug(
                "plugins.index._load: decision — index file not found, returning empty"
            )
            return {}

        # 3. STEP — read and parse YAML
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("plugins.index._load: yaml parse failed", exc_info=exc)
            return {}

        if not isinstance(raw, dict):
            log.warning("plugins.index._load: unexpected format — not a dict")
            return {}

        result: dict[str, PluginIndexEntry] = {}
        for name, meta in raw.items():
            if not isinstance(meta, dict):
                continue
            try:
                result[name] = PluginIndexEntry(
                    name=name,
                    url=str(meta.get("url", "")),
                    version=str(meta.get("version", "0.0.0")),
                    description=str(meta.get("description", "")),
                    type=str(meta.get("type", "local_plugin")),
                )
            except Exception as exc:
                log.warning(
                    "plugins.index._load: skipping malformed entry %s: %s",
                    name,
                    exc,
                )

        # 4. EXIT
        log.debug(
            "plugins.index._load: exit",
            extra={"_fields": {"count": len(result)}},
        )
        return result
