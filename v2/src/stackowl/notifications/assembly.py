"""NotificationAssembly — factory that wires the notifications subsystem.

Mirrors :class:`stackowl.memory.assembly.MemoryAssembly`: the notifications
package owns its own assembly contract, and the startup orchestrator just
calls :meth:`NotificationAssembly.build` and unpacks the router into
:class:`StepServices`.

Per the BMad v2 wiring audit (plan: gleaming-finding-puppy.md, Commit C):

* The router is for **proactive / scheduled notifications** (heartbeat,
  scheduled jobs, /urgent broadcasts) — NOT for direct user-reply delivery,
  which already routes through ``StreamRegistry`` in ``deliver.py``.
* Focus mode persists across restarts via :class:`PreferenceStore` (operator
  vote — preserves agent state). Hydration happens here at build time.
* :class:`NotificationDigestJob` is scheduled every 5 minutes (operator vote)
  via the existing scheduler primitives; respects the B9 boundary by
  registering through :class:`HandlerRegistry`.
* All four router-dependent slash commands (`/focus`, `/urgent`, `/quiet`,
  `/notifications-missed`) self-register via their ``create_and_register``
  factories — previously orphaned because ``load_builtin_commands()`` does
  not instantiate them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.commands.focus_command import FocusCommand
    from stackowl.commands.notifications_command import NotificationsMissedCommand
    from stackowl.commands.quiet_command import QuietHoursCommand
    from stackowl.commands.urgent_command import UrgentCommand
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.events.bus import EventBus
    from stackowl.memory.preferences import PreferenceStore
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.notifications.digest_job import NotificationDigestJob
    from stackowl.notifications.router import NotificationRouter


_DIGEST_HANDLER_NAME = "notification_digest"
_DIGEST_SCHEDULE = "every 5m"
_DIGEST_IDEMPOTENCY_KEY = "notification_digest:flush"
_SELECT_DIGEST_JOB_SQL = "SELECT job_id FROM jobs WHERE handler_name = ?"
_INSERT_JOB_SQL = """
INSERT INTO jobs
    (job_id, handler_name, schedule, idempotency_key, last_run_at,
     next_run_at, status, retry_count, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_FOCUS_PREF_KEY = "focus_mode"
_FOCUS_PREF_OWNER = "global"  # focus mode is process-wide, not per-session


@dataclass(frozen=True)
class NotificationComponents:
    """Frozen container of the wired notifications subsystem."""

    router: NotificationRouter
    proactive_deliverer: ProactiveDeliverer
    digest_handler: NotificationDigestJob
    focus_command: FocusCommand
    urgent_command: UrgentCommand
    quiet_command: QuietHoursCommand
    notifications_missed_command: NotificationsMissedCommand


class NotificationAssembly:
    """Factory that constructs and wires the complete notifications subsystem."""

    @staticmethod
    async def build(
        db: DbPool,
        settings: Settings,
        event_bus: EventBus,
        preference_store: PreferenceStore,
    ) -> NotificationComponents:
        """Construct the router, digest job, and router-dependent commands.

        Hydrates focus_mode from PreferenceStore at startup so /focus survives
        restarts. Seeds the 5-minute digest schedule (idempotent).
        """
        log.notifications.info("[notifications] assembly.build: entry")

        # Deferred imports keep this module cheap to import in tests.
        from stackowl.channels.registry import ChannelRegistry
        from stackowl.commands.focus_command import FocusCommand
        from stackowl.commands.notifications_command import NotificationsMissedCommand
        from stackowl.commands.quiet_command import QuietHoursCommand
        from stackowl.commands.urgent_command import UrgentCommand
        from stackowl.notifications.deliverer import ProactiveDeliverer
        from stackowl.notifications.digest_job import NotificationDigestJob
        from stackowl.notifications.router import NotificationRouter
        from stackowl.scheduler.base import HandlerRegistry

        # 1) Router — single process-wide instance.
        router = NotificationRouter(db=db, settings=settings)

        # 1b) Hydrate focus_mode from preferences so /focus survives restart.
        persisted_focus = await preference_store.get(_FOCUS_PREF_OWNER, _FOCUS_PREF_KEY)
        if persisted_focus in ("off", "soft", "hard"):
            router.set_focus_mode(persisted_focus)  # type: ignore[arg-type]
            log.notifications.info(
                "[notifications] assembly: focus_mode hydrated",
                extra={"_fields": {"mode": persisted_focus}},
            )

        # 1c) Patch set_focus_mode to also persist — done via closure so we
        # don't have to subclass NotificationRouter just for this.
        original_set_focus = router.set_focus_mode

        def _persisting_set_focus_mode(mode):  # type: ignore[no-untyped-def]
            original_set_focus(mode)
            # Best-effort persistence — never block the focus change on a DB issue.
            import asyncio

            with __import__("contextlib").suppress(Exception):
                asyncio.create_task(  # noqa: RUF006 — fire-and-forget persistence
                    preference_store.set(_FOCUS_PREF_OWNER, _FOCUS_PREF_KEY, mode),
                )

        router.set_focus_mode = _persisting_set_focus_mode  # type: ignore[method-assign]

        # 1d) Outbound transport bridge — resolves the channel-registry
        # singleton once here (not inside deliver()), then transports
        # router-vetted messages to channel adapters.
        proactive_deliverer = ProactiveDeliverer(
            router=router,
            registry=ChannelRegistry.instance(),
            settings=settings,
        )

        # 2) Digest job — register handler + seed 5-minute schedule. The
        # deliverer is injected so batched rows are actually transported on flush.
        digest_handler = NotificationDigestJob(db=db, deliverer=proactive_deliverer)
        HandlerRegistry.instance().register(digest_handler)
        await _seed_digest_schedule(db)
        log.notifications.info(
            "[notifications] assembly: digest job registered + scheduled",
            extra={"_fields": {"handler": digest_handler.handler_name}},
        )

        # 3) Router-dependent slash commands — previously orphaned because
        # `load_builtin_commands()` imports modules but doesn't call the
        # router-aware factories.
        focus_command = FocusCommand.create_and_register(router, event_bus)
        urgent_command = UrgentCommand.create_and_register(router)
        quiet_command = QuietHoursCommand.create_and_register(db)
        notifications_missed_command = NotificationsMissedCommand.create_and_register(db)
        log.notifications.info(
            "[notifications] assembly: 4 commands registered",
            extra={"_fields": {"commands": [
                focus_command.command, urgent_command.command,
                quiet_command.command, notifications_missed_command.command,
            ]}},
        )

        log.notifications.info("[notifications] assembly.build: exit — all wired")
        return NotificationComponents(
            router=router,
            proactive_deliverer=proactive_deliverer,
            digest_handler=digest_handler,
            focus_command=focus_command,
            urgent_command=urgent_command,
            quiet_command=quiet_command,
            notifications_missed_command=notifications_missed_command,
        )


def _next_run_in_5min() -> str:
    return (datetime.now(UTC) + timedelta(minutes=5)).isoformat()


async def _seed_digest_schedule(db: DbPool) -> None:
    """Insert a single notification_digest row into `jobs` if none exists.

    Idempotent: a second call is a no-op. Uses the `every 5m` schedule syntax
    so the existing scheduler's `_compute_next_run` advances `next_run_at`
    correctly on every completion.
    """
    log.notifications.debug("[notifications] digest schedule: seed entry")
    existing = await db.fetch_all(_SELECT_DIGEST_JOB_SQL, (_DIGEST_HANDLER_NAME,))
    if existing:
        log.notifications.debug(
            "[notifications] digest schedule: already seeded — noop",
            extra={"_fields": {"row_count": len(existing)}},
        )
        return
    job_id = f"digest-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(UTC).isoformat()
    await db.execute(
        _INSERT_JOB_SQL,
        (
            job_id,
            _DIGEST_HANDLER_NAME,
            _DIGEST_SCHEDULE,
            _DIGEST_IDEMPOTENCY_KEY,
            None,
            _next_run_in_5min(),
            "pending",
            0,
            now_iso,
        ),
    )
    log.notifications.info(
        "[notifications] digest schedule seeded",
        extra={"_fields": {"job_id": job_id, "schedule": _DIGEST_SCHEDULE}},
    )
