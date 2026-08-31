"""Session identity — the lane, its incarnation, and how a lane is named.

The central idea, ported from the reference platform (`gateway/session.py`) and adapted: session
identity is split in TWO.

* ``session_key``  — the conversation LANE. Deterministic, derived from where the
  message came from. NEVER changes (invariant I1).
* ``conversation_id``   — this INCARNATION of the lane. A reset keeps the key and mints
  a new id; ids are never reused (invariant I2).

StackOwl has only ever had the second one, and it never changed — which is exactly
why it could not start or end a conversation. See
``docs/reference-mapping/designs/D01.7.md``.

DIVERGENCE FROM THE REFERENCE PLATFORM: our key is prefixed with the OWL. They have one agent; we
have owls, and a different owl means a different persona and therefore a different
prompt (D01.1). Talking to Brain and talking to Scout are two conversations.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum

_KEY_SEP = ":"
#: First segment of every lane this module mints. One source: the builder writes
#: it and `_is_runner_lane` reads it, so they cannot drift apart.
_LANE_ROOT = "owl"


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
    RESTART = "restart"      # the PROCESS ended, not the conversation (ESC-13)

    @property
    def is_automatic(self) -> bool:
        """True when StackOwl decided, not the user.

        Only automatic resets produce the 'new conversation' notice — an explicit
        /new must never be reported back to the user as though it expired.

        RESTART is deliberately absent. It is not the user's business that the
        core exec-replaced itself, and it is not rare: 29 core starts were logged
        on 2026-08-15 alone, so announcing it would mean 29 "new conversation"
        notices in a day for a conversation that never actually ended.
        """
        return self in (ResetReason.DAILY, ResetReason.IDLE, ResetReason.CONTEXT_FULL)

    @property
    def ends_the_conversation(self) -> bool:
        """True when the CONVERSATION ended, not merely the process running it.

        The four original reasons all mean the user's thread of talk is over, so a
        rollover is published and subscribers summarise the incarnation that
        closed. RESTART means only that the process died and the frozen prompt
        must be re-derived under a fresh id (ESC-13) — the transcript continues
        uninterrupted, so publishing would invent a conversation boundary that did
        not happen, and would do it on every deploy.
        """
        return self is not ResetReason.RESTART


class Branch(StrEnum):
    """Which arm of the resolution took effect. Logged as the DECISION point.

    Exactly one branch executes per inbound message (invariant I3).
    """

    NEW = "new"                # no entry existed
    SUSPENDED = "suspended"    # hard wipe wins over everything
    RESUME = "resume"          # soft recovery: PRESERVE the id
    EXPIRED = "expired"        # policy fired: new id
    EXISTING = "existing"      # nothing fired: carry on
    # The user typed /new. Distinct from EXPIRED on purpose: the branch is what
    # gets logged as the DECISION, and recording a deliberate request as
    # "expired" is exactly the confusion invariant I5 exists to prevent —
    # a user who asked for a fresh conversation must never be told theirs
    # lapsed.
    EXPLICIT_RESET = "explicit_reset"


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
    #: WHAT KIND of non-chat runner this is (``objective``, ``cron``, ``subagent``,
    #: ``recovery``…), and WHICH one. Present together or not at all: their presence
    #: is what makes this a RUNNER lane rather than a chat lane (Q9).
    runner: str | None = None
    runner_id: str | None = None
    #: The conversation that ASKED for this work, when there was one. A runner gets
    #: its own lane so it earns its own frozen prompt, and this keeps its summary
    #: attributed to the conversation that requested it instead of fragmenting the
    #: story (Q17 + Q19, reconciled). ``None`` for a cron job nobody asked for.
    parent_session_key: str | None = None


@dataclass(frozen=True, slots=True)
class SessionEntry:
    """One lane and its current incarnation, plus the flags that drive resolution.

    Frozen: every transition returns a new entry via :meth:`evolve`, so a caller
    can never mutate shared state by accident and every change is an explicit,
    greppable call.
    """

    session_key: str
    conversation_id: str
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
    #: The incarnation whose rollover summary has already been ENQUEUED. Not a
    #: timestamp: a bare "when" cannot tell yesterday's boundary from today's, so
    #: the first marker would silence every later boundary on this lane. ``None``
    #: means no summary has been enqueued for any incarnation yet (DEBT-11).
    summary_enqueued_for: str | None = None
    #: For a runner lane, the conversation that caused it. Summaries are attributed
    #: to the parent when set — see migration 0100.
    parent_session_key: str | None = None
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
    # Load-bearing for us in a way it is not for the reference platform: our core replaces itself
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


def new_conversation_id(now: datetime.datetime) -> str:
    """Mint an incarnation id: ``YYYYMMDD_HHMMSS_<8hex>``.

    Sortable by time at a glance, and unique even when two lanes roll in the same
    second — the random suffix is what makes invariant I2 (never reuse an id) hold
    without a uniqueness query.
    """
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


#: Prefixes of session keys minted by the PLATFORM for its own work, not by a
#: human conversation. Kept here beside :func:`build_session_key` because this is
#: where lane naming is owned; they were previously f-strings duplicated at the
#: two mint sites with nothing tying them together.
#:
#:   ``goal-``      scheduler/handlers/goal_execution.py — job lanes
#:   ``incident-``  scheduler/handlers/incident_escalation.py — self-heal RCA lanes
#:
#:   ``job:``       scheduler/scheduler.py:133 — every scheduled job's lane
#:   ``recover-``   pipeline/durable/recovery.py:509 — the durable retry driver
#:   ``shadow-``    owls/shadow_validator.py:271 — shadow-mode replay lanes
#:
#: A human lane is ``owl:{owl}:{channel}:...`` (see build_session_key below), so
#: these prefixes cannot collide with one.
#:
#: THE LAST THREE WERE MEASURED, NOT GUESSED. Scanning every table that carries a
#: session_key found 4,150 distinct lanes, and beyond the two above there were
#: 523 ``shadow-``, 96 ``job:`` and 23 ``recover-`` — all minted in src/ with no
#: user input anywhere near them. ``recover-`` matters most: it is the durable
#: retry driver, the same family as the RetryActuator whose prompts were being
#: staged as things the operator said.
MACHINE_LANE_PREFIXES: tuple[str, ...] = (
    "goal-", "incident-", "job:", "recover-", "shadow-",
)


#: The four values a CHAT lane can carry in its fourth segment. Derived from the
#: enum rather than restated, so a fifth ChatType cannot silently start reading as
#: a runner lane — which would classify a real conversation as the platform
#: talking to itself and drop the user's facts.
_CHAT_TYPE_VALUES: frozenset[str] = frozenset(c.value for c in ChatType)


def _is_runner_lane(session_key: str) -> bool:
    """Whether this key came from :func:`build_session_key`'s RUNNER branch.

    THE DISCRIMINATOR IS STRUCTURAL AND WE OWN IT, which is what makes it safe to
    read rather than guess. The builder emits exactly two shapes::

        owl:{owl}:{runner}:{runner_id}                  a runner lane
        owl:{owl}:{channel}:{chat_type}[:{chat_id}]...  a chat lane

    so the fourth segment is a ``ChatType`` for every chat lane and a runner id
    for every runner lane. There is deliberately NO list of runner names here:
    ``SessionSource.runner`` is free text ("objective", "cron", "subagent",
    "recovery", …) precisely so the platform can mint a new kind of background
    work without a migration, and a list of them would be the next thing to drift
    out of date — which is how ``recovery`` leaked in the first place.

    Fails toward HUMAN on anything it cannot read. `session_key` is not always a
    lane this module minted (the CLI and tests pass their own strings), and an
    unparseable key is a question; the answer that loses a memory is the wrong one
    to guess.
    """
    parts = session_key.split(_KEY_SEP)
    if len(parts) < 4 or parts[0] != _LANE_ROOT:
        return False
    return parts[3] not in _CHAT_TYPE_VALUES


def is_machine_lane(session_key: str | None) -> bool:
    """Whether this lane is the platform talking to itself (DEBT-35).

    Used by the conversation miner to skip lanes that cannot contain a user fact
    by construction. Keys on something we CONTROL rather than something we infer:
    a wrong answer here silently drops real user facts, so it reads the shape of
    our own minted keys and never the content of a turn.

    TWO SHAPES, BECAUSE THE LANE VOCABULARY MOVED AND THIS DID NOT. The prefix
    form (``goal-``, ``incident-``) was the machine lane of 2026-08-25, when this
    shipped after "4,480 of 5,212 staged rows turned out to be the platform's own
    prompts". ``build_session_key`` has since minted a second machine lane —
    the RUNNER branch — and nothing told this function.

    MEASURED 2026-08-31: of 368 rows in ``staged_facts``, 178 came from a lane
    with no person on it — 99 ``owl:*:recovery:*``, 74 ``owl:*:objective:*``, and
    only 5 of the old prefixed form this check could actually see. Every one was
    filed as a durable fact ABOUT THE USER.
    """
    if not session_key:
        return False
    return session_key.startswith(MACHINE_LANE_PREFIXES) or _is_runner_lane(session_key)


#: How a lane key is named in an operator-facing report. ONE rule, here, because
#: sessions/models.py already owns what a lane key MEANS — and because writing the
#: same derivation ad hoc is how "incident" and "incident-" end up counted as two
#: different things in two different reports.
_HUMAN_FAMILY = "you (telegram)"


def lane_family(session_key: str | None) -> str:
    """The KIND of lane this key belongs to, for reporting.

    Deliberately coarse and deliberately named from the operator's point of view:
    the question a report answers is "what part of the platform spent this", and
    `owl:secretary:recovery:task-74e6b23` is not a useful answer while `recovery`
    is. A key it cannot read is named after its first token rather than dropped —
    a bucket called "other" quietly holding a large share would be worse than no
    report at all.
    """
    key = (session_key or "").strip()
    if not key:
        return "other"
    if key.isdigit():
        return _HUMAN_FAMILY  # a bare chat id is a person
    if key.startswith(_LANE_ROOT + _KEY_SEP):
        parts = key.split(_KEY_SEP)
        if len(parts) >= 4 and parts[3] in _CHAT_TYPE_VALUES:
            return _HUMAN_FAMILY
        return parts[2] if len(parts) > 2 else key
    if key.startswith("job:"):
        # The job NAME is the useful grain: "job:reflection_writer" tells the
        # operator which scheduled thing spent it, "job" tells them nothing. The
        # trailing -<id> is dropped so every run of one job counts as that job.
        name = key.split(":", 1)[1].rsplit("-", 1)[0]
        return f"job:{name}" if name else "job"
    return key.split("-", 1)[0] or "other"


def build_session_key(source: SessionSource, *, group_per_user: bool = True,
                      thread_per_user: bool = False) -> str:
    """Derive the lane name. Deterministic: same source, same key, forever (I1).

    Format::

        owl:{owl}:{channel}:{chat_type}[:{chat_id}][:{thread_id}][:{participant}]

    Isolation follows Bakir's Q5 answer, which matches the reference platform' defaults:

    * DMs are always private — the chat id already identifies one person.
    * Groups/channels are isolated PER USER by default (``group_per_user``), so
      your history is not visible to whoever asks next.
    * Threads are SHARED by default (``thread_per_user`` False), because a thread
      is a focused sub-discussion where shared context is the point.

    Components are dropped when absent rather than emitted empty, so a source that
    gains a thread id later produces a genuinely different lane instead of one that
    silently collides with the thread-less form.
    """
    # A RUNNER lane is keyed by what it is and which one — never by chat shape.
    # Isolation settings ask "whose messages are these", a question a cron job or an
    # objective does not have; letting them apply would silently reshape the key.
    # Deliberately stable across runs: a daily brief is ONE conversation that rolls,
    # not a new conversation every morning, and a per-run key would rebuild the
    # frozen prompt every time — losing the exact D01.1 win this divergence buys.
    if source.runner and source.runner_id:
        return _KEY_SEP.join(
            [_LANE_ROOT, source.owl_name, source.runner, source.runner_id]
        )

    parts: list[str] = [
        _LANE_ROOT, source.owl_name, source.channel, source.chat_type.value,
    ]
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
        conversation_id=new_conversation_id(now),
        owl_name=source.owl_name,
        channel=source.channel,
        created_at=now,
        updated_at=now,
        chat_id=source.chat_target,
        identity_key=source.identity_key,
        parent_session_key=source.parent_session_key,
    )


# Column order for the SQLite table and the JSON mirror. Declared once here so the
# store, the migration and the mirror cannot drift apart.
ENTRY_FIELDS: tuple[str, ...] = (
    "session_key", "conversation_id", "owl_name", "channel", "created_at", "updated_at",
    "message_count", "suspended", "resume_pending", "resume_reason", "was_auto_reset",
    "auto_reset_reason", "is_fresh_reset", "expiry_finalized", "restart_failures",
    "chat_id", "completed_turns", "identity_key", "summary_enqueued_for",
    "parent_session_key",
)
