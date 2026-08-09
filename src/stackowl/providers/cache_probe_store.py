"""Durable proof that a backend honours prompt-cache breakpoints (D01.2).

One row per ``(provider_name, model)``, written ONLY when the endpoint has been
observed to cache something. See ``db/migrations/0104_cache_breakpoint_probes.sql``
for why the learning is asymmetric and ``docs/reference-mapping/designs/D01.2.md``
for the design.

The short version: a zero cache reading is ambiguous — below-minimum marker, cold
cache, and a gateway that strips usage fields all read zero — so believing zeros
would let a field-stripping gateway disable the feature permanently on turn one
with no error. Only positives are durable.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log


@dataclass(frozen=True, slots=True)
class CacheProbe:
    """What one endpoint has been SEEN to do with cache breakpoints."""

    provider_name: str
    model: str
    markers_placed: int
    cache_creation_tokens: int
    cache_read_tokens: int
    first_confirmed_at: str
    last_confirmed_at: str


class CacheProbeStore:
    """Owns cache-probe persistence. One instance, injected — never a global."""

    def __init__(self, db: DbPool) -> None:
        self._db = db

    async def load(self, *, provider_name: str, model: str) -> CacheProbe | None:
        """The confirmed probe for this endpoint, or ``None`` if never confirmed.

        ``None`` means "not yet proven", never "proven dead" — there is no such
        row, by design.

        Never raises: a store that cannot answer must cost knowledge, not a turn.
        """
        log.engine.debug(
            "[cache] probe.load: entry",
            extra={"_fields": {"provider": provider_name, "model": model}},
        )
        try:
            rows = await self._db.fetch_all(
                """
                SELECT provider_name, model, markers_placed, cache_creation_tokens,
                       cache_read_tokens, first_confirmed_at, last_confirmed_at
                FROM cache_breakpoint_probes
                WHERE provider_name = ? AND model = ?
                """,
                (provider_name, model),
            )
        except Exception as exc:
            log.engine.error(
                "[cache] probe.load: read failed — treating this endpoint as unproven",
                exc_info=exc,
                extra={"_fields": {"provider": provider_name, "model": model}},
            )
            return None
        if not rows:
            log.engine.debug(
                "[cache] probe.load: exit — endpoint not yet confirmed",
                extra={"_fields": {"provider": provider_name, "model": model}},
            )
            return None
        row = rows[0]
        return CacheProbe(
            provider_name=str(row["provider_name"]),
            model=str(row["model"]),
            markers_placed=int(row["markers_placed"]),
            cache_creation_tokens=int(row["cache_creation_tokens"]),
            cache_read_tokens=int(row["cache_read_tokens"]),
            first_confirmed_at=str(row["first_confirmed_at"]),
            last_confirmed_at=str(row["last_confirmed_at"]),
        )

    async def record(
        self,
        *,
        provider_name: str,
        model: str,
        markers_placed: int,
        cache_creation_tokens: int,
        cache_read_tokens: int,
        now: str | None = None,
    ) -> None:
        """Persist a CONFIRMED positive; a zero reading is dropped on the floor.

        Invariant I5. The guard is the first statement in the method rather than a
        condition at the call site, so a future caller cannot forget it.

        Token columns are high-water marks: a later, smaller reading means a
        smaller prompt, not a degraded endpoint, and ``first_confirmed_at`` is
        never overwritten so the accumulated knowledge only ever grows.

        Never raises.
        """
        if cache_creation_tokens <= 0 and cache_read_tokens <= 0:
            # NOT an error and NOT evidence of anything — see the migration's
            # header for why believing this zero would disable the feature
            # permanently on a gateway that reports nothing.
            log.engine.debug(
                "[cache] probe.record: zero reading — nothing persisted (I5)",
                extra={"_fields": {"provider": provider_name, "model": model,
                                   "markers_placed": markers_placed}},
            )
            return
        stamp = now or datetime.datetime.now(datetime.UTC).isoformat()
        try:
            await self._db.execute(
                """
                INSERT INTO cache_breakpoint_probes (
                    provider_name, model, markers_placed, cache_creation_tokens,
                    cache_read_tokens, first_confirmed_at, last_confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_name, model) DO UPDATE SET
                    markers_placed        = excluded.markers_placed,
                    cache_creation_tokens = MAX(
                        cache_breakpoint_probes.cache_creation_tokens,
                        excluded.cache_creation_tokens
                    ),
                    cache_read_tokens     = MAX(
                        cache_breakpoint_probes.cache_read_tokens,
                        excluded.cache_read_tokens
                    ),
                    last_confirmed_at     = excluded.last_confirmed_at
                """,
                (provider_name, model, markers_placed, cache_creation_tokens,
                 cache_read_tokens, stamp, stamp),
            )
        except Exception as exc:
            log.engine.error(
                "[cache] probe.record: write failed — the confirmation is lost, the turn is not",
                exc_info=exc,
                extra={"_fields": {"provider": provider_name, "model": model}},
            )
            return
        log.engine.info(
            "[cache] probe.record: exit — endpoint CONFIRMED to honour cache breakpoints",
            extra={"_fields": {"provider": provider_name, "model": model,
                               "markers_placed": markers_placed,
                               "cache_creation": cache_creation_tokens,
                               "cache_read": cache_read_tokens}},
        )
