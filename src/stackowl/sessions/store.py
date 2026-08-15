"""SessionStore — durable lanes, plus a human-readable mirror.

SQLite is the SINGLE source of truth. The JSON file is a DERIVED, WRITE-ONLY
projection of the key->id map, refreshed after each commit and **never read back**
by the platform.

That asymmetry is the whole point. Bakir asked for both (Q4, reaffirmed with the
sync-bug cost on the table), and two stores that are both trusted is a bug class.
A one-way projection cannot drift into disagreement, because nothing consults it:
if the mirror is deleted, corrupted, or hand-edited, the next write regenerates it
and the platform never noticed. It buys legibility at no correctness cost.

Resolution itself lives in :mod:`stackowl.sessions.policy` as pure functions. This
module only persists what that decided — so the branch logic stays testable without
a database, and this stays testable without re-deriving policy.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.sessions.models import (
    Branch,
    ResetReason,
    SessionEntry,
    SessionSource,
    build_session_key,
    new_session_id,
)
from stackowl.sessions.policy import ResetPolicy, expired_reason, resolve

_MIRROR_NAME = "sessions.json"

_COLUMNS = (
    "session_key, session_id, owl_name, channel, created_at, updated_at, "
    "message_count, suspended, resume_pending, resume_reason, was_auto_reset, "
    "auto_reset_reason, is_fresh_reset, expiry_finalized, restart_failures, "
    "chat_id, completed_turns, identity_key, summary_enqueued_for, "
    "parent_session_key"
)


def _to_entry(row: dict[str, Any]) -> SessionEntry:
    """Rehydrate an entry. Booleans are INTEGER in SQLite; reasons are strings."""
    reason = row.get("auto_reset_reason")
    return SessionEntry(
        session_key=row["session_key"],
        session_id=row["session_id"],
        owl_name=row["owl_name"],
        channel=row["channel"],
        created_at=datetime.datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.datetime.fromisoformat(row["updated_at"]),
        message_count=int(row["message_count"] or 0),
        completed_turns=int(row["completed_turns"] or 0),
        identity_key=row.get("identity_key"),
        summary_enqueued_for=row.get("summary_enqueued_for"),
        parent_session_key=row.get("parent_session_key"),
        suspended=bool(row["suspended"]),
        resume_pending=bool(row["resume_pending"]),
        resume_reason=row.get("resume_reason"),
        was_auto_reset=bool(row["was_auto_reset"]),
        auto_reset_reason=ResetReason(reason) if reason else None,
        is_fresh_reset=bool(row["is_fresh_reset"]),
        expiry_finalized=bool(row["expiry_finalized"]),
        restart_failures=int(row["restart_failures"] or 0),
        chat_id=row.get("chat_id"),
    )


#: When THIS process started. Captured at import, which for the core is boot —
#: and on the exec-replace restart path the module is imported afresh, so it
#: re-stamps correctly rather than carrying the dead process's value.
#:
#: Read once here and passed INTO `resolve` so the policy module stays pure.
_PROCESS_STARTED_AT = datetime.datetime.now().astimezone()


class SessionStore:
    """Owns lane persistence. One instance, injected — never a module global."""

    #: Published when a lane's incarnation ends. THE seam of this item: `D09.1`
    #: (background review), `D09.3` (the skill curator) and Q17's memory summary
    #: all need "an idle moment on this conversation", and this is it. Three items
    #: subscribe to one boundary instead of each inventing its own idle detector —
    #: dedup target X3, resolved by architecture rather than by a cleanup pass.
    ROLLOVER_EVENT = "session.rollover"

    def __init__(self, db: DbPool, policy: ResetPolicy | None = None,
                 mirror_dir: Path | None = None,
                 event_bus: object | None = None,
                 process_started_at: datetime.datetime | None = None) -> None:
        self._db = db
        self._policy = policy or ResetPolicy()
        # ESC-13 — when the process that could have frozen a prompt started.
        # Injectable because a test that pins lane behaviour with a fixed clock
        # would otherwise be comparing its own timestamps against the real wall
        # clock and silently exercising the restart trigger instead of the
        # branch it meant to test.
        self._process_started_at = process_started_at or _PROCESS_STARTED_AT
        self._mirror_dir = mirror_dir
        # Duck-typed to keep sessions/ free of an events/ import. None → the
        # rollover is still logged, just not published; nothing breaks.
        self._event_bus = event_bus
        # Chats whose next turn must start a NEW conversation, keyed by the
        # CHANNEL-NATIVE id (D01.7, 2026-07-27).
        #
        # /new could not end a lane because commands dispatch at the gateway,
        # BEFORE routing, while the composite lane is keyed on the owl — and the
        # owl is a routing OUTPUT, which is why _resolve_incarnation runs after
        # routing. So all /new ever has is the channel-native id, and looking a
        # lane up by it found nothing: the command silently did nothing for the
        # life of the feature.
        #
        # In-memory ON PURPOSE, unlike everything else in this store. A pending
        # reset lives for milliseconds — the command and the resolution are the
        # same turn in the same process — and a reset that SURVIVED a restart
        # would be worse than one that is lost: it would silently start a fresh
        # conversation at some unrelated later moment. Losing it costs the user
        # one repeated /new; keeping it costs them a conversation they did not
        # ask to end.
        self._pending_new: set[str] = set()

    # ------------------------------------------------------------------ read

    async def get(self, session_key: str) -> SessionEntry | None:
        rows = await self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions WHERE session_key = ?", (session_key,)
        )
        return _to_entry(rows[0]) if rows else None

    async def resolve_send_target(self, session_key: str) -> str | None:
        """The channel-native destination for ``session_key``, or ``None``.

        This is what proactive delivery needs and what a composite lane key can no
        longer supply on its own. ``None`` for an unknown lane or a channel with no
        per-lane destination — the caller then falls back loudly. A fabricated
        recipient IS the cross-delivery bug, so this never guesses.
        """
        entry = await self.get(session_key)
        target = entry.chat_id if entry else None
        log.gateway.debug(
            "session.resolve_send_target: exit",
            extra={"_fields": {"session_key": session_key, "known_lane": entry is not None,
                               "resolved": target is not None}},
        )
        return target

    async def list_all(self) -> list[SessionEntry]:
        rows = await self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions ORDER BY updated_at DESC"
        )
        return [_to_entry(r) for r in rows]

    # ----------------------------------------------------------- resolution

    async def resolve_for(
        self, source: SessionSource, now: datetime.datetime,
        *, has_active_work: bool = False,
        group_per_user: bool = True, thread_per_user: bool = False,
    ) -> tuple[SessionEntry, Branch, ResetReason | None]:
        """Resolve the lane for an inbound message and persist the outcome.

        Returns ``(entry, branch, reason)``. The branch is returned rather than
        inferred by the caller so the DECISION point is logged from one place and
        a caller can never disagree with what actually happened.
        """
        key = build_session_key(source, group_per_user=group_per_user,
                                thread_per_user=thread_per_user)
        log.gateway.debug(
            "session.resolve: entry",
            extra={"_fields": {"owl": source.owl_name, "channel": source.channel,
                               "chat_type": source.chat_type.value,
                               "session_key": key}},
        )
        # A pending /new is consumed HERE, because this is the first moment the
        # composite lane exists: the command that requested it only had the
        # channel-native id. Consumed before the policy runs so an explicit
        # request is never second-guessed by an automatic rule, and popped
        # unconditionally so one /new ends exactly one conversation — a flag left
        # set would silently start a fresh conversation on every later message,
        # which is the more annoying failure of the two.
        if source.chat_id and source.chat_id in self._pending_new:
            self._pending_new.discard(source.chat_id)
            fresh = await self.start_new_incarnation(key, now=now)
            if fresh is not None:
                log.gateway.info(
                    "session.resolve: explicit /new honoured",
                    extra={"_fields": {"session_key": key,
                                       "new_session_id": fresh.session_id}},
                )
                return fresh, Branch.EXPLICIT_RESET, ResetReason.EXPLICIT
            # No lane yet — /new on a brand-new chat has nothing to end. Fall
            # through and let the ordinary path mint the first one, so the user
            # still lands in a working conversation.
            log.gateway.info(
                "session.resolve: /new with no lane yet — the first conversation starts now",
                extra={"_fields": {"session_key": key}},
            )

        existing = await self.get(key)
        decision = resolve(
            existing, now, self._policy, has_active_work=has_active_work,
            process_started_at=self._process_started_at,
        )

        # The newest message wins on the send target — a Telegram group upgraded to
        # a supergroup re-keys the chat while staying the SAME lane — but a message
        # that carries no target must not erase one we already have. The identity
        # follows the same rule for the same reason: a CLI turn that cannot state
        # one must not unlink the lane from its owner, or the next rollover summary
        # is filed nowhere.
        target = source.chat_target or (existing.chat_id if existing else None)
        identity = source.identity_key or (existing.identity_key if existing else None)
        parent = source.parent_session_key or (
            existing.parent_session_key if existing else None)

        # A RUNNER inherits its identity from the conversation that asked for the
        # work (Bakir's DEBT-13 answer). Knowledge is about a PERSON, not about which
        # machinery produced it, so an objective started from a chat must stage its
        # facts where that chat's recall already looks — otherwise re-keying these
        # lanes would silently repoint where background knowledge lands, which is
        # exactly what the 3a.2 addendum ruled against.
        #
        # Runner lanes ONLY: a chat lane's identity comes from its own ingress and
        # must never arrive through a parent it should not have. Inheritance fills a
        # gap and never overrides a known identity, and a parent that no longer
        # exists (or never had an identity) simply yields None — a runner may outlive
        # the lane that spawned it, and that must not raise on its critical path.
        if identity is None and parent and source.runner:
            parent_entry = await self.get(parent)
            identity = parent_entry.identity_key if parent_entry else None
            log.gateway.debug(
                "session.resolve: runner identity inherited",
                extra={"_fields": {"session_key": key, "parent": parent,
                                   "inherited": identity is not None}},
            )

        if existing is None:
            entry = SessionEntry(
                session_key=key, session_id=new_session_id(now),
                owl_name=source.owl_name, channel=source.channel,
                created_at=now, updated_at=now, chat_id=target,
                identity_key=identity, message_count=1,
                parent_session_key=parent,
            )
        elif decision.reason is ResetReason.RESTART:
            # ESC-13 — the PROCESS ended, not the conversation. A fresh
            # session_id so the frozen prompt cannot be re-minted under an id
            # that already has one, and NOTHING else: the counters describe the
            # user's thread of talk, which did not stop just because the core
            # was redeployed. Resetting them here would also move the idle and
            # daily boundaries every time we ship.
            entry = existing.evolve(
                session_id=new_session_id(now),
                updated_at=now,
                message_count=existing.message_count + 1,
                auto_reset_reason=ResetReason.RESTART,
                was_auto_reset=False,
                is_fresh_reset=False,
            )
        elif decision.mints_new_incarnation:
            # A rollover ENDS an incarnation; it never destroys a transcript
            # (invariant I6). The old session_id stays referenced by messages/
            # cost_records, so the conversation remains searchable.
            #
            # Both counters restart at the boundary: they describe THIS run, not
            # the lane's lifetime. message_count starts at 1 because the message
            # that crossed the boundary belongs to the new incarnation.
            entry = existing.evolve(
                session_id=new_session_id(now),
                created_at=now, updated_at=now,
                message_count=1, completed_turns=0,
                suspended=False, resume_pending=False, resume_reason=None,
                was_auto_reset=decision.reason is not None
                and decision.reason.is_automatic,
                auto_reset_reason=decision.reason,
                is_fresh_reset=False, expiry_finalized=False,
                restart_failures=0, chat_id=target, identity_key=identity,
                parent_session_key=parent,
            )
        else:
            entry = existing.evolve(
                updated_at=now, chat_id=target, identity_key=identity,
                parent_session_key=parent,
                message_count=existing.message_count + 1,
            )

        await self.save(entry)
        log.gateway.info(
            "session.resolve: branch taken",
            extra={"_fields": {"session_key": key, "branch": decision.branch.value,
                               "reason": decision.reason.value if decision.reason else None,
                               "session_id": entry.session_id,
                               "previous_session_id": existing.session_id if existing else None}},
        )
        # A lane the SWEEPER already finalised was announced when it expired, on
        # the clock. Announcing again now — possibly hours later, when the user
        # finally speaks — would fire every consumer twice for one boundary.
        already_announced = existing is not None and existing.expiry_finalized
        announces = decision.reason is None or decision.reason.ends_the_conversation
        if (
            decision.mints_new_incarnation
            and existing is not None
            and not already_announced
            and announces
        ):
            log.gateway.info(
                "session.rollover: old incarnation ended",
                extra={"_fields": {
                    "session_key": key, "old_session_id": existing.session_id,
                    "new_session_id": entry.session_id,
                    "reason": decision.reason.value if decision.reason else None,
                    "message_count": existing.message_count,
                    "completed_turns": existing.completed_turns,
                }},
            )
            self._publish_rollover(existing, entry, decision.reason)
        return entry, decision.branch, decision.reason

    def _publish_rollover(self, old: SessionEntry, new: SessionEntry,
                          reason: ResetReason | None) -> None:
        """Announce the boundary. Never blocks it.

        Subscribers are expected to enqueue DURABLE work rather than do it here
        (Bakir's Q15): a rollover fires at 4 AM unattended, which is precisely
        when nobody is watching, so anything done inline is lost if the process
        dies mid-handler.

        A subscriber that throws must not prevent the conversation from starting —
        the EventBus already isolates handlers, and this catch is the second belt.
        """
        if self._event_bus is None:
            return
        payload = {
            "session_key": old.session_key,
            "old_session_id": old.session_id,
            "new_session_id": new.session_id,
            "reason": reason.value if reason else None,
            "owl_name": old.owl_name,
            "channel": old.channel,
            # Everything a consumer needs to enqueue durable work WITHOUT
            # re-reading the store. identity_key is what the summary is filed
            # under, and it must travel with the event because the sweeper
            # publishes at 4 AM with no ingress context to re-derive it from.
            "identity_key": old.identity_key,
            "message_count": old.message_count,
            "completed_turns": old.completed_turns,
            "ended_at": new.created_at.isoformat(),
        }
        try:
            emit = self._event_bus.emit  # type: ignore[attr-defined]
            emit(self.ROLLOVER_EVENT, payload)
        except Exception as exc:  # no-hidden-errors: the boundary still stands
            log.gateway.error(
                "session.rollover: publication failed — the boundary still happened",
                exc_info=exc,
                extra={"_fields": {"session_key": old.session_key,
                                   "new_session_id": new.session_id}},
            )

    # ----------------------------------------------------------------- write

    async def save(self, entry: SessionEntry) -> SessionEntry:
        """Upsert a lane, then refresh the mirror. The mirror never blocks a save."""
        await self._db.execute(
            f"""
            INSERT INTO sessions ({_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_key) DO UPDATE SET
                session_id=excluded.session_id, owl_name=excluded.owl_name,
                channel=excluded.channel, created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                message_count=excluded.message_count,
                completed_turns=excluded.completed_turns,
                suspended=excluded.suspended, resume_pending=excluded.resume_pending,
                resume_reason=excluded.resume_reason,
                was_auto_reset=excluded.was_auto_reset,
                auto_reset_reason=excluded.auto_reset_reason,
                is_fresh_reset=excluded.is_fresh_reset,
                expiry_finalized=excluded.expiry_finalized,
                restart_failures=excluded.restart_failures,
                -- COALESCE, not excluded.chat_id: a channel that cannot state a
                -- target (CLI) must never blank a real one we already know, or a
                -- single CLI turn would silently unaddress the Telegram lane.
                chat_id=COALESCE(excluded.chat_id, sessions.chat_id),
                -- Same COALESCE rule, same reason: a turn that cannot state an
                -- identity must not unlink the lane from its owner, or the next
                -- rollover summary is filed where recall never looks.
                identity_key=COALESCE(excluded.identity_key, sessions.identity_key),
                summary_enqueued_for=excluded.summary_enqueued_for,
                -- Same COALESCE rule as chat_id/identity_key: a run that cannot
                -- state its parent must never orphan the lane.
                parent_session_key=COALESCE(excluded.parent_session_key,
                                            sessions.parent_session_key)
            """,
            (
                entry.session_key, entry.session_id, entry.owl_name, entry.channel,
                entry.created_at.isoformat(), entry.updated_at.isoformat(),
                entry.message_count, int(entry.suspended), int(entry.resume_pending),
                entry.resume_reason, int(entry.was_auto_reset),
                entry.auto_reset_reason.value if entry.auto_reset_reason else None,
                int(entry.is_fresh_reset), int(entry.expiry_finalized),
                entry.restart_failures, entry.chat_id,
                entry.completed_turns, entry.identity_key,
                entry.summary_enqueued_for, entry.parent_session_key,
            ),
        )
        await self._project_mirror()
        return entry

    async def request_new_incarnation(self, chat_key: str) -> None:
        """Record that this chat's NEXT turn must start a fresh conversation.

        What ``/new`` calls. It runs at the gateway, before routing, so the
        composite lane it needs to end does not exist yet — only the
        channel-native id does. Marking the intent here and consuming it in
        ``resolve_for`` keeps the actual ending in the ONE place that already
        knows how to end a lane, rather than teaching a second place to do it.

        Idempotent: two ``/new``s before a turn still end one conversation.
        """
        if not chat_key:
            log.gateway.warning(
                "session.new: requested with no chat key — ignored",
                extra={"_fields": {"chat_key": chat_key}},
            )
            return
        self._pending_new.add(chat_key)
        log.gateway.info(
            "session.new: requested — the next turn starts a fresh conversation",
            extra={"_fields": {"chat_key": chat_key}},
        )

    async def start_new_incarnation(
        self, session_key: str, reason: ResetReason = ResetReason.EXPLICIT,
        now: datetime.datetime | None = None,
    ) -> SessionEntry | None:
        """End this lane's incarnation on purpose and begin a fresh one.

        The mechanism behind ``/new``. It shares ONE code path with the automatic
        boundary — archive, announce, mint — so the four triggers (daily, idle,
        explicit, context-full) cannot drift into behaving differently. That was
        Bakir's Q8 observation: /new and the daily rollover are the same thing with
        different causes.

        ``is_fresh_reset`` is set and ``was_auto_reset`` is NOT: the user did this
        deliberately and must never be told their conversation "expired". Returns
        ``None`` for a lane that does not exist yet — there is nothing to end, and
        inventing one would report a rollover that never happened.
        """
        stamp = now or datetime.datetime.now().astimezone()
        existing = await self.get(session_key)
        if existing is None:
            log.gateway.info(
                "session.new: no such lane — nothing to end",
                extra={"_fields": {"session_key": session_key}},
            )
            return None
        fresh = existing.evolve(
            session_id=new_session_id(stamp),
            created_at=stamp, updated_at=stamp,
            message_count=0, completed_turns=0,
            suspended=False, resume_pending=False, resume_reason=None,
            was_auto_reset=False, auto_reset_reason=reason,
            is_fresh_reset=True, expiry_finalized=False, restart_failures=0,
        )
        await self.save(fresh)
        log.gateway.info(
            "session.rollover: old incarnation ended",
            extra={"_fields": {
                "session_key": session_key, "old_session_id": existing.session_id,
                "new_session_id": fresh.session_id, "reason": reason.value,
                "message_count": existing.message_count,
                "completed_turns": existing.completed_turns,
            }},
        )
        self._publish_rollover(existing, fresh, reason)
        return fresh

    async def consume_reset_notice(self, entry: SessionEntry) -> SessionEntry:
        """Clear ``was_auto_reset`` after the notice has been shown ONCE (I5)."""
        if not entry.was_auto_reset:
            return entry
        return await self.save(entry.evolve(was_auto_reset=False))

    async def lanes_awaiting_summary(self) -> list[SessionEntry]:
        """Lanes whose boundary was announced but whose summary was never queued.

        THE RECOVERY QUERY FOR DEBT-11. Publishing ``session.rollover`` is
        in-memory and fire-and-forget: if no consumer is listening — or the core
        exec-replaces itself in the window before one enqueues — the boundary is
        lost, and ``expiry_finalized`` then makes the double-announce guard suppress
        any second announcement. Observed live: the sweeper finalised a lane at
        09:03:04Z with nothing subscribed, and the next message correctly declined
        to re-announce.

        This makes the question answerable from the ROW instead of from a live
        subscriber. It reads ``expiry_finalized`` because that is the sweeper's
        path — the unattended 4 AM case Q15 was actually written about — and the
        sweeper finalises WITHOUT minting, so the lane still holds the incarnation
        that ended and the boundary is still recoverable from it.

        Compared per INCARNATION, not per lane: a marker from a previous boundary
        must never silence the next one.
        """
        rows = await self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM sessions "
            "WHERE expiry_finalized = 1 "
            "  AND (summary_enqueued_for IS NULL OR summary_enqueued_for != session_id) "
            "ORDER BY updated_at ASC"
        )
        entries = [_to_entry(r) for r in rows]
        log.gateway.debug(
            "session.lanes_awaiting_summary: exit",
            extra={"_fields": {"count": len(entries)}},
        )
        return entries

    async def mark_summary_enqueued(self, session_key: str,
                                    session_id: str) -> None:
        """Record that ``session_id``'s summary is queued, so it is queued ONCE.

        Written only after the enqueue actually succeeded. A failed enqueue must
        leave the lane recoverable, or the retry this backstop exists to provide is
        thrown away on the first hiccup.
        """
        await self._db.execute(
            "UPDATE sessions SET summary_enqueued_for = ? WHERE session_key = ?",
            (session_id, session_key),
        )
        await self._project_mirror()
        log.gateway.info(
            "session.mark_summary_enqueued: recorded",
            extra={"_fields": {"session_key": session_key, "session_id": session_id}},
        )

    async def record_completed_turn(self, session_key: str) -> None:
        """Count a turn that actually produced a reply.

        Kept separate from ``message_count`` (bumped at resolution) because the
        DIFFERENCE between the two is the signal: a lane receiving messages and
        completing no turns is a lane that is failing, and one number cannot say
        that. It is also what a rollover consumer reads to decide whether an
        incarnation contained a real conversation.

        An UNKNOWN lane is a no-op, never an insert. This is called from the
        turn-end path, which also runs for background work that never passed
        through ingress and therefore has no lane; inventing one there would
        create conversations nobody had.
        """
        entry = await self.get(session_key)
        if entry is None:
            log.gateway.debug(
                "session.record_completed_turn: no such lane — not creating one",
                extra={"_fields": {"session_key": session_key}},
            )
            return
        await self.save(entry.evolve(completed_turns=entry.completed_turns + 1))

    async def clear_resume_pending(self, session_key: str) -> None:
        """Called after a resumed turn completes successfully."""
        entry = await self.get(session_key)
        if entry is not None and entry.resume_pending:
            await self.save(entry.evolve(resume_pending=False, resume_reason=None,
                                         restart_failures=0))

    async def sweep(
        self, now: datetime.datetime | None = None,
        is_busy: Callable[[SessionEntry], Awaitable[bool]] | None = None,
    ) -> tuple[int, int]:
        """Finalise lanes whose incarnation expired while nobody was talking.

        Without this, a rollover only happens when the user next sends a message —
        so the 4 AM boundary would really mean "whenever you next say something",
        and Q17's overnight summary would never run unattended. This is what makes
        the boundary a CLOCK event rather than a traffic event.

        It FINALISES rather than mints: the old incarnation is announced as ended
        and stamped ``expiry_finalized``, and the next inbound message mints the
        new id through the normal path. Minting here would hand out an incarnation
        nobody is using and start a conversation the user never opened.

        ``is_busy`` carries Bakir's Q12 rule (invariant I4) and is what the caller
        composes from its four conditions — a running background process, an
        in-flight durable task, an active objective, a pending clarify. A busy lane
        is SKIPPED, not delayed-and-forced: it is re-examined on the next sweep.

        Returns ``(finalized, skipped_active)``.
        """
        stamp = now or datetime.datetime.now().astimezone()
        log.gateway.debug("session.sweep: entry",
                          extra={"_fields": {"at": stamp.isoformat()}})
        finalized = skipped = 0
        for entry in await self.list_all():
            if entry.expiry_finalized or entry.suspended or entry.resume_pending:
                continue
            if expired_reason(entry, stamp, self._policy) is None:
                continue
            if is_busy is not None and await is_busy(entry):
                skipped += 1
                log.gateway.info(
                    "session.sweep: lane is busy — expiry skipped (I4)",
                    extra={"_fields": {"session_key": entry.session_key,
                                       "session_id": entry.session_id}},
                )
                continue
            reason = expired_reason(entry, stamp, self._policy)
            ended = entry.evolve(expiry_finalized=True, was_auto_reset=True,
                                 auto_reset_reason=reason)
            await self.save(ended)
            log.gateway.info(
                "session.rollover: old incarnation ended",
                extra={"_fields": {
                    "session_key": entry.session_key,
                    "old_session_id": entry.session_id,
                    "new_session_id": None,  # minted lazily on the next message
                    "reason": reason.value if reason else None,
                    "message_count": entry.message_count,
                    "completed_turns": entry.completed_turns,
                }},
            )
            self._publish_rollover(entry, ended, reason)
            finalized += 1
        log.gateway.info(
            "session.sweep: finalized",
            extra={"_fields": {"count": finalized, "skipped_active": skipped}},
        )
        return finalized, skipped

    async def prune(self, older_than: datetime.datetime) -> int:
        """Drop lane RECORDS not touched since ``older_than``. Transcripts stay.

        Bakir's Q11: keep the transcript, prune the record. A lane with active
        recovery state is never pruned — dropping it would lose the very fact that
        a turn needs resuming.
        """
        rows = await self._db.fetch_all(
            "SELECT session_key FROM sessions WHERE updated_at < ? "
            "AND suspended = 0 AND resume_pending = 0",
            (older_than.isoformat(),),
        )
        for row in rows:
            await self._db.execute("DELETE FROM sessions WHERE session_key = ?",
                                   (row["session_key"],))
        if rows:
            await self._project_mirror()
        log.gateway.info("session.prune: exit",
                         extra={"_fields": {"pruned": len(rows)}})
        return len(rows)

    # ---------------------------------------------------------------- mirror

    def mirror_path(self) -> Path:
        base = self._mirror_dir or StackowlHome.workspace()
        return base / _MIRROR_NAME

    async def _project_mirror(self) -> None:
        """Rewrite the key->id mirror. Failure is logged and swallowed.

        Nothing reads this file, so a failed projection cannot corrupt behaviour —
        at worst the human-readable view is stale until the next write. Letting it
        raise would make a debugging aid capable of breaking a conversation, which
        would be exactly backwards.
        """
        try:
            entries = await self.list_all()
            payload = {
                "_note": "DERIVED, WRITE-ONLY. SQLite is the source of truth; "
                         "nothing reads this file. Safe to delete — it regenerates.",
                "lanes": {
                    e.session_key: {
                        "session_id": e.session_id,
                        "owl": e.owl_name,
                        "channel": e.channel,
                        "created_at": e.created_at.isoformat(),
                        "updated_at": e.updated_at.isoformat(),
                        "messages": e.message_count,
                        "completed_turns": e.completed_turns,
                    }
                    for e in entries
                },
            }
            path = self.mirror_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)  # atomic on POSIX and Windows
        except Exception as exc:  # no-hidden-errors: log, never break a turn
            log.gateway.error(
                "session.mirror: projection failed — SQLite is unaffected",
                exc_info=exc, extra={"_fields": {"path": str(self.mirror_path())}},
            )
