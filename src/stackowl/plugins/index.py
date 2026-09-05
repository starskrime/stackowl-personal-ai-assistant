"""PluginIndex — local plugin discovery from ~/.stackowl/plugin-index.yaml."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from stackowl.paths import StackowlHome

log = logging.getLogger("stackowl.plugins")

def default_index_path() -> Path:
    """The local plugin index, resolved AGAINST THE CURRENT ENVIRONMENT.

    This was a module-level constant, ``_CONFIG_BASE = StackowlHome.plugins_dir()``.
    Every other path in this platform resolves at call time, which is what lets
    ``STACKOWL_HOME`` isolate an instance and what lets ``tests/conftest.py``
    redirect the home away from the operator's real one. Freezing it here meant a
    process that switched home in-process would read one home's plugin index while
    every other subsystem read the other's — silently, since a missing index is
    indistinguishable from an empty one.

    Measured 2026-09-05: no harm had occurred, because every ``src/`` importer of
    this module is function-local, so the constant resolved at first CALL on the
    production path. The hazard's trigger was a feature that does not exist yet —
    a ``--profile`` flag setting the variable after imports. Resolved at call time
    it cannot arise, and the indirection is gone rather than merely corrected.
    """
    return StackowlHome.plugins_dir()


@dataclass(frozen=True)
class PluginIndexEntry:
    """A single entry in the local plugin index.

    ``sha256`` (PLUG-1) is the hex SHA-256 of the downloadable archive — the
    integrity field a verified remote install (PLUG-2) checks before extracting.
    Legacy/unsigned entries omit it → it defaults to ``""`` (unverifiable, refused
    by the verifier — fail-closed, never auto-installed).
    """

    name: str
    url: str
    version: str
    description: str
    type: str
    sha256: str = ""


class PluginIndex:
    """Reads the local plugin index YAML and looks up plugins by name."""

    def __init__(self, index_path: Path | None = None) -> None:
        # 1. ENTRY
        log.debug("plugins.index.__init__: entry")
        self._path = index_path or (default_index_path() / "plugin-index.yaml")
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
                    # PLUG-1 — optional; legacy entries without it parse fine.
                    sha256=str(meta.get("sha256", "")),
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
