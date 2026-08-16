"""OwlStore — the SQLite home for owl manifests (migration 0118).

BAKIR, 2026-08-16: "Nothing should live in memory, everything in md or sqlite. No
data duplication md file or sqlite."

MEASURED: one owl lived in four places — ``stackowl.yaml`` (authoritative, 12
manifests), ``owl_dna`` (12 trait rows), ``owl_dna_authored`` (17 rows, 5 of them
orphans), and ``owl_profiles`` (0 rows, a table nothing writes) — plus the
in-memory registry. That split is what produced the rename bug the same day:
``display_name`` written correctly to one store and read by nobody on the path
whose behaviour it was supposed to change.

THE CONTRACT. ``manifest_json`` is the single source for an owl. The scalar
columns beside it (``display_name``, ``role``, ``lifecycle``, ``origin``) are a
DERIVED INDEX, written from the manifest by :meth:`upsert` in the same statement,
never edited on their own — so they cannot drift from it. Read the columns to
LIST; read ``manifest_json`` to LOAD.

THE REGISTRY REMAINS IN MEMORY, and that is not a violation of the rule. It holds
live objects rebuilt at boot from this table and no authoritative fact of its own;
what the rule forbids is a durable fact whose only home is memory or YAML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool

__all__ = ["OwlStore"]

_UPSERT_SQL = """
INSERT INTO owls (name, display_name, role, lifecycle, origin, manifest_json,
                  owner_id, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
ON CONFLICT(name) DO UPDATE SET
    display_name = excluded.display_name,
    role         = excluded.role,
    lifecycle    = excluded.lifecycle,
    origin       = excluded.origin,
    manifest_json= excluded.manifest_json,
    updated_at   = excluded.updated_at
"""


class OwlStore:
    """Durable CRUD for owl manifests. Never raises on a read — see :meth:`list_all`."""

    def __init__(self, db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None:
        self._db = db
        self._owner_id = owner_id

    async def count(self) -> int:
        """How many owls this owner has. Used by the boot seed's empty check."""
        rows = await self._db.fetch_all(
            "SELECT COUNT(*) AS c FROM owls WHERE owner_id = ?", (self._owner_id,)
        )
        return int(rows[0]["c"]) if rows else 0

    async def list_all(self) -> list[OwlAgentManifest]:
        """Every owl for this owner, newest schema first.

        A row whose ``manifest_json`` will not parse is SKIPPED and logged at
        ERROR rather than taking the whole registry down with it: one corrupt owl
        must not cost the user the other eleven. Returning fewer owls is
        recoverable; failing to boot is not.
        """
        log.startup.debug("[owls] store.list_all: entry")
        rows = await self._db.fetch_all(
            "SELECT name, manifest_json FROM owls WHERE owner_id = ? ORDER BY name",
            (self._owner_id,),
        )
        out: list[OwlAgentManifest] = []
        for row in rows:
            try:
                out.append(OwlAgentManifest.model_validate_json(row["manifest_json"]))
            except Exception as exc:  # no-hidden-errors: skip loudly, never crash boot
                log.startup.error(
                    "[owls] store.list_all: unreadable manifest — skipping this owl",
                    exc_info=exc,
                    extra={"_fields": {"name": row["name"]}},
                )
        log.startup.info(
            "[owls] store.list_all: exit",
            extra={"_fields": {"rows": len(rows), "loaded": len(out)}},
        )
        return out

    async def upsert(self, manifest: OwlAgentManifest) -> None:
        """Write an owl. The derived columns are set from ``manifest`` here, in the
        same statement as ``manifest_json``, so an index column can never disagree
        with the document it indexes."""
        log.startup.debug(
            "[owls] store.upsert: entry", extra={"_fields": {"name": manifest.name}}
        )
        await self._db.execute(
            _UPSERT_SQL,
            (
                manifest.name,
                manifest.display_name or "",
                manifest.role or "",
                manifest.lifecycle or "on_demand",
                manifest.origin or "",
                manifest.model_dump_json(),
                self._owner_id,
            ),
        )
        log.startup.info(
            "[owls] store.upsert: stored",
            extra={"_fields": {"name": manifest.name,
                               "display_name": manifest.display_name or ""}},
        )

    async def delete(self, name: str) -> bool:
        """Remove an owl. Returns whether a row was actually removed, so a caller
        can tell 'retired it' from 'it was already gone' rather than assuming."""
        before = await self.count()
        await self._db.execute(
            "DELETE FROM owls WHERE name = ? AND owner_id = ?", (name, self._owner_id)
        )
        after = await self.count()
        removed = after < before
        log.startup.info(
            "[owls] store.delete: exit",
            extra={"_fields": {"name": name, "removed": removed}},
        )
        return removed

    async def seed_from(self, manifests: list[OwlAgentManifest]) -> int:
        """One-time import: populate an EMPTY table from the YAML-era manifests.

        Idempotent by the emptiness check, not by luck — once a single owl exists,
        this never runs again, so it cannot resurrect an owl the user has since
        retired. Returns the number seeded (0 when the table was already
        populated), which is what the boot log reports.
        """
        existing = await self.count()
        if existing:
            log.startup.debug(
                "[owls] store.seed_from: already populated — noop",
                extra={"_fields": {"existing": existing}},
            )
            return 0
        seeded = 0
        for m in manifests:
            try:
                await self.upsert(m)
                seeded += 1
            except Exception as exc:  # one bad owl must not abort the whole seed
                log.startup.error(
                    "[owls] store.seed_from: could not seed an owl",
                    exc_info=exc, extra={"_fields": {"name": getattr(m, "name", "?")}},
                )
        log.startup.info(
            "[owls] store.seed_from: exit — owls migrated into sqlite",
            extra={"_fields": {"seeded": seeded, "offered": len(manifests)}},
        )
        return seeded
