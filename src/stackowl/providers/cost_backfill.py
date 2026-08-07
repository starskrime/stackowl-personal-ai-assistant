"""Re-price historical cost rows that were charged as cloud but are self-hosted.

WHY. ``is_local_url`` was purely syntactic until 2026-08-07, so a provider
configured by HOSTNAME rather than IP literal was classified cloud even when it
resolved to a private address. Measured on the live database that day:

    82,016 rows, every one priced by the unknown-cloud fallback
    ~$2,328 of imaginary spend in the preceding 10 days alone
    priced = 0 / NULL on all of them  (DEBT-15's honesty marker, working)

The marker did its job — nothing ever claimed those dollars were measured — but
every aggregate, the ``/cost`` view, the budget signals and D01.6's baseline were
computed over invented numbers.

WHY THIS IS NOT A SQL MIGRATION, though a migration is what was asked for.
A migration cannot resolve DNS, so it could only target rows by a hardcoded
provider name — which would put one deployment's provider name into a file every
deployment runs, and this codebase forbids vendor names in ``src/`` for exactly
that reason. The same intent is met deployment-agnostically here: ask the CURRENT
locality classifier which configured providers are local, and re-price only
those. A deployment with no local providers changes nothing.

SAFETY. Only rows that are NOT already marked priced are touched, so this is
idempotent and cannot overwrite a real measured price. It runs once per process
guarded by a ``stackowl_meta`` key, and it logs at WARNING with the exact row
count — rewriting recorded history silently would be worse than the wrong
numbers it corrects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.net.host_locality import is_local_url
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool

__all__ = ["reprice_local_history"]

#: Set once the backfill has completed, so it never re-scans on later boots.
_DONE_KEY = "cost_local_reprice_done"


async def _already_done(db: DbPool) -> bool:
    rows = await db.fetch_all(
        "SELECT value FROM stackowl_meta WHERE key = ?", (_DONE_KEY,),
    )
    return bool(rows)


async def reprice_local_history(db: DbPool, settings: Settings) -> int:
    """Zero the cost of historical rows belonging to self-hosted providers.

    Returns the number of rows re-priced. Never raises — a bookkeeping
    correction must not be able to stop the platform from starting.
    """
    log.engine.debug("[cost_backfill] reprice_local_history: entry")
    try:
        if await _already_done(db):
            log.engine.debug("[cost_backfill] already applied — skipping")
            return 0

        local_names = [
            p.name for p in settings.providers if is_local_url(p.base_url)
        ]
        if not local_names:
            # Nothing self-hosted: the historical prices stand as recorded.
            await _mark_done(db)
            log.engine.info(
                "[cost_backfill] no self-hosted providers — nothing to re-price",
            )
            return 0

        placeholders = ",".join("?" for _ in local_names)
        # `priced IS NOT 1` covers both 0 and NULL, and guarantees a row that
        # carries a REAL table price is never overwritten.
        affected = await db.execute_returning_rowcount(
            f"UPDATE cost_records SET cost_usd = 0.0, priced = 1 "  # noqa: S608 — placeholders are generated, values are bound
            f"WHERE provider_name IN ({placeholders}) AND priced IS NOT 1",
            tuple(local_names),
        )
        await _mark_done(db)
        if affected:
            # WARNING: this rewrote recorded history. Loud by design.
            log.engine.warning(
                "[cost_backfill] re-priced historical rows to $0 — these providers "
                "are self-hosted and were charged the unknown-cloud fallback",
                extra={"_fields": {
                    "rows": affected,
                    "providers": sorted(local_names),
                }},
            )
        return int(affected)
    except Exception as exc:  # noqa: BLE001 — B5: never break startup over bookkeeping
        log.engine.error(
            "[cost_backfill] reprice failed — costs left as recorded",
            exc_info=exc,
        )
        return 0


async def _mark_done(db: DbPool) -> None:
    import time

    await db.execute(
        "INSERT INTO stackowl_meta (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (_DONE_KEY, "1", time.time()),
    )
