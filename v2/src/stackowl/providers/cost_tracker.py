"""CostTracker — per-call token accounting and daily budget enforcement."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.exceptions import ProviderError
from stackowl.infra.observability import log
from stackowl.providers.cost_tracker_helpers import _MAX_TRACKED_TURNS, TurnCostLedger
from stackowl.providers.pricing.loader import PricingLoader
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID, OwnedRepository

# Re-exported (the bound lives on TurnCostLedger now, B2 split) so callers/tests
# that import ``_MAX_TRACKED_TURNS`` from this module keep working.
__all__ = ["CostRecord", "CostTracker", "DailySummary", "_MAX_TRACKED_TURNS"]

_BUDGET_WARN_RATIO = 0.80


class CostRecord(BaseModel):
    """A single recorded LLM call cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    trace_id: str
    recorded_at: str  # ISO-8601 UTC


class DailySummary(BaseModel):
    """Aggregated daily spend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    total_usd: float
    by_provider: dict[str, float]
    by_model: dict[str, float]
    call_count: int


class CostTracker(OwnedRepository):
    """Records token usage and estimated cost per LLM call.

    Persists each call to SQLite (`cost_records` table) and enforces an
    optional daily USD budget. Emits `budget_80pct_alert` and
    `budget_exceeded` events on the EventBus when thresholds are crossed.
    Subsequent record() calls after budget_exceeded raise ProviderError.

    Owner-scoped: cost rows are stamped with ``owner_id`` and daily totals are
    constrained to it (defaults to the single-user :data:`DEFAULT_PRINCIPAL_ID`,
    so existing behavior is unchanged).
    """

    _table = "cost_records"

    def __init__(
        self,
        db: DbPool,
        event_bus: EventBus,
        daily_limit_usd: float | None = None,
        pricing: PricingLoader | None = None,
        owner_id: str = DEFAULT_PRINCIPAL_ID,
    ) -> None:
        log.engine.debug(
            "[cost_tracker] init: entry",
            extra={"_fields": {"daily_limit_usd": daily_limit_usd}},
        )
        super().__init__(db, owner_id)
        self._bus: EventBus = event_bus
        self._daily_limit_usd: float | None = daily_limit_usd
        self._pricing: PricingLoader = pricing or PricingLoader()
        self._warned_dates: set[str] = set()
        self._exceeded_dates: set[str] = set()
        # E8-S0cost — BOUNDED in-memory per-trace running total (USD). Updated on
        # every record() so a hot cost-pause check (CostPauseGuard) reads a turn's
        # accumulated spend WITHOUT a SQLite query. The bounded FIFO ledger lives in
        # TurnCostLedger (B2 split) so this file stays under the line cap.
        self._turn_ledger: TurnCostLedger = TurnCostLedger()
        log.engine.debug(
            "[cost_tracker] init: exit",
            extra={"_fields": {"pricing_models": len(self._pricing.table)}},
        )

    def _estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int, *, is_local: bool
    ) -> float:
        """Delegate cost estimation to the PricingLoader (locality-aware)."""
        return self._pricing.estimate(model, input_tokens, output_tokens, is_local=is_local)

    async def record(
        self,
        provider_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float,
        trace_id: str = "",
        is_local: bool = False,
    ) -> CostRecord:
        """Record a completed LLM call. Persists to SQLite and checks budget.

        ``is_local`` marks a self-hosted backend so an unknown LOCAL model stays
        $0 while an unknown CLOUD model gets a conservative fallback price (F128).
        Defaults to ``False`` (cloud) so an un-threaded caller fails safe to PAID.
        """
        log.engine.debug(
            "[cost_tracker] record: entry",
            extra={"_fields": {
                "provider": provider_name, "model": model,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "duration_ms": duration_ms,
            }},
        )

        now = datetime.datetime.now(tz=datetime.UTC)
        today = now.date().isoformat()

        if self._daily_limit_usd is not None and today in self._exceeded_dates:
            log.engine.error(
                "[cost_tracker] record: budget already exceeded — blocking call",
                extra={"_fields": {
                    "provider": provider_name, "model": model,
                    "date": today, "limit_usd": self._daily_limit_usd,
                }},
            )
            raise ProviderError(
                "budget",
                ValueError("Budget cap reached — /config set budget.daily_limit_usd <N> to raise it"),
            )

        cost_usd = self._estimate_cost(model, input_tokens, output_tokens, is_local=is_local)
        record = CostRecord(
            provider_name=provider_name, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, trace_id=trace_id, recorded_at=now.isoformat(),
        )

        try:
            await self._db.execute(
                """
                INSERT INTO cost_records (
                    provider_name, model, input_tokens, output_tokens,
                    cost_usd, trace_id, recorded_at, owner_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.provider_name, record.model, record.input_tokens,
                    record.output_tokens, record.cost_usd, record.trace_id,
                    record.recorded_at, self._owner_id,
                ),
            )
        except Exception as exc:
            log.engine.error(
                "[cost_tracker] record: SQLite insert failed",
                exc_info=exc,
                extra={"_fields": {"provider": provider_name, "model": model}},
            )
            raise

        log.engine.info(
            "[cost] %s/%s: $%.6f (%din/%dout tokens, %.1fms)",
            provider_name, model, cost_usd, input_tokens, output_tokens, duration_ms,
            extra={
                "_fields": {
                    "provider": provider_name,
                    "model": model,
                    "cost_usd": cost_usd,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": duration_ms,
                }
            },
        )

        # E8-S0cost — fold this call into the turn's bounded running total BEFORE
        # the daily-cap check (which may raise on the NEXT call, not this one), so
        # a live cost-pause check sees the spend the moment it lands. Keyed by
        # trace_id; the parent turn, its delegated children, and MoA proposers all
        # record under the same trace_id, so the total is the WHOLE turn's spend.
        if trace_id:
            self._turn_ledger.add(trace_id, cost_usd)

        await self._check_budget(today)

        log.engine.debug(
            "[cost_tracker] record: exit",
            extra={"_fields": {"provider": provider_name, "cost_usd": cost_usd}},
        )
        return record

    async def _check_budget(self, date: str) -> None:
        limit = self._daily_limit_usd
        if limit is None or limit <= 0:
            return
        summary = await self.daily_total(date)
        ratio = summary.total_usd / limit if limit > 0 else 0.0
        payload = {"current_usd": summary.total_usd, "limit_usd": limit}
        if summary.total_usd >= limit and date not in self._exceeded_dates:
            self._exceeded_dates.add(date)
            log.engine.error(
                "[cost_tracker] budget exceeded",
                extra={
                    "_fields": {
                        "date": date,
                        "current_usd": summary.total_usd,
                        "limit_usd": limit,
                    }
                },
            )
            self._bus.emit("budget_exceeded", payload)
        elif ratio >= _BUDGET_WARN_RATIO and date not in self._warned_dates:
            self._warned_dates.add(date)
            log.engine.warning(
                "[cost_tracker] budget at %.0f%% of limit",
                ratio * 100,
                extra={
                    "_fields": {
                        "date": date,
                        "current_usd": summary.total_usd,
                        "limit_usd": limit,
                        "ratio": ratio,
                    }
                },
            )
            self._bus.emit("budget_80pct_alert", payload)

    async def daily_total(self, date: str | None = None) -> DailySummary:
        """Aggregate cost_records for the given date (default: today UTC)."""
        target = date or datetime.datetime.now(tz=datetime.UTC).date().isoformat()
        log.engine.debug(
            "[cost_tracker] daily_total: entry",
            extra={"_fields": {"date": target}},
        )
        rows = await self._db.fetch_all(
            """
            SELECT provider_name, model, cost_usd
            FROM cost_records
            WHERE owner_id = ? AND substr(recorded_at, 1, 10) = ?
            """,
            (self._owner_id, target),
        )
        total = 0.0
        by_provider: dict[str, float] = {}
        by_model: dict[str, float] = {}
        for row in rows:
            cost = float(row["cost_usd"])
            total += cost
            by_provider[row["provider_name"]] = by_provider.get(row["provider_name"], 0.0) + cost
            by_model[row["model"]] = by_model.get(row["model"], 0.0) + cost
        summary = DailySummary(
            date=target,
            total_usd=total,
            by_provider=by_provider,
            by_model=by_model,
            call_count=len(rows),
        )
        log.engine.debug(
            "[cost_tracker] daily_total: exit",
            extra={
                "_fields": {
                    "date": target,
                    "total_usd": total,
                    "call_count": len(rows),
                }
            },
        )
        return summary

    def turn_cost_usd(self, trace_id: str) -> float:
        """Return the accumulated USD spend for ``trace_id`` this server lifetime.

        Reads the bounded in-memory running total maintained by :meth:`record` via
        the composed :class:`TurnCostLedger` (B2 split); a hot path for the
        cost-pause check (NO SQLite query). Returns ``0.0`` for an unknown/empty/
        evicted trace.
        """
        return self._turn_ledger.total(trace_id)

    def update_limit(self, daily_limit_usd: float | None) -> None:
        """Hot-reload budget limit (called by ConfigWatcher on settings_reloaded)."""
        log.engine.info(
            "[cost_tracker] update_limit: %s -> %s",
            self._daily_limit_usd,
            daily_limit_usd,
            extra={
                "_fields": {
                    "old_limit_usd": self._daily_limit_usd,
                    "new_limit_usd": daily_limit_usd,
                }
            },
        )
        self._daily_limit_usd = daily_limit_usd
        self._warned_dates.clear()
        self._exceeded_dates.clear()
