"""DeliverySpec — resolve a cron-born job's recipient from DURABLE state (C1).

A scheduler poll has no live session, no ``TraceContext`` and no channel, so it
cannot recover a recipient from request context (the root cause of C1: a
target-less proactive send rides telegram's shared mutable ``_last_chat_id`` and,
on a fresh process, delivers to nobody while dishonestly recording a result).

:class:`DeliverySpec` resolves the recipient SOLELY from the job's persisted
``target_channels`` / ``target_addresses`` columns (primitive #1). It returns the
``[(channel, native_target)]`` pairs every proactive surface delivers to. A
channel listed with no resolvable address yields NO pair and is reported via
:meth:`unresolved_channels` so the caller records that channel as *undeliverable*
— loudly, never ``delivered``, never falling back to ``_last_*``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from stackowl.config.settings import Settings
    from stackowl.scheduler.job import Job


def resolve_owner_addresses(
    settings: Settings, channels: list[str]
) -> dict[str, str | int]:
    """Resolve each proactive channel's DURABLE owner destination from config.

    A cron-born brief has no live session, so its recipient must come from durable
    config. For a single-user personal assistant the owner's telegram chat id IS
    the (sole) allowed user id — a Telegram private chat's ``chat_id`` equals the
    user's id. A channel with no resolvable owner token is OMITTED here: the
    :class:`DeliverySpec` resolver then reports it ``undeliverable`` loudly (never a
    fake ``delivered``, never a ``_last_*`` guess). No hardcoded channel names
    drive LOGIC beyond the per-adapter native-token shape.

    Lives next to :class:`DeliverySpec` so every producer path (scheduler,
    website_watch, goal_execution) shares ONE owner→native-target resolver.
    """
    addresses: dict[str, str | int] = {}
    for channel in channels:
        # TODO(channels): replace the telegram-only branch with a per-channel
        # native-token resolver registry as channels grow.
        if channel == "telegram":
            allowed = sorted(settings.telegram_channel.allowed_user_ids)
            # Only a SINGLE unambiguous owner yields a durable address — a multi-
            # user allowlist has no single proactive recipient (left undeliverable).
            if len(allowed) == 1:
                addresses[channel] = allowed[0]
            elif allowed:
                log.notifications.warning(
                    "[notifications] resolve_owner_addresses: telegram has multiple "
                    "allowed users — no single proactive recipient (undeliverable)",
                    extra={"_fields": {"count": len(allowed)}},
                )
            else:
                log.notifications.warning(
                    "[notifications] resolve_owner_addresses: telegram has no allowed "
                    "user id — brief recipient unresolved (undeliverable)",
                )
        else:
            # Other channels have no durable owner token at seed time; the brief
            # for them is recorded undeliverable until a real recipient is wired.
            log.notifications.debug(
                "[notifications] resolve_owner_addresses: no durable owner token",
                extra={"_fields": {"channel": channel}},
            )
    return addresses


@dataclass(frozen=True)
class DeliverySpec:
    """Immutable resolved recipient set for one job, built from durable state."""

    job_id: str
    _resolved: tuple[tuple[str, str | int], ...]
    _unresolved: tuple[str, ...]

    @classmethod
    def from_job(cls, job: Job) -> DeliverySpec:
        """Resolve ``[(channel, native_target)]`` from the job's durable columns.

        Reuses the job's own ``target_channels`` / ``target_addresses`` (round-
        tripped by ``scheduler_helpers.row_to_job``). No request context is
        consulted. The native token type is preserved as persisted (telegram
        ``int`` chat id, slack ``str`` channel id).
        """
        # 1. ENTRY
        log.notifications.debug(
            "[notifications] recipient.from_job: entry",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "channel_count": len(job.target_channels),
                }
            },
        )
        resolved: list[tuple[str, str | int]] = []
        unresolved: list[str] = []
        addresses = job.target_addresses or {}
        for channel in job.target_channels or []:
            # 2. DECISION — a channel with no persisted address cannot be sent to.
            target = addresses.get(channel)
            if target is None or (isinstance(target, str) and not target.strip()):
                unresolved.append(channel)
                log.notifications.warning(
                    "[notifications] recipient.from_job: channel has no durable "
                    "address — undeliverable (no _last_* guess)",
                    extra={"_fields": {"job_id": job.job_id, "channel": channel}},
                )
                continue
            resolved.append((channel, target))

        # 4. EXIT
        log.notifications.debug(
            "[notifications] recipient.from_job: exit",
            extra={
                "_fields": {
                    "job_id": job.job_id,
                    "resolved": len(resolved),
                    "unresolved": len(unresolved),
                }
            },
        )
        return cls(
            job_id=job.job_id,
            _resolved=tuple(resolved),
            _unresolved=tuple(unresolved),
        )

    def pairs(self) -> list[tuple[str, str | int]]:
        """Return the resolvable ``(channel, native_target)`` pairs (may be empty)."""
        return list(self._resolved)

    def unresolved_channels(self) -> list[str]:
        """Return channels listed on the job but with no durable address.

        The caller records each as ``undeliverable`` (never ``delivered``).
        """
        return list(self._unresolved)

    def has_recipient(self) -> bool:
        """True iff at least one channel resolved to a concrete destination."""
        return bool(self._resolved)
