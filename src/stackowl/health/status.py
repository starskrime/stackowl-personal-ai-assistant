"""HealthStatus dataclass and HealthContributor protocol."""

from __future__ import annotations

import errno
import sqlite3
from dataclasses import dataclass
from typing import Literal, Protocol

from stackowl.exceptions import CircuitOpenError

#: THE SUBSYSTEM VOCABULARY, AND IT HAD FIVE COPIES AND NO NAME.
#:
#: MEASURED 2026-09-12: `Literal["ok", "degraded", "down"]` was written out
#: independently in `health/status.py`, `memory/bridge.py`, `webhooks/receiver.py`,
#: `notifications/router.py` and `startup/provider_probe.py`. The `HealthStatus`
#: DATACLASS is imported by a dozen modules; the vocabulary INSIDE it was not
#: importable at all, so every new reporter re-typed it.
#:
#: That is why this platform could not say "I could not measure it". Adding a state
#: was never a design problem — it was a five-file edit that the next reporter would
#: have made six, which is the two-copies-of-one-rule shape this tree finds more
#: often than any other. Naming it is what makes the vocabulary extensible.
#:
#: `unknown` MEANS NOTHING MEASURED IT — never that the subsystem is fine and never
#: that it is dead. It is deliberately NARROW: `aggregator._corroborate_non_answers`
#: only lets a non-answer keep this word when something ELSE also failed to answer
#: the same sweep, because MEASURED over every retained log, 24 of 40 double
#: timeouts fired while fourteen other contributors answered inside the same window.
#: A lone non-answer is evidence about the subsystem and stays `down`.
HealthState = Literal["ok", "degraded", "down", "unknown"]

#: AND THE VOCABULARY ALONE IS NOT ENOUGH — EVERY READER STILL HAS TO CLASSIFY IT.
#:
#: A state that no consumer matches does not raise; it falls through whatever branch
#: happens to be last, silently. MEASURED on the tree as it stood: of the four places
#: that read a `HealthStatus.status`, adding a fourth word would have given
#: `health_sweep` a subsystem that is neither `down` nor `degraded` and therefore
#: counted HEALTHY, and the CLI an `✗` for something nothing had measured. Neither
#: would have failed a test. That is the same defect as the five copies, one level up:
#: the PARTITION was implicit, so extending it was a silent edit.
#:
#: So the three groups are declared here beside the vocabulary, they must cover it
#: exactly, and `test_the_health_vocabulary_has_ONE_home` fails on any state that is
#: in none of them or in two. A new state cannot be added without deciding, in
#: writing, whether it kills the process.
HEALTHY_STATES: tuple[HealthState, ...] = ("ok",)

#: Worth telling an operator about; NOT worth killing the process over.
WARNING_STATES: tuple[HealthState, ...] = ("degraded", "unknown")

#: The only states that trip liveness. `unknown` is deliberately NOT here, and the
#: reason is the corroboration rule rather than a guess about causes: by the time a
#: status reaches this word, SEVERAL contributors have failed to answer the same
#: sweep, which is the shape of a loaded host — exactly the load under which a
#: restart loop does the most damage. A lone non-answer never gets here; it is
#: promoted to `down` and still kills the process, as it always did.
LIVENESS_FAILING_STATES: tuple[HealthState, ...] = ("down",)


@dataclass(frozen=True)
class HealthStatus:
    name: str
    status: HealthState

    message: str | None
    latency_ms: float
    #: WHAT TO DO ABOUT IT — the other half of a diagnosis (D14.4).
    #:
    #: `message` says what is wrong; this says what the operator can run or check. Until
    #: 2026-09-06 there was no field for it, so a contributor that KNEW the fix had
    #: nowhere to put it, and both surfaces an operator reads — `stackowl health` and the
    #: 2am alert — rendered "database not found: /path" and stopped, with the CLI then
    #: exiting 1.
    #:
    #: **None is a real answer and the common one.** It means no operator action is
    #: known, which is true of every measurement-style contributor: "the prompt prefix is
    #: growing" has no command that fixes it. Filling those in would be inventing advice,
    #: and a field that is always populated is a field readers learn to skim — the value
    #: is precisely that a remedy appearing means there IS something to do.
    #:
    #: Defaulted so all 72 existing construction sites are unaffected.
    remedy: str | None = None


class HealthContributor(Protocol):
    """Any subsystem that can report its own health."""

    @property
    def contributor_name(self) -> str: ...

    async def health_check(self) -> HealthStatus: ...


#: Errno values whose meaning IS the operator's next step. Numeric, so this is not a
#: word list in any language — `errno` is a POSIX constant table, and the message the
#: OS renders beside it is irrelevant here.
_ERRNO_REMEDIES: dict[int, str] = {
    errno.ENOSPC: (
        "the volume is FULL — free space where the platform's data lives "
        "(`df -h $STACKOWL_HOME`); nothing here can recover on its own"
    ),
    errno.EROFS: (
        "the filesystem is mounted READ-ONLY — check `mount` for the volume holding "
        "$STACKOWL_HOME; a kernel remount after an I/O error looks exactly like this"
    ),
    errno.EACCES: (
        "permission denied — check the owner and mode of the path in the message "
        "against the user this process runs as"
    ),
    errno.EIO: (
        "a hardware-level I/O error — check `dmesg` and the filesystem on the volume "
        "holding $STACKOWL_HOME before restarting anything"
    ),
    errno.EMFILE: (
        "this process is out of file descriptors — check `ulimit -n` and look for a "
        "handle leak in the subsystem named above"
    ),
}

#: SQLite reports its operational failures as a short, fixed English phrase — a
#: LIBRARY API contract, not natural language, exactly as this tree already records
#: for SQL keywords in `tools/system/shell.py`. Matched on the phrase because
#: `sqlite3.OperationalError` carries no code on the exception itself.
_SQLITE_REMEDIES: tuple[tuple[str, str], ...] = (
    ("disk i/o error", (
        "SQLite could not read or write the file at the OS level — check `dmesg` and "
        "free space on the volume holding the database; the platform cannot repair "
        "this itself and will keep failing every write until it is fixed"
    )),
    ("database is locked", (
        "another writer is holding the database — check for a second stackowl process "
        "(`pgrep -af stackowl`) before assuming this one is at fault"
    )),
    ("readonly database", (
        "the database file is not writable — check its mode and owner, and whether the "
        "filesystem was remounted read-only"
    )),
    ("unable to open database file", (
        "the path exists in config but not on disk, or its directory is not readable — "
        "check STACKOWL_HOME points at the install you mean"
    )),
    ("no such table", (
        "the schema is behind the code — run the migrations for this install"
    )),
)


def remedy_for(exc: BaseException) -> str | None:
    """What the operator should do about a failure that ACTUALLY ARRIVED, or None.

    WHY THIS EXISTS, and it is a number rather than a preference. MEASURED
    2026-09-10 across `src/`: of 72 ``HealthStatus(...)`` construction sites, 41
    report ``down`` or ``degraded``, 10 of those sit inside an ``except`` — a real
    failure that arrived — and 5 carry a :attr:`HealthStatus.remedy`. **The two sets
    are DISJOINT: zero of the ten.** Every remedy in this tree sits on a branch the
    author could PREDICT (a missing file, a runtime not constructed); every branch
    that turns an arrived failure into a status carries none.

    That is not carelessness, and writing ten more strings by hand would not fix it.
    At the keyboard the ``except`` branch has no exception in hand, so there is
    genuinely nothing to write. The only moment the question can be answered is when
    the exception EXISTS — so the branches ask here instead, and this is the one
    place that decides.

    **None is still a real answer, and stays the common one.** D14.4's rule — a
    remedy that is always populated is a field readers learn to skim, and inventing
    advice is worse than silence — is enforced HERE rather than by ten authors'
    restraint. Nothing is returned unless the exception itself carries the evidence:
    an errno, or one of SQLite's fixed operational phrases.

    WHAT THIS DOES NOT DO, stated so it is not read as a contradiction. D14.4
    records that ``prefix_growth``, ``unattributed_spend`` and ``store_cadence``
    deliberately have no remedy, because a measurement that has DEGRADED has no
    command that fixes it. That stands. Those three contributors also have a second,
    different state — "I could not measure it, because ``exc``" — which their own
    comments already separate from the first in the MESSAGE ("could not measure it
    is not it has regressed") and which then received the same empty remedy. One
    contributor, two kinds of failure, one policy. This addresses only the second.
    """
    # THE LOUDEST CASE IN THE WHOLE CORPUS, and it needed no new vocabulary — the
    # exception already carried the answer. MEASURED 2026-09-10: of 959 unhealthy
    # subsystem reports, 421 are `provider:NeraAiRaw`, and every probe failure behind
    # them raises `CircuitOpenError: Circuit open for 'X' — retry after Ns`. That is
    # not an outage anyone should act on: the breaker is DOING ITS JOB and will retry
    # by itself. Saying so turns the platform's most repeated alarm from "something is
    # wrong" into "known, self-recovering, and here is when".
    if isinstance(exc, CircuitOpenError):
        return (
            f"the breaker for '{exc.provider_name}' is open and will retry on its own "
            f"in ~{exc.retry_after_seconds:.0f}s — nothing to do unless it never "
            f"closes; check that the provider host in config is reachable from here"
        )
    if isinstance(exc, TimeoutError):
        return (
            "it did not answer twice — check the load on this host before treating "
            "this as an outage; a busy box and a dead subsystem look the same here"
        )
    if isinstance(exc, sqlite3.OperationalError):
        text = str(exc).lower()
        for phrase, advice in _SQLITE_REMEDIES:
            if phrase in text:
                return advice
    number = getattr(exc, "errno", None)
    if isinstance(number, int):
        return _ERRNO_REMEDIES.get(number)
    return None
