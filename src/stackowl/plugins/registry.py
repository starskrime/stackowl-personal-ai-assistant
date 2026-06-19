"""PluginRegistry — install, list, and uninstall plugins via SQLite."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing-only
    pass

from stackowl.plugins.manifest import PluginManifest

log = logging.getLogger("stackowl.plugins")


class PluginRegistry:
    """Manages plugin persistence in the StackOwl SQLite database."""

    def __init__(
        self,
        db_path: Path,
        tool_registry: Any = None,
        command_registry: Any = None,
        handler_registry: Any = None,
        channel_registry: Any = None,
        owl_registry: Any = None,
    ) -> None:
        # 1. ENTRY
        log.debug("[plugins] registry.init: entry — db_path=%s", db_path)
        self._db_path = db_path
        self._tool_registry = tool_registry
        self._command_registry = command_registry
        self._handler_registry = handler_registry
        self._channel_registry = channel_registry
        self._owl_registry = owl_registry
        # 4. EXIT
        log.debug("[plugins] registry.init: exit")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def install(self, manifest: PluginManifest, *, sha256: str = "") -> None:
        """Persist *manifest* to the plugins table (INSERT OR REPLACE).

        ``sha256`` (PLUG-2) records the verified integrity digest of a remotely
        installed plugin. A local install passes ``""`` (no remote digest) →
        byte-identical to the pre-PLUG behaviour for local plugins.
        """
        # 1. ENTRY
        log.debug(
            "[plugins] registry.install: entry",
            extra={"_fields": {
                "name": manifest.name, "version": manifest.version,
                "verified": bool(sha256),
            }},
        )
        try:
            # 2. DECISION
            log.debug(
                "[plugins] registry.install: decision — INSERT OR REPLACE for '%s'",
                manifest.name,
            )
            conn = sqlite3.connect(self._db_path)
            try:
                # 3. STEP
                conn.execute(
                    """
                    INSERT OR REPLACE INTO plugins
                        (name, version, type, entry_point, capabilities, config_schema,
                         description, author, license, installed_at, enabled, sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        manifest.name,
                        manifest.version,
                        manifest.type,
                        manifest.entry_point,
                        json.dumps(manifest.capabilities),
                        json.dumps(manifest.config_schema) if manifest.config_schema is not None else None,
                        manifest.description,
                        manifest.author,
                        manifest.license,
                        time.time(),
                        sha256,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.error("[plugins] registry.install: failed", exc_info=exc)
            raise

        # 4. EXIT
        log.debug(
            "[plugins] registry.install: exit",
            extra={"_fields": {"name": manifest.name}},
        )

    def list(self) -> list[PluginManifest]:
        """Return all enabled plugins as :class:`PluginManifest` objects."""
        # 1. ENTRY
        log.debug("[plugins] registry.list: entry")
        try:
            # 2. DECISION
            log.debug("[plugins] registry.list: decision — SELECT enabled plugins")
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                # 3. STEP
                rows = conn.execute(
                    "SELECT * FROM plugins WHERE enabled = 1 ORDER BY name ASC"
                ).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.error("[plugins] registry.list: query failed", exc_info=exc)
            raise

        manifests: list[PluginManifest] = []
        for row in rows:
            d = dict(row)
            try:
                caps_raw = d.get("capabilities", "[]")
                capabilities = json.loads(caps_raw) if caps_raw else []
                schema_raw = d.get("config_schema")
                config_schema = json.loads(schema_raw) if schema_raw else None
                m = PluginManifest(
                    name=d["name"],
                    version=d["version"],
                    type=d["type"],
                    entry_point=d["entry_point"],
                    capabilities=capabilities,
                    config_schema=config_schema,
                    description=d.get("description", ""),
                    author=d.get("author"),
                    license=d.get("license"),
                )
                manifests.append(m)
            except Exception as exc:
                log.error(
                    "[plugins] registry.list: row parse failed",
                    exc_info=exc,
                    extra={"_fields": {"name": d.get("name")}},
                )

        # 4. EXIT
        log.debug("[plugins] registry.list: exit", extra={"_fields": {"count": len(manifests)}})
        return manifests

    def exists(self, name: str) -> bool:
        """Return True when a plugin named *name* is installed (enabled or disabled).

        Used by /plugins enable|disable to distinguish "plugin not found" from
        "plugin already in the requested state" — prevents false-success responses.
        """
        # 1. ENTRY
        log.debug("[plugins] registry.exists: entry", extra={"_fields": {"name": name}})
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT 1 FROM plugins WHERE name = ? LIMIT 1", (name,)
                ).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            log.error("[plugins] registry.exists: query failed", exc_info=exc)
            raise
        result = row is not None
        # 4. EXIT
        log.debug("[plugins] registry.exists: exit", extra={"_fields": {"name": name, "found": result}})
        return result

    async def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or disable a plugin by name."""
        # 1. ENTRY
        log.debug(
            "[plugins] registry.set_enabled: entry",
            extra={"_fields": {"name": name, "enabled": enabled}},
        )
        # 2. DECISION
        log.debug(
            "[plugins] registry.set_enabled: decision — UPDATE enabled=%s for '%s'",
            enabled,
            name,
        )
        try:
            # 3. STEP
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(
                    "UPDATE plugins SET enabled = ? WHERE name = ?",
                    (1 if enabled else 0, name),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.error("[plugins] registry.set_enabled: failed", exc_info=exc)
            raise
        # 4. EXIT
        log.debug(
            "[plugins] registry.set_enabled: exit",
            extra={"_fields": {"name": name, "enabled": enabled}},
        )

    async def uninstall(self, name: str) -> None:
        """Remove the plugin named *name* from the plugins table and all registries."""
        # 1. ENTRY
        log.debug("[plugins] registry.uninstall: entry", extra={"_fields": {"name": name}})

        # 2. DECISION — unregister from in-memory registries first
        log.debug(
            "[plugins] registry.uninstall: decision — removing live registrations for '%s'", name
        )
        removed = 0
        for reg in [
            self._tool_registry,
            self._command_registry,
            self._handler_registry,
            self._channel_registry,
        ]:
            if reg is not None and hasattr(reg, "unregister_by_source"):
                removed += reg.unregister_by_source(name)
        if self._owl_registry is not None and hasattr(self._owl_registry, "unregister_source"):
            removed += self._owl_registry.unregister_source(name)

        # 3. STEP — persist removal in SQLite
        log.debug(
            "[plugins] registry.uninstall: step — registrations removed",
            extra={"_fields": {"count": removed}},
        )
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("DELETE FROM plugins WHERE name = ?", (name,))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.error("[plugins] registry.uninstall: failed", exc_info=exc)
            raise

        # 4. EXIT
        log.debug("[plugins] registry.uninstall: exit", extra={"_fields": {"name": name}})
