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

    #: Every store this cascade empties, with the column each is keyed by. One
    #: list, so a table added to the cascade cannot be forgotten by the recorder.
    _IDENTITY_TABLES: tuple[tuple[str, str], ...] = (
        ("owls", "name"),
        ("owl_dna", "owl_name"),
        ("owl_dna_authored", "owl_name"),
        ("dna_checkpoints", "owl_name"),
        ("skill_ownership", "owl_name"),
    )

    async def _record_identity_before_delete(self, name: str) -> None:
        """Write what the cascade is about to remove into the shared audit log.

        SELECT * rather than a key list: a key alone restores nothing, and
        restoring is the only reason the record exists.

        Never raises — the caller is mid-delete.
        """
        from stackowl.audit.deletions import record_deleted_rows

        for table, column in self._IDENTITY_TABLES:
            try:
                rows = await self._db.fetch_all(
                    f"SELECT * FROM {table} WHERE {column} = ?", (name,),  # noqa: S608
                )
            except Exception as exc:
                log.startup.warning(
                    "[owls] store.delete: could not read %s before deleting it",
                    table, exc_info=exc, extra={"_fields": {"owl": name}},
                )
                continue
            await record_deleted_rows(
                self._db,
                table=table,
                rows=[dict(r) for r in rows],
                reason=f"owl {name!r} deleted — identity cascade",
                actor="OwlStore.delete",
            )

    async def delete(self, name: str) -> bool:
        """Remove an owl and EVERY store that holds its identity, in one transaction.

        Returns whether an ``owls`` row was actually removed, so a caller can tell
        'retired it' from 'it was already gone' rather than assuming. The cascade
        runs either way: the live database already holds DNA for six owls whose
        ``owls`` row is long gone, and a delete that skipped them could never
        clean them up.

        THE CASCADE WAS MISSING AND IT LEAKED, measured 2026-08-31: ``owls`` held
        10 rows against ``owl_dna`` 16 and ``owl_dna_authored`` 21. The DNA cleanup
        existed but lived one layer up, in ``owls_command._delete_dna_rows``, so it
        only ran for ``/owls remove`` — every other deletion path left shadows.
        Mirrors :meth:`SkillStore.delete`, which had the same gap fixed on
        2026-08-29 for ``skill_ownership``.

        AND THE SHADOWS PROPAGATED. ``GraphReconciliationHandler`` treats
        ``owl_dna`` and ``skill_ownership`` as AUTHORITATIVE and republishes them
        into the Kuzu graph weekly — graph Owl nodes matched ``owl_dna`` exactly.
        A healthy reconciler faithfully copying a shadow. That is also why this
        needs no graph leg: the reconciler PRUNES as well as backfills, so
        cleaning the SQLite identity lets the loop that already exists heal the
        graph, rather than adding a second writer to Kuzu.

        IDENTITY, NEVER HISTORY. Twelve tables carry ``owl_name``, but
        ``cost_records`` (127k rows), ``task_outcomes``, ``reflections``,
        ``conversations``, ``tasks`` and ``sessions`` are the record of what the
        owl DID, and this programme measures with them. The precedent is
        ``SkillStore.delete`` leaving ``skill_audit`` alone — the restraint that
        made 128 purged skills recoverable.
        """
        before = await self.count()
        # WHAT WAS IN THEM, BEFORE THEY STOP EXISTING. Bakir, 2026-08-31:
        # "snapshot the deleted rows before deleting". A record saying "removed
        # an owl" tells you the damage happened and nothing about undoing it —
        # which is precisely the position the 2026-08-30 purge left us in.
        # Best-effort and never raises: a failed record must not become a failed
        # deletion.
        await self._record_identity_before_delete(name)
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM owls WHERE name = ? AND owner_id = ?", (name, self._owner_id)
            )
            # The evolved DNA, the authored baseline, and the checkpoints between
            # them. owl_dna_authored had NO deleter anywhere in the tree before
            # this; owl_dna and dna_checkpoints had one only in the command layer.
            # OWNER-SCOPED (2026-09-01). The cascade moved here from
            # owls_command.py on 2026-08-31, where its unscoped form was a
            # TRACKED allowlist entry — moving the code silently moved it out of
            # the allowlist's reach, and tests/tenancy would have said so if any
            # run this loop makes had ever included it. Deleting another
            # principal's DNA because their owl shares a name is the exact
            # cross-tenant write the tripwire exists to stop.
            await tx.execute(
                "DELETE FROM owl_dna WHERE owl_name = ? AND owner_id = ?",
                (name, self._owner_id),
            )
            await tx.execute(
                "DELETE FROM owl_dna_authored WHERE owl_name = ? AND owner_id = ?",
                (name, self._owner_id),
            )
            await tx.execute(
                "DELETE FROM dna_checkpoints WHERE owl_name = ? AND owner_id = ?",
                (name, self._owner_id),
            )
            # Which skills this owl owned is part of WHO IT WAS, and the boot
            # hydrator re-attaches these names to owls — a dead owl's rows would
            # be re-attached forever (the same "phantom ownership" that
            # purge_skill_ownership names on the skill side).
            await tx.execute(
                "DELETE FROM skill_ownership WHERE owner_id = ? AND owl_name = ?",
                (self._owner_id, name),
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
