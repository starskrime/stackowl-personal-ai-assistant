"""CostTracker — per-call token accounting and daily budget enforcement."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
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

    # D01.6 turn metrics (migration 0091). All defaulted, so every existing
    # construction site keeps working untouched and an un-threaded caller
    # records a row that is still valid — just without the new dimensions.
    session_key: str = ""
    # D01.7 — which INCARNATION of that lane produced this call. session_key alone
    # spans every rollover the lane has ever had, which is why the D01.6 baseline
    # saw 10 distinct prompts on one "conversation". D01.1's stability invariant
    # groups by THIS.
    conversation_id: str = ""
    # DEBT-21 — WHICH OWL spent. Required to measure D01.1's invariant I1:
    # a lane can run several owls (the staged RCA drives three against one
    # incident lane) and each MUST have its own prompt, so grouping by
    # conversation_id alone counts a correct design as a violation.
    owl_name: str = ""
    # Provider-reported prefix-cache hits. 0 is AMBIGUOUS by construction: it
    # means "no cache hit" OR "provider does not report" (D01.6 I4). Readers
    # must count reporting rows to tell the two apart — see /cost.
    cached_input_tokens: int = 0
    # SHA-256[:16] of the exact system prompt sent. The D01.1 stability
    # invariant is COUNT(DISTINCT prompt_hash) per session_key == 1.
    prompt_hash: str = ""
    system_prompt_chars: int = 0
    # Time to first content token, streaming calls only. None = not measured.
    ttft_ms: int | None = None


class DailySummary(BaseModel):
    """Aggregated daily spend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    total_usd: float
    by_provider: dict[str, float]
    by_model: dict[str, float]
    call_count: int
    # DEBT-15 — is ``total_usd`` a real total, or does it contain guesses?
    # False when ANY constituent call used the unknown-CLOUD fallback, or when
    # a row predates migration 0101 and its provenance is genuinely unknown.
    # Defaults True so an aggregate nobody has taught to compute it reads as it
    # always did rather than hedging everything.
    all_priced: bool = True


class CostTracker(OwnedRepository):
    """Records token usage and estimated cost per LLM call.

    Persists each call to SQLite (`cost_records` table) and enforces an
    optional daily USD budget. Emits `budget_80pct_alert` and
    `budget_exceeded` events on the EventBus when thresholds are crossed.
    Both are INFORMATIVE ONLY (Bakir, 2026-07-26): recording NEVER refuses a
    call, however far past the threshold the day has run. The one mechanism
    permitted to interrupt is the SOFT per-turn pause (`per_turn_pause_usd`),
    which asks the user and never raises.

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
        *,
        notify_channel: str | None = None,
        notify_target: str | int | None = None,
    ) -> None:
        log.engine.debug(
            "[cost_tracker] init: entry",
            extra={"_fields": {"daily_limit_usd": daily_limit_usd}},
        )
        super().__init__(db, owner_id)
        self._bus: EventBus = event_bus
        self._daily_limit_usd: float | None = daily_limit_usd
        self._pricing: PricingLoader = pricing or PricingLoader()
        # FX-10 — the owner's resolved durable recipient (see
        # notifications.recipient.resolve_owner_addresses), pre-resolved ONCE by
        # the caller. budget_exceeded/budget_80pct_alert are owner-GLOBAL alerts
        # with no per-event recipient, so this is the one place to attach it.
        # None (default) means unresolved — the event still emits (unchanged
        # behavior) but carries no message/target, so EventDeliveryBridge's
        # honest-recipient rail drops it rather than guessing.
        self._notify_channel = notify_channel
        self._notify_target = notify_target
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
        session_key: str = "",
        conversation_id: str = "",
        owl_name: str = "",
        cached_input_tokens: int = 0,
        prompt_hash: str = "",
        system_prompt_chars: int = 0,
        ttft_ms: int | None = None,
    ) -> CostRecord:
        """Record a completed LLM call. Persists to SQLite and checks budget.

        ``is_local`` marks a self-hosted backend so an unknown LOCAL model stays
        $0 while an unknown CLOUD model gets a conservative fallback price (F128).
        Defaults to ``False`` (cloud) so an un-threaded caller fails safe to PAID.

        The five D01.6 turn-metric arguments all default, so a caller that does
        not thread them records exactly the row it recorded before. See
        ``docs/reference-mapping/designs/D01.6.md``.
        """
        log.engine.debug(
            "[cost_tracker] record: entry",
            extra={"_fields": {
                "provider": provider_name, "model": model,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "duration_ms": duration_ms, "session_key": session_key,
                "conversation_id": conversation_id,
            }},
        )
        # D01.6 DECISION point — which naming the provider used for cache stats,
        # or none at all. Logged because "cached=0" alone cannot distinguish a
        # cold cache from a silent provider (I4), and misreading that would make
        # a working D01.1 look like a failed one.
        log.engine.debug(
            "[cost_tracker] record: cache stats source",
            extra={"_fields": {
                "source": "reported" if cached_input_tokens > 0 else "absent_or_zero",
                "cached_input_tokens": cached_input_tokens,
                "provider": provider_name,
            }},
        )

        now = datetime.datetime.now(tz=datetime.UTC)
        today = now.date().isoformat()

        # DEBT-7 (Bakir, 2026-07-26) — recording NEVER refuses a call. This used
        # to raise ProviderError for every call once the daily threshold was
        # crossed ("budget already exceeded — blocking call"). The budget signal
        # is INFORMATIVE ONLY: it must never block, gate, throttle or abort a
        # turn. A cost signal that silently refused to answer would be a worse
        # failure than the missing signal it replaced. The events below still
        # fire; only the refusal is gone. The pipeline's own per-turn budget
        # caps (pipeline/budget/callback.py, BudgetExceeded) are a SEPARATE
        # mechanism and are untouched, as is the soft per-turn pause
        # (per_turn_pause_usd) — which asks the user rather than raising.

        cost_usd = self._estimate_cost(model, input_tokens, output_tokens, is_local=is_local)
        # DEBT-15 — is that figure a PRICE or a guess? Recorded per row because
        # these rows are aggregated (D01.6 metric 3 sums them), and a SUM over a
        # mix of real and fallback dollars cannot be made honest afterwards.
        priced = self._pricing.is_priced(model, is_local=is_local)
        record = CostRecord(
            provider_name=provider_name, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost_usd, trace_id=trace_id, recorded_at=now.isoformat(),
            session_key=session_key, conversation_id=conversation_id, owl_name=owl_name,
            cached_input_tokens=cached_input_tokens,
            prompt_hash=prompt_hash, system_prompt_chars=system_prompt_chars,
            ttft_ms=ttft_ms,
        )

        try:
            await self._db.execute(
                """
                INSERT INTO cost_records (
                    provider_name, model, input_tokens, output_tokens,
                    cost_usd, trace_id, recorded_at, owner_id,
                    session_key, conversation_id, cached_input_tokens, prompt_hash,
                    system_prompt_chars, ttft_ms, priced, owl_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.provider_name, record.model, record.input_tokens,
                    record.output_tokens, record.cost_usd, record.trace_id,
                    record.recorded_at, self._owner_id,
                    record.session_key, record.conversation_id, record.cached_input_tokens,
                    record.prompt_hash, record.system_prompt_chars, record.ttft_ms,
                    int(priced), record.owl_name,
                ),
            )
        except Exception as exc:
            log.engine.error(
                "[cost_tracker] record: SQLite insert failed",
                exc_info=exc,
                extra={"_fields": {"provider": provider_name, "model": model}},
            )
            raise

        # DEBT-15 — "~" and "est," mark a figure derived from the unknown-CLOUD
        # fallback rather than a table price, so the log can never be read as a
        # measurement it is not. A priced call is unchanged.
        log.engine.info(
            "[cost] %s/%s: %s$%.6f (%din/%dout tokens, %.1fms%s)",
            provider_name, model, "" if priced else "~", cost_usd,
            input_tokens, output_tokens, duration_ms, "" if priced else ", est",
            extra={
                "_fields": {
                    "provider": provider_name,
                    "model": model,
                    "cost_usd": cost_usd,
                    "priced": priced,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": duration_ms,
                    # D01.6 EXIT point — the four measured dimensions.
                    "session_key": session_key,
                    "cached_input_tokens": cached_input_tokens,
                    "prompt_hash": prompt_hash,
                    "system_prompt_chars": system_prompt_chars,
                    "ttft_ms": ttft_ms,
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
        payload: dict[str, object] = {"current_usd": summary.total_usd, "limit_usd": limit}
        # FX-10 — attach message/channel/target when a recipient is resolved, so
        # EventDeliveryBridge (event_bridge.py) can actually deliver this instead
        # of dropping it at the honest-recipient rail.
        if self._notify_channel is not None and self._notify_target is not None:
            payload["channel"] = self._notify_channel
            payload["target"] = self._notify_target
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
            self._bus.emit("budget_exceeded", {
                **payload,
                "message": f"Daily LLM budget exceeded: ${summary.total_usd:.2f} / ${limit:.2f}",
            })
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
            self._bus.emit("budget_80pct_alert", {
                **payload,
                "message": (
                    f"Daily LLM budget at {ratio * 100:.0f}%: "
                    f"${summary.total_usd:.2f} / ${limit:.2f}"
                ),
            })

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

    async def session_total(self, session_key: str, conversation_id: str) -> DailySummary:
        """Aggregate cost_records for ONE incarnation of one lane.

        Scoped to BOTH identifiers on purpose: a lane outlives its
        incarnations, so ``session_key`` alone would bill a fresh conversation
        for its predecessor's spend. ``DailySummary.date`` carries the
        ``conversation_id`` here — the shape is an aggregate over a conversation,
        not over a calendar day.
        """
        log.engine.debug(
            "[cost_tracker] session_total: entry",
            extra={"_fields": {"session_key": session_key, "conversation_id": conversation_id}},
        )
        rows = await self._db.fetch_all(
            """
            SELECT provider_name, model, cost_usd, priced
            FROM cost_records
            WHERE owner_id = ? AND session_key = ? AND conversation_id = ?
            """,
            (self._owner_id, session_key, conversation_id),
        )
        total = 0.0
        by_provider: dict[str, float] = {}
        by_model: dict[str, float] = {}
        # A NULL `priced` is a row from before migration 0101 — unknown
        # provenance, which cannot honestly be counted as priced.
        all_priced = True
        for row in rows:
            cost = float(row["cost_usd"])
            total += cost
            if row["priced"] != 1:
                all_priced = False
            by_provider[row["provider_name"]] = by_provider.get(row["provider_name"], 0.0) + cost
            by_model[row["model"]] = by_model.get(row["model"], 0.0) + cost
        log.engine.debug(
            "[cost_tracker] session_total: exit",
            extra={"_fields": {
                "session_key": session_key, "conversation_id": conversation_id,
                "total_usd": total, "call_count": len(rows),
            }},
        )
        return DailySummary(
            date=conversation_id,
            total_usd=total,
            by_provider=by_provider,
            by_model=by_model,
            call_count=len(rows),
            all_priced=all_priced,
        )

    def turn_cost_usd(self, trace_id: str) -> float:
        """Return the accumulated USD spend for ``trace_id`` this server lifetime.

        Reads the bounded in-memory running total maintained by :meth:`record` via
        the composed :class:`TurnCostLedger` (B2 split); a hot path for the
        cost-pause check (NO SQLite query). Returns ``0.0`` for an unknown/empty/
        evicted trace.
        """
        return self._turn_ledger.total(trace_id)

    async def get_turn_token_totals(self, trace_id: str) -> tuple[int, int] | None:
        """Sum input/output tokens across all cost_records for one turn.

        Owner-scoped like every other read on this repository (via
        ``_fetch_owned``). Returns ``(total_input, total_output)`` or ``None``
        if no rows exist for ``trace_id`` (never recorded, or a turn with no
        billed calls).
        """
        log.engine.debug(
            "[cost_tracker] get_turn_token_totals: entry",
            extra={"_fields": {"trace_id": trace_id}},
        )
        rows = await self._fetch_owned(
            "cost_records", where_sql="trace_id = ?", params=(trace_id,)
        )
        if not rows:
            log.engine.debug(
                "[cost_tracker] get_turn_token_totals: no records for trace",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return None
        total_input = sum(int(row["input_tokens"]) for row in rows)
        total_output = sum(int(row["output_tokens"]) for row in rows)
        log.engine.debug(
            "[cost_tracker] get_turn_token_totals: exit",
            extra={
                "_fields": {
                    "trace_id": trace_id,
                    "total_input": total_input,
                    "total_output": total_output,
                }
            },
        )
        return (total_input, total_output)

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
