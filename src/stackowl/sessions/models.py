"""Session identity — the lane, its incarnation, and how a lane is named.

The central idea, ported from Hermes (`gateway/session.py`) and adapted: session
identity is split in TWO.

* ``session_key``  — the conversation LANE. Deterministic, derived from where the
  message came from. NEVER changes (invariant I1).
* ``session_id``   — this INCARNATION of the lane. A reset keeps the key and mints
  a new id; ids are never reused (invariant I2).

StackOwl has only ever had the second one, and it never changed — which is exactly
why it could not start or end a conversation. See
``docs/hermes-mapping/designs/D01.7.md``.

DIVERGENCE FROM HERMES: our key is prefixed with the OWL. They have one agent; we
have owls, and a different owl means a different persona and therefore a different
prompt (D01.1). Talking to Brain and talking to Scout are two conversations.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum

_KEY_SEP = ":"


class ChatType(StrEnum):
    """Where a message came from, structurally. Drives isolation (Q5)."""

    DM = "dm"
    GROUP = "group"
    CHANNEL = "channel"
    THREAD = "thread"


class ResetReason(StrEnum):
    """Why an incarnation ended. Surfaced to the user for the auto- cases (I5)."""

    DAILY = "daily"          # the 4 AM boundary
    IDLE = "idle"            # no activity for idle_minutes
    EXPLICIT = "explicit"    # the user typed /new
    CONTEXT_FULL = "context_full"  # compression needed the boundary (D03.2)
    SUSPENDED = "suspended"  # hard wipe: /stop, or the stuck-loop escape

    @property
    def is_automatic(self) -> bool:
        """True when StackOwl decided, not the user.

        Only automatic resets produce the 'new conversation' notice — an explicit
        /new must never be reported back to the user as though it expired.
        """
        return self in (ResetReason.DAILY, ResetReason.IDLE, ResetReason.CONTEXT_FULL)


class Branch(StrEnum):
    """Which arm of the resolution took effect. Logged as the DECISION point.

    Exactly one branch executes per inbound message (invariant I3).
    """

    NEW = "new"                # no entry existed
    SUSPENDED = "suspended"    # hard wipe wins over everything
    RESUME = "resume"          # soft recovery: PRESERVE the id
    EXPIRED = "expired"        # policy fired: new id
    EXISTING = "existing"      # nothing fired: carry on


@dataclass(frozen=True, slots=True)
class SessionSource:
    """Where an inbound message came from. The raw material for a session key."""

    owl_name: str
    channel: str
    chat_type: ChatType = ChatType.DM
    chat_id: str | None = None
    thread_id: str | None = None
    participant_id: str | None = None
    #: The channel-NATIVE send target for this message (a Telegram chat_id, a
    #: Slack channel id). Kept SEPARATE from ``chat_id`` because the two are not
    #: the same thing: ``chat_id`` identifies the conversation for KEYING, while
    #: this addresses it for DELIVERY. They coincide in a Telegram DM and diverge
    #: everywhere else — a Telegram group's chat id is not the asking user's, and
    #: a Slack lane is a hash that was never a channel id at all. ``None`` when the
    #: channel cannot state a target (CLI); that is honest, not a failure.
    chat_target: str | None = None
    #: WHO this message is from, after alias resolution — the key durable
    #: knowledge is filed under. Carried on the lane so the rollover summary can
    #: be staged where recall actually looks: facts are about a PERSON, not about
    #: which owl happened to hear them. ``None`` when the channel has no resolver
    #: or the lane has no person behind it (a runner lane); fabricating one would
    #: misattribute somebody's memory.
    identity_key: str | None = None


@dataclass(frozen=True, slots=True)
class SessionEntry:
    """One lane and its current incarnation, plus the flags that drive resolution.

    Frozen: every transition returns a new entry via :meth:`evolve`, so a caller
    can never mutate shared state by accident and every change is an explicit,
    greppable call.
    """

    session_key: str
    session_id: str
    owl_name: str
    channel: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    #: Inbound messages on THIS incarnation. Replaces ``turn_count``, which was
    #: never incremented by anything — see migration 0096.
    message_count: int = 0
    #: Turns on this incarnation that produced a reply. Kept separate from
    #: ``message_count`` because the DIFFERENCE is the signal: a lane where the
    #: two diverge is a lane that is failing to answer.
    completed_turns: int = 0
    #: Who this lane belongs to, after alias resolution. What a rollover summary
    #: is filed under, and the owner the sweeper binds its stores to.
    identity_key: str | None = None
    #: Where to SEND to reach this lane, in the channel's own terms. Stored rather
    #: than derived: a composite lane key is not int()-able into a chat id, and
    #: parsing one would couple every delivery path to the key's exact shape.
    #: ``None`` for channels with no per-lane destination (CLI).
    chat_id: str | None = None

    # --- state flags, evaluated in the order of Branch above (invariant I3) ---
    # Hard wipe. Set by /stop or the 3-strike stuck-loop escape. Beats everything.
    suspended: bool = False
    # Soft recovery. Set when the core exec-replaced or drained mid-turn. PRESERVES
    # the id so the transcript continues; cleared after the next successful turn.
    # Load-bearing for us in a way it is not for Hermes: our core replaces itself
    # on every code change, so this is the common case, not the edge case (Q6).
    resume_pending: bool = False
    resume_reason: str | None = None
    # Consumed ONCE to show the user a one-line notice (invariant I5). A boundary
    # the user cannot see is one they experience as amnesia.
    was_auto_reset: bool = False
    auto_reset_reason: ResetReason | None = None
    # Kept distinct from was_auto_reset so an explicit /new never reports itself as
    # an expiry — the user did that on purpose and must not be told it broke.
    is_fresh_reset: bool = False
    # Set by the background sweeper after rollover consumers ran.
    expiry_finalized: bool = False
    # Consecutive restarts that failed to complete a turn on THIS lane. At the
    # threshold the lane is suspended so one poisoned conversation cannot take the
    # others down with it (Q7).
    restart_failures: int = 0

    def evolve(self, **changes: object) -> SessionEntry:
        """Return a copy with ``changes`` applied. The only way an entry changes."""
        return replace(self, **changes)  # type: ignore[arg-type]


def new_session_id(now: datetime.datetime) -> str:
    """Mint an incarnation id: ``YYYYMMDD_HHMMSS_<8hex>``.

    Sortable by time at a glance, and unique even when two lanes roll in the same
    second — the random suffix is what makes invariant I2 (never reuse an id) hold
    without a uniqueness query.
    """
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def build_session_key(source: SessionSource, *, group_per_user: bool = True,
                      thread_per_user: bool = False) -> str:
    """Derive the lane name. Deterministic: same source, same key, forever (I1).

    Format::

        owl:{owl}:{channel}:{chat_type}[:{chat_id}][:{thread_id}][:{participant}]

    Isolation follows Bakir's Q5 answer, which matches Hermes' defaults:

    * DMs are always private — the chat id already identifies one person.
    * Groups/channels are isolated PER USER by default (``group_per_user``), so
      your history is not visible to whoever asks next.
    * Threads are SHARED by default (``thread_per_user`` False), because a thread
      is a focused sub-discussion where shared context is the point.

    Components are dropped when absent rather than emitted empty, so a source that
    gains a thread id later produces a genuinely different lane instead of one that
    silently collides with the thread-less form.
    """
    parts: list[str] = ["owl", source.owl_name, source.channel, source.chat_type.value]
    if source.chat_id:
        parts.append(source.chat_id)
    if source.thread_id:
        parts.append(source.thread_id)
    if _needs_participant(source, group_per_user=group_per_user,
                          thread_per_user=thread_per_user) and source.participant_id:
        parts.append(source.participant_id)
    return _KEY_SEP.join(parts)


def _needs_participant(source: SessionSource, *, group_per_user: bool,
                       thread_per_user: bool) -> bool:
    """Whether this source's lane is per-user rather than shared."""
    if source.chat_type is ChatType.DM:
        return False  # a DM is already one person
    if source.thread_id:
        return thread_per_user
    return group_per_user


def is_shared_lane(source: SessionSource, *, group_per_user: bool = True,
                   thread_per_user: bool = False) -> bool:
    """True when several people share this lane.

    A shared lane must prefix each speaker's name onto the USER MESSAGE, never into
    the system prompt — putting it in the prompt would make the prompt vary by
    speaker and break D01.1's stability invariant for the whole conversation.
    """
    return not _needs_participant(source, group_per_user=group_per_user,
                                  thread_per_user=thread_per_user) and (
        source.chat_type is not ChatType.DM
    )


def new_entry(source: SessionSource, now: datetime.datetime,
              *, group_per_user: bool = True,
              thread_per_user: bool = False) -> SessionEntry:
    """A fresh lane at its first incarnation."""
    return SessionEntry(
        session_key=build_session_key(source, group_per_user=group_per_user,
                                      thread_per_user=thread_per_user),
        session_id=new_session_id(now),
        owl_name=source.owl_name,
        channel=source.channel,
        created_at=now,
        updated_at=now,
        chat_id=source.chat_target,
        identity_key=source.identity_key,
    )


# Column order for the SQLite table and the JSON mirror. Declared once here so the
# store, the migration and the mirror cannot drift apart.
ENTRY_FIELDS: tuple[str, ...] = (
    "session_key", "session_id", "owl_name", "channel", "created_at", "updated_at",
    "message_count", "suspended", "resume_pending", "resume_reason", "was_auto_reset",
    "auto_reset_reason", "is_fresh_reset", "expiry_finalized", "restart_failures",
    "chat_id", "completed_turns", "identity_key",
)
