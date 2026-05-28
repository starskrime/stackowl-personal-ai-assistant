"""PreferenceStore — persisted per-owner key-value preferences.

Replaces in-memory dict stores (e.g. ``_tier_preferences`` in
``commands.tier_command``) so preferences survive ``stackowl serve`` restarts
and propagate across all channels for the same owner.

owner_key conventions (matches BrowserSessionRegistry):
- CLI:      owner_key = "local"
- Telegram: owner_key = f"telegram:{chat_id}"
- WhatsApp: owner_key = f"whatsapp:{jid}"
"""

from __future__ import annotations

import time

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log


class PreferenceStore:
    """Thin async SQLite wrapper for user_preferences (migration 0028).

    Always scopes queries by owner_key — preferences NEVER leak across owners.
    """

    def __init__(self, db: DbPool) -> None:
        self._db = db
        log.memory.debug("[preferences] store.init: ready")

    async def get(self, owner_key: str, key: str) -> str | None:
        """Return the value for (owner_key, key), or None if unset."""
        log.memory.debug(
            "[preferences] get: entry",
            extra={"_fields": {"owner_key": owner_key, "key": key}},
        )
        rows = await self._db.fetch_all(
            "SELECT value FROM user_preferences WHERE owner_key = ? AND key = ?",
            (owner_key, key),
        )
        value = rows[0]["value"] if rows else None
        log.memory.debug(
            "[preferences] get: exit",
            extra={"_fields": {"owner_key": owner_key, "key": key, "hit": value is not None}},
        )
        return value

    async def set(self, owner_key: str, key: str, value: str) -> None:
        """Upsert preference value. UNIQUE(owner_key, key) constraint handles dedupe."""
        log.memory.debug(
            "[preferences] set: entry",
            extra={"_fields": {"owner_key": owner_key, "key": key, "value_len": len(value)}},
        )
        await self._db.execute(
            """INSERT INTO user_preferences (owner_key, key, value, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(owner_key, key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (owner_key, key, value, time.time()),
        )
        log.memory.info(
            "[preferences] set: exit",
            extra={"_fields": {"owner_key": owner_key, "key": key}},
        )

    async def delete(self, owner_key: str, key: str) -> None:
        await self._db.execute(
            "DELETE FROM user_preferences WHERE owner_key = ? AND key = ?",
            (owner_key, key),
        )
        log.memory.info(
            "[preferences] delete: ok",
            extra={"_fields": {"owner_key": owner_key, "key": key}},
        )

    async def list_for_owner(self, owner_key: str) -> dict[str, str]:
        """Return all preferences for owner_key as {key: value}."""
        log.memory.debug(
            "[preferences] list_for_owner: entry",
            extra={"_fields": {"owner_key": owner_key}},
        )
        rows = await self._db.fetch_all(
            "SELECT key, value FROM user_preferences WHERE owner_key = ?",
            (owner_key,),
        )
        result = {row["key"]: row["value"] for row in rows}
        log.memory.debug(
            "[preferences] list_for_owner: exit",
            extra={"_fields": {"owner_key": owner_key, "n": len(result)}},
        )
        return result
