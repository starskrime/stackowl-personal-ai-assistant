"""CostCommand — /cost slash command for spending visibility (FR196).

Subcommands:
  /cost            → today's spend (USD) by provider and model.
  /cost privacy    → wipe cost_records after explicit YES confirmation.

The command opens a short-lived :class:`DbPool` so it can run from the
slash-command pipeline without depending on the server's running event loop
context.  Imports of CostTracker/DbPool happen inside :meth:`handle` to avoid
import cycles with the providers subsystem.
"""

from __future__ import annotations

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import Arg, CommandMeta, Example, SubCommand, render_usage
from stackowl.commands.registry import register_command
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

_PRIVACY_CONFIRMATION = "YES"

_COST_META = CommandMeta(
    grammar="verb",
    group="Cost & Usage",
    subcommands=(
        SubCommand(
            name="turns",
            summary="Per-conversation cost, cache hit rate, first-token time and prompt stability",
            description=(
                "You see the four D01.6 measures per conversation instead of one "
                "daily total: what a whole conversation cost, how much of its input "
                "was served from the provider's prefix cache, how long until text "
                "started appearing, and whether the system prompt stayed stable "
                "across the conversation. The cache line reports how many turns the "
                "provider gave statistics for, because a 0% rate with nothing "
                "reporting is a silent backend, not a cold cache."
            ),
            args=(),
            examples=(
                Example(
                    invocation="/cost turns",
                    note="Top 10 conversations by turn count",
                ),
            ),
        ),
        SubCommand(
            name="privacy",
            summary="Wipe all cost history after a YES confirmation",
            description=(
                "You permanently delete every cost record. The wipe is "
                "irreversible, so it runs only after you confirm with YES."
            ),
            args=(
                Arg(
                    name="confirmation",
                    required=False,
                    summary="literal YES to confirm the wipe",
                    choices=("YES",),
                ),
            ),
            examples=(
                Example(
                    invocation="/cost privacy YES",
                    note="Irreversibly delete all cost records",
                ),
            ),
        ),
    ),
)


class CostCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "cost"

    @property
    def description(self) -> str:
        return "Show today's spending or wipe cost history (/cost privacy)."

    @property
    def meta(self) -> CommandMeta:
        return _COST_META

    async def handle(self, args: str, state: PipelineState) -> str:
        log.engine.debug(
            "[commands] cost.handle: entry",
            extra={"_fields": {"args_len": len(args), "session": state.session_key}},
        )
        raw = args.strip()
        if not raw:
            log.engine.debug("[commands] cost.handle: decision — show summary")
            return await self._summary()
        parts = raw.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if sub == "turns":
            log.engine.debug("[commands] cost.handle: decision — turns (D01.6 metrics)")
            return await self._turns()
        if sub == "privacy":
            log.engine.debug(
                "[commands] cost.handle: decision — privacy",
                extra={"_fields": {"confirmation_present": bool(rest)}},
            )
            return await self._privacy(rest)
        log.engine.debug(
            "[commands] cost.handle: decision — unknown subcommand",
            extra={"_fields": {"sub": sub[:40]}},
        )
        return render_usage("cost", _COST_META)

    async def _turns(self) -> str:
        """D01.6 — the four measures, per conversation.

        Answers the questions the flat daily total cannot: is the prefix cache
        working, is the prompt stable within a conversation, what does a whole
        conversation cost, and how long until text starts appearing.

        Reports ``turns_reporting_cache`` alongside the hit rate on purpose.
        ``cached_input_tokens = 0`` is ambiguous by construction (D01.6 I4) — it
        means "no cache hit" OR "this backend does not report cache statistics".
        A 0% rate with 0 reporting turns is a silent provider; a 0% rate with
        every turn reporting is a genuinely cold cache. Collapsing those two into
        one number would let a working fix look like a failed one.
        """
        log.engine.debug("[commands] cost.turns: entry")
        from stackowl.db.pool import DbPool

        db = DbPool()
        try:
            await db.open()
        except Exception as exc:
            log.engine.error("[commands] cost.turns: db open failed", exc_info=exc)
            return "No cost data yet"
        try:
            rows = await db.fetch_all(
                """
                SELECT session_key,
                       COUNT(*)                      AS turns,
                       COUNT(DISTINCT prompt_hash)   AS distinct_prompts,
                       SUM(input_tokens)             AS input_tokens,
                       SUM(cached_input_tokens)      AS cached_tokens,
                       SUM(CASE WHEN cached_input_tokens > 0 THEN 1 ELSE 0 END)
                                                     AS turns_reporting_cache,
                       AVG(ttft_ms)                  AS avg_ttft_ms,
                       SUM(cost_usd)                 AS cost_usd
                FROM cost_records
                WHERE session_key != ''
                GROUP BY session_key
                ORDER BY turns DESC
                LIMIT 10
                """
            )
        except Exception as exc:
            log.engine.warning(
                "[commands] cost.turns: query failed",
                extra={"_fields": {"error": str(exc)}},
            )
            return "No per-conversation metrics yet — run `stackowl db migrate` (needs 0091)."
        finally:
            await db.close()

        if not rows:
            return (
                "No per-conversation metrics recorded yet.\n"
                "These are stamped from the next turn onward; send a message and re-run."
            )

        lines = ["**Per-conversation metrics** (D01.6 — top 10 by turns)", ""]
        for r in rows:
            turns = int(r["turns"] or 0)
            distinct = int(r["distinct_prompts"] or 0)
            inp = int(r["input_tokens"] or 0)
            cached = int(r["cached_tokens"] or 0)
            reporting = int(r["turns_reporting_cache"] or 0)
            ttft = r["avg_ttft_ms"]
            cost = float(r["cost_usd"] or 0.0)

            if reporting == 0:
                cache_note = "n/a (provider reports no cache stats)"
            else:
                pct = (cached * 100.0 / inp) if inp else 0.0
                cache_note = f"{pct:.1f}% cached ({reporting}/{turns} turns reporting)"

            # The D01.1 invariant, stated inline so the number means something.
            if distinct <= 1:
                stability = "stable (1 prompt)"
            else:
                stability = f"CHURNING — {distinct} distinct prompts across {turns} turns"

            ttft_note = f"{int(ttft)}ms" if ttft is not None else "not measured"
            lines += [
                f"`{r['session_key']}`",
                f"  turns {turns} · ${cost:.4f} · first token {ttft_note}",
                f"  cache: {cache_note}",
                f"  prompt: {stability}",
                "",
            ]
        lines.append(
            "_Prompt stability is the D01.1 target: one distinct prompt per conversation._"
        )
        log.engine.debug(
            "[commands] cost.turns: exit", extra={"_fields": {"sessions": len(rows)}}
        )
        return "\n".join(lines)

    async def _summary(self) -> str:
        log.engine.debug("[commands] cost.summary: entry")
        from stackowl.db.pool import DbPool
        from stackowl.events.bus import EventBus
        from stackowl.providers.cost_tracker import CostTracker

        db = DbPool()
        try:
            await db.open()
        except Exception as exc:
            log.engine.error("[commands] cost.summary: db open failed", exc_info=exc)
            return "No cost data yet"
        try:
            tracker = CostTracker(db=db, event_bus=EventBus(), daily_limit_usd=None)
            try:
                summary = await tracker.daily_total()
            except Exception as exc:
                log.engine.warning(
                    "[commands] cost.summary: daily_total failed (no table?)",
                    extra={"_fields": {"error": str(exc)}},
                )
                return "No cost data yet"
        finally:
            await db.close()

        if summary.call_count == 0:
            log.engine.debug("[commands] cost.summary: exit — no calls today")
            return f"No spend recorded for {summary.date}"

        lines = [
            f"Spend for {summary.date}: ${summary.total_usd:.4f} ({summary.call_count} calls)",
            "",
            "By provider:",
        ]
        for prov, cost in sorted(summary.by_provider.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {prov:<20} ${cost:.4f}")
        lines.append("")
        lines.append("By model:")
        for model, cost in sorted(summary.by_model.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {model:<30} ${cost:.4f}")
        log.engine.debug(
            "[commands] cost.summary: exit",
            extra={"_fields": {"total_usd": summary.total_usd, "calls": summary.call_count}},
        )
        return "\n".join(lines)

    async def _privacy(self, confirmation: str) -> str:
        log.engine.debug(
            "[commands] cost.privacy: entry",
            extra={"_fields": {"confirmation_len": len(confirmation)}},
        )
        if confirmation != _PRIVACY_CONFIRMATION:
            log.engine.debug("[commands] cost.privacy: decision — missing YES")
            return "This will permanently delete all cost records.\nType '/cost privacy YES' to confirm."
        from stackowl.db.pool import DbPool

        db = DbPool()
        try:
            await db.open()
        except Exception as exc:
            log.engine.error("[commands] cost.privacy: db open failed", exc_info=exc)
            return "✗ Could not open database"
        try:
            try:
                await db.execute("DELETE FROM cost_records")
            except Exception as exc:
                log.engine.warning(
                    "[commands] cost.privacy: delete failed (table missing?)",
                    extra={"_fields": {"error": str(exc)}},
                )
                return "No cost data yet"
        finally:
            await db.close()
        log.engine.info("[commands] cost.privacy: exit — cost_records wiped")
        return "✓ Cost history cleared"


_CMD = register_command(CostCommand())
