"""Built-in health contributors: db, filesystem, provider."""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from stackowl.authz.bounds import DEFAULT_TURN_MAX_INPUT_TOKENS
from stackowl.config.provider import ProviderConfig
from stackowl.health.status import HealthStatus

if TYPE_CHECKING:
    from stackowl.channels.liveness import ChannelLivenessStore
    from stackowl.infra.clock import Clock
    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.memory.outcome_store import TaskOutcomeStore
    from stackowl.owls.registry import OwlRegistry

log = logging.getLogger("stackowl.health")


class GraphContributor:
    """Health contributor for the Kuzu knowledge-graph layer (DUR-5 / F069).

    With a live ``adapter`` wired (ADR-6 self-heal, Task 3), ``health_check()``
    probes it via its existing ``health()`` (shim'd from ``HealthReport`` to
    ``HealthStatus``) so the
    verdict reflects the REAL live connection — not just whether the process
    imported ``kuzu`` at assembly time. Previously this contributor only
    checked import success and would report ``ok`` even with a dead live
    connection; ``tests/memory/test_kuzu_adapter_healable.py`` guards against
    repeating that mistake.

    Without an adapter (``probe()`` / a degrade-at-boot snapshot), falls back
    to the cached ``available``/``reason`` — used by the out-of-process
    ``health`` CLI command, which must NOT open the live graph DB (the serve
    process holds it), and by ``MemoryAssembly.build``'s degrade branches where
    there is no live adapter to probe at all.

    ``contributor_name`` is ``"graph"`` and MUST match the ``healers`` dict key
    registered in ``scheduler/assembly.py`` — the health sweep looks up the
    matching ``HealableResource`` via ``dict.get(status.name)``, a plain
    exact-string match with no normalization.
    """

    def __init__(
        self,
        *,
        available: bool,
        reason: str | None = None,
        adapter: KuzuAdapter | None = None,
    ) -> None:
        self._available = available
        self._reason = reason
        self._adapter = adapter

    @classmethod
    def probe(cls) -> GraphContributor:
        """Build a contributor by probing whether the Kuzu native layer loads.

        Used by the out-of-process ``health`` CLI command, which must NOT open
        the live graph DB (the serve process holds it). Importing the ``kuzu``
        native module reproduces the exact ARM-wheel-missing failure mode that
        DUR-5 degrades on, so an import failure is reported as ``down`` without
        touching the on-disk database. No live adapter — deliberately stays on
        the cached-verdict path in ``health_check()``.
        """
        try:
            import kuzu  # noqa: F401
        except Exception as exc:  # pragma: no cover — only on a broken wheel
            return cls(available=False, reason=f"{type(exc).__name__}: {exc}")
        return cls(available=True)

    @property
    def contributor_name(self) -> str:
        return "graph"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        log.debug(
            "[health] graph_contributor: entry available=%s has_adapter=%s",
            self._available, self._adapter is not None,
        )
        if self._adapter is not None:
            # ADR-6 Task 3 — probe the LIVE adapter instead of trusting the
            # cached import-time snapshot; a successful `import kuzu` says
            # nothing about whether the live connection still works.
            report = await self._adapter.health()
            latency_ms = (time.monotonic() - t0) * 1000
            message = None if report.status == "ok" else str(report.details)
            log.debug("[health] graph_contributor: exit — live status=%s", report.status)
            return HealthStatus(
                name=self.contributor_name,
                status=report.status,
                message=message,
                latency_ms=latency_ms,
            )
        latency_ms = (time.monotonic() - t0) * 1000
        if self._available:
            return HealthStatus(
                name="graph", status="ok", message=None, latency_ms=latency_ms
            )
        return HealthStatus(
            name="graph",
            status="down",
            message=self._reason or "knowledge graph unavailable",
            latency_ms=latency_ms,
        )


class DbContributor:
    """Health contributor: SQLite database reachability."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @property
    def contributor_name(self) -> str:
        return "db"

    async def health_check(self) -> HealthStatus:
        import asyncio

        log.debug("[health] db_contributor: entry")
        t0 = time.monotonic()

        def _ping() -> None:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("SELECT 1").fetchone()
            finally:
                conn.close()

        if not self._db_path.exists():
            return HealthStatus(
                name="db",
                status="down",
                message=f"database not found: {self._db_path}",
                latency_ms=0.0,
            )
        try:
            await asyncio.to_thread(_ping)
            latency_ms = (time.monotonic() - t0) * 1000
            log.debug("[health] db_contributor: exit — ok (%.0fms)", latency_ms)
            return HealthStatus(name="db", status="ok", message=None, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning("[health] db_contributor: ping failed: %s", exc)
            return HealthStatus(name="db", status="down", message=str(exc), latency_ms=latency_ms)


# LanceDBHealthContributor stood here, shimming the adapter's HealthReport into
# the HealthStatus the aggregator expects — carefully, because a pass-through that
# upgraded a degraded report into "ok" is the exact silent-upgrade mistake flagged
# for Kuzu. Both it and the adapter went in D08.2: a health surface for a subsystem
# that no longer exists reports on nothing.

#: Above this share of a window, spend has stopped being attributable and someone
#: should know. MEASURED, not chosen: the defect that prompted the trace work sat at
#: **54.5%** (67,383 of 123,648 records) on 2026-08-29, and the repaired platform has
#: run at **0-3%** every day since 2026-08-30. Ten per cent is clear of the noise and
#: far below the failure, so it can neither cry wolf at the healthy rate nor stay
#: quiet through a return of the original defect.
_UNATTRIBUTED_DEGRADED_SHARE = 0.10

#: Below this many records the share is arithmetic, not a measurement. Two blank out
#: of three is 67% and means nothing — the same "0 exemptions over 7 browser calls"
#: shape where a ratio was computed over a denominator that could not carry it.
_UNATTRIBUTED_MIN_SAMPLE = 20

#: OWNER-SCOPED, and the tenancy tripwire is why. `cost_records` is owner-governed,
#: and this query first shipped without an `owner_id` predicate — the exact defect
#: `CLAUDE.md` already records against `usage_report.py`, caught here by
#: `tests/tenancy/test_no_owner_scope_bypass.py` before the commit. That test's own
#: message is the rule: scope new code by owner_id rather than allowlisting it, since
#: the allowlist is for pre-existing accessors. Every row is `principal-default`
#: today, so this narrows nothing now and closes the gap for when it does.
_UNATTRIBUTED_SQL = (
    "SELECT COUNT(*) AS total, "
    "SUM(CASE WHEN trace_id IS NULL OR trace_id = '' THEN 1 ELSE 0 END) AS blank "
    "FROM cost_records WHERE recorded_at >= ? AND owner_id = ?"
)


#: A round below this many input tokens is not carrying the prefix — it is a judge,
#: classifier or router call. Measured: those cluster around 200-700 tokens while a
#: prefix-carrying round is ~25,000, so the floor separates two populations rather
#: than trimming one.
_PREFIX_ROUND_FLOOR_TOKENS = 10_000

#: Below this many prefix-carrying rounds in a window, a median is arithmetic rather
#: than a measurement — the same floor, and the same reason, as
#: ``_UNATTRIBUTED_MIN_SAMPLE`` one class down.
_PREFIX_MIN_SAMPLE = 30

#: Growth in the median prefix-carrying round that counts as a regression, chosen
#: from the CONSEQUENCE rather than taste — and corrected once, which is the point.
#: It was first set to 1.25 while being justified by the measured +20-tool
#: trajectory (median 24,811 -> ~29,800). That is 1.20x, so the threshold MISSED the
#: exact case written down to motivate it; the test asserting the motivating case
#: fires is what caught it. At 1.20 the 500,000-token cap goes from ~20.1 rounds per
#: turn to ~16.8 — losing three rounds of working room is a change an operator should
#: be told about, not discover as "it stops earlier than it used to".
_PREFIX_GROWTH_ALARM = 1.20

#: OWNER-SCOPED for the same reason its sibling is: `cost_records` is owner-governed
#: and `tests/tenancy/test_no_owner_scope_bypass.py` exists because this predicate was
#: omitted once already, in `usage_report.py`.
#: THE FIRST prefix-carrying round of each trace, not every round. A later round
#: carries the prefix PLUS accumulated tool results, so a median over all rounds
#: moves when conversations merely get longer — it would measure "turns grew" and
#: report it as "the prefix grew". Measured on live data before this was corrected:
#: all-rounds gave 1.32x over 24h while the first-round measure is the one that
#: isolates the schemas + system prompt this class is named for. Naming a metric
#: after a quantity it does not measure is how a dashboard starts lying.
_PREFIX_SQL = (
    "SELECT input_tokens FROM cost_records WHERE id IN ("
    " SELECT MIN(id) FROM cost_records"
    " WHERE recorded_at >= ? AND recorded_at < ? AND owner_id = ?"
    " AND input_tokens >= ? AND trace_id != ''"
    " GROUP BY trace_id"
    ") ORDER BY id DESC LIMIT 500"
)


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    return float(ordered[mid]) if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


class PrefixGrowthContributor:
    """Health contributor: the unchanging prompt prefix is getting more expensive.

    WHY THIS EXISTS. Every provider round re-sends a prefix — 79 tool schemas
    (~19,900 tokens) plus the system prompt — and measured 2026-09-05, **384,429,704
    tokens, 64% of the primary provider's entire input bill, is prefix already sent
    earlier in the SAME turn**. Nothing measured it: before this class,
    ``grep -rn input_tokens src/stackowl/health/`` returned zero, so the platform
    could not answer its single largest cost question about itself.

    WHY GROWTH AND NOT SHARE. "64% of your tokens are re-sent prefix" would be a
    permanently-degraded alarm — CLAUDE.md shape #4, no decay — and an alarm that can
    never clear is one the operator learns to ignore. The share is a property of the
    architecture. The GROWTH is a regression, and it is the one that bites.

    WHAT GROWTH COSTS, and it is not tokens. A turn ends when its cumulative token cap
    is reached, so the prefix sets how many ROUNDS a turn gets. At the measured median
    of 24,811 tokens the 500,000-token cap binds at ~20.1 rounds — which happens to
    sit on the 20-step cap by coincidence rather than design. Twenty more tools at the
    measured ~249 tokens each moves the median to ~29,800 and the crossing to ~16.8,
    and every turn on the platform quietly gets a shorter leash. The registry only
    grows, `HARD_TOOL_COUNT_CAP` (150 against 79 registered) permits roughly double
    today's prefix, and no eviction event appears in any retained log — so nothing
    else in the tree would notice this happening.

    DEGRADED, NEVER DOWN: an expensive prefix serves turns perfectly, it just serves
    fewer rounds of them.
    """

    def __init__(
        self, db: object, *, window_hours: int = 72, owner_id: str | None = None
    ) -> None:
        """``window_hours`` is 72, not 24, and the number came from the data.

        One trace contributes ONE sample (its first prefix-carrying round), so the
        sample rate is turns-per-day, not calls-per-day. Measured on the live ledger:
        a 24-hour window yields 25 recent and 5 baseline samples against a floor of
        30 — it would have answered "not enough to judge" almost always, which is an
        honest answer and a useless contributor. At 72 hours both windows carry
        enough to judge and the medians agree with an independently derived figure
        (23,754 / 23,536 here vs 23,458 measured separately).
        """
        from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

        self._db = db
        self._window_hours = window_hours
        self._owner_id = owner_id or DEFAULT_PRINCIPAL_ID

    @property
    def contributor_name(self) -> str:
        return "prefix_growth"

    async def _window(self, start: str, end: str) -> list[int]:
        rows = await self._db.fetch_all(  # type: ignore[attr-defined]
            _PREFIX_SQL, (start, end, self._owner_id, _PREFIX_ROUND_FLOOR_TOKENS)
        )
        return [int(r.get("input_tokens") or 0) for r in (rows or [])]

    async def health_check(self) -> HealthStatus:
        # 1. ENTRY
        log.debug("[health] prefix_growth: entry")
        t0 = time.monotonic()
        now = datetime.now(UTC)
        recent_from = (now - timedelta(hours=self._window_hours)).isoformat()
        base_from = (now - timedelta(hours=self._window_hours * 2)).isoformat()
        try:
            # 2. DECISION — two windows, most recent first (the stub in tests
            #    answers in this order, and so does production).
            recent = await self._window(recent_from, now.isoformat())
            baseline = await self._window(base_from, recent_from)
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            # "I could not measure it" is not "it has regressed" — reporting the
            # first as the second is the instrument lying, which is the sibling's
            # rule and it applies identically here.
            log.warning("[health] prefix_growth: check failed: %s", exc)
            return HealthStatus(
                name="prefix_growth", status="degraded",
                message=f"could not measure prefix growth: {exc}", latency_ms=latency_ms,
            )

        latency_ms = (time.monotonic() - t0) * 1000
        if len(recent) < _PREFIX_MIN_SAMPLE or len(baseline) < _PREFIX_MIN_SAMPLE:
            # 3. STEP — say UNKNOWN rather than compute a ratio the data cannot carry.
            return HealthStatus(
                name="prefix_growth", status="ok",
                message=(
                    f"not enough prefix-carrying rounds to judge "
                    f"({len(recent)} recent, {len(baseline)} baseline; "
                    f"{_PREFIX_MIN_SAMPLE} needed)"
                ),
                latency_ms=latency_ms,
            )

        now_median = _median(recent)
        was_median = _median(baseline)
        ratio = (now_median / was_median) if was_median else 1.0
        cap = DEFAULT_TURN_MAX_INPUT_TOKENS
        rounds_now = cap / now_median if now_median else 0
        rounds_was = cap / was_median if was_median else 0

        if ratio < _PREFIX_GROWTH_ALARM:
            return HealthStatus(
                name="prefix_growth", status="ok",
                message=(
                    f"median prefix-carrying round {now_median:,.0f} tok "
                    f"(~{rounds_now:.1f} rounds per turn)"
                ),
                latency_ms=latency_ms,
            )

        # 4. EXIT — INFO, because production runs at INFO and this line is the
        #    evidence for "why do my turns stop earlier than they used to".
        log.info(
            "[health] prefix_growth: the re-sent prompt prefix has grown — every "
            "turn now gets fewer rounds before its token cap",
            extra={"_fields": {
                "median_now": now_median, "median_baseline": was_median,
                "growth": round(ratio, 3),
                "rounds_now": round(rounds_now, 1), "rounds_before": round(rounds_was, 1),
            }},
        )
        return HealthStatus(
            name="prefix_growth", status="degraded",
            message=(
                f"prefix grew {was_median:,.0f} -> {now_median:,.0f} tok per round "
                f"({ratio:.2f}x): a turn now gets ~{rounds_now:.0f} rounds instead of "
                f"~{rounds_was:.0f} before its token cap"
            ),
            latency_ms=latency_ms,
        )


class UnattributedSpendContributor:
    """Health contributor: model spend that belongs to no trace.

    WHY THIS EXISTS. `_bind_job_trace` fixed a real defect — on 2026-08-29, 54.5% of
    recorded LLM calls carried a blank `trace_id`, so a fifth of all spend was
    attributable to nothing. Three tests pin that fix. **They pin the CODE**: that a
    scheduled job binds a lane. Nothing watched the EFFECT, so a new background
    caller reaching a provider outside any `TraceContext` would reappear exactly as
    the first one did — silently, and visible only to whoever thought to look. The
    original was found because a human happened to query for it.

    This repo's standing question is "if this degrades silently, what notices?", and
    for its own observability the measured answer was: nothing.

    WHY A HEALTH CONTRIBUTOR rather than a job. The health sweep already runs every
    five minutes, already aggregates subsystem status and already dedupes its alerts;
    the platform's rule is to extend the loop that exists rather than add a second.

    DEGRADED, NEVER DOWN. Unattributable spend is a bookkeeping failure, not an
    outage — the platform serves turns perfectly while it is true. Reporting "down"
    for something that does not stop the platform is how an operator learns to ignore
    the health surface, a lesson already recorded here at the cost of 25 pages in one
    day for something that was never down.
    """

    def __init__(
        self, db: object, *, window_hours: int = 24, owner_id: str | None = None
    ) -> None:
        from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

        self._db = db
        self._window_hours = window_hours
        self._owner_id = owner_id or DEFAULT_PRINCIPAL_ID

    @property
    def contributor_name(self) -> str:
        return "unattributed_spend"

    async def health_check(self) -> HealthStatus:
        # 1. ENTRY
        log.debug("[health] unattributed_spend: entry")
        t0 = time.monotonic()
        since = (
            datetime.now(UTC) - timedelta(hours=self._window_hours)
        ).isoformat()
        try:
            rows = await self._db.fetch_all(  # type: ignore[attr-defined]
                _UNATTRIBUTED_SQL, (since, self._owner_id)
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            # NEVER a regression verdict on an instrument failure. "I could not
            # measure it" and "it has regressed" are different claims, and reporting
            # the first as the second is the instrument lying.
            log.warning("[health] unattributed_spend: check failed: %s", exc)
            return HealthStatus(
                name="unattributed_spend", status="degraded",
                message=f"could not measure attribution: {exc}", latency_ms=latency_ms,
            )

        row = (rows or [{}])[0]
        total = int(row.get("total") or 0)
        blank = int(row.get("blank") or 0)
        latency_ms = (time.monotonic() - t0) * 1000

        # 2. DECISION — a denominator that cannot carry a rate
        if total < _UNATTRIBUTED_MIN_SAMPLE:
            log.debug("[health] unattributed_spend: exit — too few records (%d)", total)
            return HealthStatus(
                name="unattributed_spend", status="ok",
                message=f"too few records to judge ({total} in {self._window_hours}h)",
                latency_ms=latency_ms,
            )

        share = blank / total
        # 3. STEP — the OK case carries its denominator, or it is worthless
        if share >= _UNATTRIBUTED_DEGRADED_SHARE:
            detail = (
                f"{blank} of {total} model calls in the last {self._window_hours}h "
                f"({share:.0%}) carry no trace_id — their spend is attributable to "
                "nothing. A caller is reaching a provider outside any TraceContext."
            )
            log.warning("[health] unattributed_spend: %s", detail)
            return HealthStatus(
                name="unattributed_spend", status="degraded",
                message=detail, latency_ms=latency_ms,
            )

        # 4. EXIT
        log.info(
            "[health] unattributed_spend: exit — ok",
            extra={"_fields": {"total": total, "blank": blank, "share": round(share, 4)}},
        )
        return HealthStatus(
            name="unattributed_spend", status="ok",
            message=f"{blank} of {total} unattributed ({share:.0%}) in {self._window_hours}h",
            latency_ms=latency_ms,
        )


class StoreCadenceContributor:
    """Health contributor: a store that has gone quiet past its own declaration.

    WHY IT IS A HEALTH CONTRIBUTOR rather than a job of its own. The health sweep
    already runs every five minutes, already aggregates subsystem status, and
    already dedupes its alerts through ``_alert_state`` — the platform's rule is
    to extend the loop that exists rather than add a second. A stopped writer IS
    a subsystem being down; it just had no voice here until now.

    DEGRADED, NOT DOWN. A store past its cadence means a writer has probably
    stopped, which is serious — but the platform is demonstrably still serving
    turns while it is true, and reporting "down" for something that does not stop
    the platform is how an operator learns to ignore the health surface. That
    lesson is already recorded here: 25 pages in one day, and it was never down.
    """

    def __init__(self, db: object) -> None:
        self._db = db

    @property
    def contributor_name(self) -> str:
        return "store_cadence"

    async def health_check(self) -> HealthStatus:
        from stackowl.health.store_cadence import cadence_report

        log.debug("[health] store_cadence: entry")
        t0 = time.monotonic()
        try:
            report = await cadence_report(self._db)
            silent = list(report.silent)
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            # NEVER "down" ON AN INSTRUMENT FAILURE. "I could not measure it" and
            # "it has stopped" are different claims; reporting the first as the
            # second is the instrument lying, which this repo has paid for.
            log.warning("[health] store_cadence: check failed: %s", exc)
            return HealthStatus(
                name="store_cadence", status="degraded",
                message=f"cadence check failed: {exc}", latency_ms=latency_ms,
            )
        latency_ms = (time.monotonic() - t0) * 1000
        if not silent:
            # THE OK CASE CARRIES ITS DENOMINATOR. "No store is silent" is
            # worthless without "out of how many": this check's own first live
            # run returned a clean zero while a date-format bug had skipped
            # almost every store it claimed to cover. A healthy report that
            # cannot say what it looked at is the same trap with a tick next to
            # it.
            # INFO, for the same reason the SILENT branch below is INFO:
            # production runs at INFO. This was DEBUG, so a check running every
            # five minutes said nothing whenever all was well — and a clean run
            # then looks exactly like a check that never ran. That is not a
            # theoretical hazard here: the closing query for the 2026-09-03
            # cadence re-declaration returned ZERO lines against a sweep that had
            # just executed, which is how this was found. The message already
            # carries the denominator its own comment argues for; it just had to
            # be somewhere readable.
            log.info(
                "[health] store_cadence: ok — no store past its declared cadence",
                extra={"_fields": {
                    "measured": report.measured,
                    "empty": report.empty,
                    "unreadable": report.unreadable,
                    "latency_ms": round(latency_ms, 1),
                }},
            )
            return HealthStatus(
                name="store_cadence", status="ok",
                message=(
                    f"{report.measured} stores measured, none past their "
                    f"declared cadence ({report.empty} empty, "
                    f"{report.unreadable} unreadable)"
                ),
                latency_ms=latency_ms,
            )
        detail = "; ".join(
            f"{s.table} silent {s.idle_days:.1f}d (declared max {s.allowed_days:.0f}d — "
            f"{s.why})"
            for s in sorted(silent, key=lambda x: -x.idle_days)
        )
        # INFO, and named per store: production runs at INFO, and "3 stores are
        # quiet" without naming them cannot be acted on at 2am.
        log.info(
            "[health] store_cadence: a store is past its declared cadence",
            extra={"_fields": {
                "n_silent": len(silent),
                "tables": [s.table for s in silent],
            }},
        )
        return HealthStatus(
            name="store_cadence", status="degraded", message=detail,
            latency_ms=latency_ms,
        )


class FilesystemContributor:
    """Health contributor: data and log directory writability."""

    def __init__(self, data_dir: Path, log_dir: Path) -> None:
        self._data_dir = data_dir
        self._log_dir = log_dir

    @property
    def contributor_name(self) -> str:
        return "filesystem"

    async def health_check(self) -> HealthStatus:
        log.debug("[health] fs_contributor: entry")
        t0 = time.monotonic()
        for label, path in [("data_dir", self._data_dir), ("log_dir", self._log_dir)]:
            if not path.exists():
                return HealthStatus(
                    name="filesystem",
                    status="down",
                    message=f"{label} missing: {path}",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
        latency_ms = (time.monotonic() - t0) * 1000
        log.debug("[health] fs_contributor: exit — ok (%.0fms)", latency_ms)
        return HealthStatus(name="filesystem", status="ok", message=None, latency_ms=latency_ms)


class BrowserContributor:
    """Health contributor: Camoufox runtime status + RSS.

    Reports cold-start time and active-session counts. Does not perform a
    navigation — that would be too expensive for a health probe. Use the
    /browser sessions / settings commands for live drill-down.
    """

    def __init__(self, runtime: object | None, sessions: object | None) -> None:
        self._runtime = runtime
        self._sessions = sessions

    @property
    def contributor_name(self) -> str:
        return "browser"

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        runtime = self._runtime
        if runtime is None:
            return HealthStatus(
                name="browser", status="degraded",
                message="runtime not constructed",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        if not getattr(runtime, "available", False):
            reason = getattr(runtime, "unavailable_reason", None) or "unknown"
            return HealthStatus(
                name="browser", status="down",
                message=f"unavailable: {reason}",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        cold = getattr(runtime, "cold_start_ms", None)
        # Best-effort session count (no async call needed — read internal dict).
        session_count = 0
        if self._sessions is not None:
            sessions_dict = getattr(self._sessions, "_sessions", {})
            try:
                session_count = len(sessions_dict)
            except Exception:
                session_count = 0
        rss_mb = _process_rss_mb()
        msg = f"cold_start_ms={int(cold) if cold else '?'} sessions={session_count} rss_mb={rss_mb}"
        return HealthStatus(
            name="browser", status="ok", message=msg,
            latency_ms=(time.monotonic() - t0) * 1000,
        )


def _process_rss_mb() -> int:
    """Best-effort RSS in MB. Returns 0 on platforms without /proc."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1]) // 1024
    except OSError:
        pass
    return 0


_STALE_AFTER_S = 120.0  # 4 missed 30s heartbeats — matches adapter degrade intent


class ChannelLivenessContributor:
    """Health contributor: is a channel's receive/send path actually alive (RC0)?

    Reads a cross-process ``channel_liveness`` row and turns its AGE into honest
    health. This is the signal that would have caught the 30-hour outage where
    the sweep saw "registered" (in-proc) and reported ok while the real long-poll
    loop was dead in another process.

    Constructed with a ``ChannelLivenessStore`` (any DbPool + Clock) plus the
    channel name to watch. Kept as a SEPARATE contributor rather than folded into
    ``ChannelRegistry.health_check`` on purpose: the registry is a channel-agnostic
    classmethod singleton with no DbPool, and making it telegram-aware + DB-coupled
    would break its single responsibility. Same end result, cleaner seam.

    ``kind`` distinguishes two complementary signals sharing the same
    channel-agnostic table: ``"receive"`` (PB0b — is the inbound poll/long-poll
    loop alive?) and ``"send"`` (PB-CANARY — did a real outbound send recently get
    confirmed delivered?). Defaults to ``"receive"`` and ``stale_after_s`` defaults
    to the original module constant, so PB0b's existing registration call (which
    passes neither) is BYTE-IDENTICAL to before this generalization.
    """

    def __init__(
        self,
        store: ChannelLivenessStore,
        channel: str,
        clock: Clock,
        *,
        kind: Literal["receive", "send"] = "receive",
        stale_after_s: float = _STALE_AFTER_S,
    ) -> None:
        self._store = store
        self._channel = channel
        self._clock = clock
        self._kind = kind
        self._stale_after_s = stale_after_s

    @property
    def contributor_name(self) -> str:
        return f"{self._channel}_{self._kind}"

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        log.debug(
            "[health] channel_liveness_contributor: entry channel=%s kind=%s",
            self._channel, self._kind,
        )
        last = await self._store.read_last_receive_at(self._channel)
        latency_ms = (time.monotonic() - t0) * 1000
        name = f"{self._channel}_{self._kind}"
        if last is None:
            never_msg = (
                f"{self._channel} receive loop never reported alive"
                if self._kind == "receive"
                else f"{self._channel} — no successful send ever confirmed"
            )
            return HealthStatus(
                name=name,
                status="down",
                message=never_msg,
                latency_ms=latency_ms,
            )
        age = (self._clock.now() - last).total_seconds()
        if age > self._stale_after_s:
            stale_msg = (
                f"{self._channel} receive loop stale — last update {int(age)}s ago"
                if self._kind == "receive"
                else f"{self._channel} — no successful send confirmed in the last "
                f"{int(self._stale_after_s)}s (last confirmed {int(age)}s ago)"
            )
            return HealthStatus(
                name=name,
                status="degraded",
                message=stale_msg,
                latency_ms=latency_ms,
            )
        ok_msg = (
            f"last update {int(age)}s ago"
            if self._kind == "receive"
            else f"last send confirmed {int(age)}s ago"
        )
        return HealthStatus(
            name=name,
            status="ok",
            message=ok_msg,
            latency_ms=latency_ms,
        )


class ProviderContributor:
    """Health contributor: provider HTTP connectivity."""

    def __init__(self, provider: ProviderConfig) -> None:
        self._provider = provider

    @property
    def contributor_name(self) -> str:
        return f"provider:{self._provider.name}"

    async def health_check(self) -> HealthStatus:
        from stackowl.startup.provider_probe import probe_provider

        log.debug("[health] provider_contributor: entry name=%s", self._provider.name)
        result = await probe_provider(self._provider)
        # PASS THE VERDICT THROUGH. This collapsed every non-ok result to
        # "degraded", so even once the probe could say "down" the distinction
        # would have been swallowed one layer up. The sibling MCP contributor in
        # this file already distinguishes the two.
        status = result.status
        return HealthStatus(
            name=f"provider:{result.name}",
            # The ignore that stood here is GONE, not silenced: it existed
            # because the collapse produced a bare str. Passing the probe's own
            # verdict through makes the types line up, and mypy said so.
            status=status,
            message=result.reason,
            latency_ms=result.latency_ms,
        )


class McpHealthContributor:
    """Health contributor: MCP server liveness via parallel probes (ADR-6, Task 8).

    MCP had ZERO aggregator presence before this contributor — an outage was
    undetectable. McpClient itself is fully stateless per-call (fresh connection
    every discover_tools/call_tool with bounded retry-once), so its HealableResource
    implementation is a pure no-op. The real gap closed here is this contributor:
    it wraps McpLivenessProbe.probe_all() and maps down/degraded servers into
    a HealthStatus so the health sweep can alert + log on MCP failures.

    ``contributor_name`` is ``"mcp"`` and MUST match the ``healers`` dict key
    registered in ``scheduler/assembly.py`` — the health sweep looks up the
    matching ``HealableResource`` via ``dict.get(status.name)``, a plain
    exact-string match with no normalization.
    """

    def __init__(
        self,
        probe: object,  # McpLivenessProbe — TYPE_CHECKING import to avoid circular dep
        configs: list[object],  # list[McpServerConfig]
    ) -> None:
        self._probe = probe
        self._configs = configs

    @property
    def contributor_name(self) -> str:
        return "mcp"

    async def health_check(self) -> HealthStatus:
        log.debug("[health] mcp_contributor: entry")
        t0 = time.monotonic()

        # Empty config = no MCP servers configured. Report ok early.
        if not self._configs:
            latency_ms = (time.monotonic() - t0) * 1000
            log.debug("[health] mcp_contributor: exit — no servers configured")
            return HealthStatus(
                name="mcp",
                status="ok",
                message="no MCP servers configured",
                latency_ms=latency_ms,
            )

        # Probe all servers in parallel.
        results: dict[str, bool] = await self._probe.probe_all(self._configs)  # type: ignore[attr-defined]

        # Aggregate results: down if any server is dead, degraded if all alive but we saw failures, ok otherwise.
        down_servers = [name for name, is_alive in results.items() if not is_alive]
        latency_ms = (time.monotonic() - t0) * 1000

        if len(down_servers) == len(results):
            # All servers down
            log.debug("[health] mcp_contributor: exit — all servers down")
            return HealthStatus(
                name="mcp",
                status="down",
                message=f"all {len(results)} MCP server(s) down: {', '.join(down_servers)}",
                latency_ms=latency_ms,
            )
        elif down_servers:
            # Some servers down (but not all)
            log.debug("[health] mcp_contributor: exit — degraded (%d down)", len(down_servers))
            return HealthStatus(
                name="mcp",
                status="degraded",
                message=f"{len(down_servers)} of {len(results)} MCP server(s) down: {', '.join(down_servers)}",
                latency_ms=latency_ms,
            )
        else:
            # All servers alive
            log.debug("[health] mcp_contributor: exit — ok")
            return HealthStatus(
                name="mcp",
                status="ok",
                message=f"all {len(results)} MCP server(s) alive",
                latency_ms=latency_ms,
            )


class ResilienceContributor:
    """Health contributor: per-subsystem recycle counts for HealableResources.

    Reports availability and recycle metadata across all registered resources
    (browser runtime, db pool, providers, memory adapters, etc.) so operators
    can spot flapping subsystems in one place.
    """

    #: Recycles within ONE sweep interval that mean "flapping" rather than "healed".
    #: One recycle is the self-heal working as designed. Measured baseline: recycle
    #: incidents run ~9.5/day — about 0.03 per 5-minute sweep — so two in a single
    #: interval is genuinely abnormal.
    FLAP_THRESHOLD = 2

    def __init__(self, resources: dict[str, object]) -> None:
        """``resources`` maps a short label ('browser', 'db_pool') to the resource instance."""
        self._resources = resources
        #: Last observed cumulative recycle count per label. The status is driven by
        #: the DELTA between sweeps, never the total: a monotonic counter on a
        #: long-lived process crosses any fixed threshold eventually and then latches
        #: degraded forever, which is CLAUDE.md defect shape #4 (no decay) and an
        #: alarm the operator learns to ignore. `None` until the first observation,
        #: so a process that has been up for a week does not alarm about history.
        self._seen: dict[str, int] = {}

    @property
    def contributor_name(self) -> str:
        return "resilience"

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        log.debug("[health] resilience_contributor: entry")
        parts: list[str] = []
        any_unavailable = False
        flapping: list[str] = []
        for label, res in self._resources.items():
            available = bool(getattr(res, "available", True))
            recycle_count = int(getattr(res, "recycle_count", 0))
            reason = getattr(res, "unavailable_reason", None)

            # DELTA, not total — see `_seen`. A first sighting establishes the
            # baseline and cannot alarm.
            previous = self._seen.get(label)
            self._seen[label] = recycle_count
            recent = 0 if previous is None else max(recycle_count - previous, 0)
            if recent >= self.FLAP_THRESHOLD:
                flapping.append(f"{label}+{recent}")

            if not available:
                any_unavailable = True
                parts.append(f"{label}:DOWN({reason or 'unknown'})")
            elif recent:
                parts.append(f"{label}:ok(recycles={recycle_count},+{recent} since last sweep)")
            elif recycle_count > 0:
                parts.append(f"{label}:ok(recycles={recycle_count})")
            else:
                parts.append(f"{label}:ok")

        if flapping:
            # INFO, not DEBUG: production runs at INFO, and this line is the
            # evidence for the degraded verdict above it.
            log.info(
                "[health] resilience_contributor: a resource is flapping — "
                "recycled repeatedly between sweeps",
                extra={"_fields": {"flapping": flapping, "threshold": self.FLAP_THRESHOLD}},
            )

        latency_ms = (time.monotonic() - t0) * 1000
        return HealthStatus(
            name="resilience",
            status="degraded" if (any_unavailable or flapping) else "ok",
            message=" ".join(parts) if parts else "no healable resources registered",
            latency_ms=latency_ms,
        )


# Minimum rated turns before a dislike rate is trusted — a single early
# dislike out of 1-2 votes must not flag an owl as degraded.
_OWL_RATING_MIN_SAMPLES = 10
# Dislike-rate threshold for "degraded". Chosen conservative (well above normal
# noise) since this feeds the same incident-escalation pipeline as provider
# outages — a false "degraded" here would train the operator to ignore it.
_OWL_RATING_DEGRADED_THRESHOLD = 0.4
_OWL_RATING_WINDOW_SECONDS = 7 * 24 * 3600.0


class OwlRatingHealthContributor:
    """Health contributor: per-owl Like/Dislike vote signal (approach_rating).

    Before this, a dislike vote only suppressed DNA-attribution reinforcement
    for the trait band that produced it (dna_attribution.py) — it never
    aggregated into any per-owl health/trust signal, and owl health was
    completely invisible to the health-aggregator/incident-escalation
    pipeline that already exists for providers, tools, and channels. This
    closes that gap: an owl whose recent dislike rate crosses the threshold
    (with enough votes to be meaningful, not just one early dislike) reports
    degraded, same shape as every other contributor here.
    """

    def __init__(
        self,
        outcome_store: TaskOutcomeStore,
        owl_registry: OwlRegistry,
        *,
        min_samples: int = _OWL_RATING_MIN_SAMPLES,
        degraded_threshold: float = _OWL_RATING_DEGRADED_THRESHOLD,
        window_seconds: float = _OWL_RATING_WINDOW_SECONDS,
    ) -> None:
        self._store = outcome_store
        self._registry = owl_registry
        self._min_samples = min_samples
        self._degraded_threshold = degraded_threshold
        self._window_seconds = window_seconds

    @property
    def contributor_name(self) -> str:
        return "owl_ratings"

    async def health_check(self) -> HealthStatus:
        log.debug("[health] owl_rating_contributor: entry")
        t0 = time.monotonic()
        since_epoch = time.time() - self._window_seconds
        degraded: list[str] = []
        n_checked = 0
        for manifest in self._registry.list():
            try:
                positive, negative = await self._store.count_approach_ratings_for_owl(
                    manifest.name, since_epoch=since_epoch,
                )
            except Exception as exc:  # B5 — one owl's query failure never sinks the check
                log.warning(
                    "[health] owl_rating_contributor: query failed for owl=%s",
                    manifest.name, exc_info=exc,
                )
                continue
            total = positive + negative
            if total < self._min_samples:
                continue
            n_checked += 1
            rate = negative / total
            if rate >= self._degraded_threshold:
                degraded.append(f"{manifest.name} ({negative}/{total} disliked)")
        latency_ms = (time.monotonic() - t0) * 1000
        log.debug(
            "[health] owl_rating_contributor: exit — degraded=%d checked=%d",
            len(degraded), n_checked,
        )
        return HealthStatus(
            name=self.contributor_name,
            status="degraded" if degraded else "ok",
            message=(
                ", ".join(degraded) if degraded
                else f"no owl over dislike threshold ({n_checked} owl(s) with enough votes)"
            ),
            latency_ms=latency_ms,
        )
