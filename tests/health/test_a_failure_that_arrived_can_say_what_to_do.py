"""Every remedy sat on a branch that never fires; every branch that fires had none.

MEASURED 2026-09-10 across `src/`, and the two sets are DISJOINT:

    HealthStatus( construction sites            72
      reporting `down` or `degraded`            41
      of those, inside an `except`              10   <- a failure that ARRIVED
      of those, carrying a remedy                5
      INSIDE an except AND carrying a remedy     0

D14.4 added `HealthStatus.remedy` as "the other half of a diagnosis" and wired both
surfaces an operator reads. MEASURED over the same corpus: **822 sweeps reported
UNHEALTHY subsystems and every one carried `remedies: []`** — not one, ever.

WHY, and it is not carelessness. A remedy was written wherever the author could
PREDICT the failure: a database file that is not there, a browser runtime that was
never constructed. At an `except` there is no exception in hand at authoring time, so
there is genuinely nothing to write — and writing ten more strings by hand would put
them back in the same place. The only moment the question can be answered is when the
exception EXISTS. So the branches ask `remedy_for(exc)` there, and one function
decides.

THE INCIDENT THAT MAKES THIS CONCRETE. `DbContributor` has two `down` branches. The
file-missing branch carries a remedy and has never fired on a running install. The
ping-raised branch carries none — and it is the one that fired at
2026-09-07T14:32:02Z, the first minute of the four-hour total database failure in
which every write raised `sqlite3.OperationalError: disk I/O error`. The one moment
this platform most needed to tell its operator where to look, the field built for
saying so was empty by construction.

NONE IS STILL THE COMMON ANSWER, and D14.4's rule is not weakened — it is enforced in
one place instead of by ten authors' restraint. Nothing is returned unless the
exception carries the evidence itself: an errno, or one of SQLite's fixed operational
phrases.
"""

from __future__ import annotations

import ast
import errno
import sqlite3
from pathlib import Path

import pytest

from stackowl.health.status import HealthStatus, remedy_for

_SRC = Path(__file__).resolve().parents[2] / "src" / "stackowl"


# --------------------------------------------------------------------------- #
# 1. The function: evidence in, advice out — and silence when there is none
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_the_failure_that_took_the_platform_down_now_says_where_to_look() -> None:
    """The exact exception from 2026-09-07T14:32Z."""
    advice = remedy_for(sqlite3.OperationalError("disk I/O error"))
    assert advice, "the four-hour outage's own exception still carries no remedy"
    assert "dmesg" in advice or "space" in advice, (
        "the advice must name somewhere to LOOK — D14.4 invariant 4"
    )


@pytest.mark.tripwire
@pytest.mark.parametrize(
    "exc",
    [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("attempt to write a readonly database"),
        sqlite3.OperationalError("unable to open database file"),
        sqlite3.OperationalError("no such table: tasks"),
        OSError(errno.ENOSPC, "No space left on device"),
        OSError(errno.EROFS, "Read-only file system"),
        OSError(errno.EACCES, "Permission denied"),
        OSError(errno.EMFILE, "Too many open files"),
        TimeoutError(),
    ],
)
def test_an_arrived_failure_that_carries_evidence_carries_advice(
    exc: BaseException,
) -> None:
    assert remedy_for(exc), f"{exc!r} says what happened and nothing says what to do"


@pytest.mark.tripwire
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("no db here"),
        ValueError("bad value"),
        KeyError("missing"),
        sqlite3.OperationalError("some phrase nobody has a playbook for"),
        OSError("no errno at all"),
    ],
)
def test_silence_is_still_the_answer_when_nothing_is_known(exc: BaseException) -> None:
    """THE CONTROL, and it is the half that keeps the field worth reading.

    D14.4: a field that is always populated is a field readers learn to skim. If
    this ever starts answering for everything, the guard above stops meaning
    anything — so the two directions ship together.
    """
    assert remedy_for(exc) is None, (
        f"{exc!r} produced invented advice — nothing about it says what to do"
    )


@pytest.mark.tripwire
def test_the_loudest_alarm_in_the_corpus_now_says_it_is_self_recovering() -> None:
    """MEASURED 2026-09-10: of 959 unhealthy subsystem reports, **421** are
    `provider:NeraAiRaw`, and every probe failure behind them raises
    `CircuitOpenError`. That is not an outage anyone should act on — the breaker is
    doing its job and retries by itself. The exception already carried the retry
    window; nothing here invents anything.

    This is the case that decides whether the fix is worth having: a remedy that
    fires on nothing real is decoration, and this one lands on the single loudest
    unhealthy report the platform produces.
    """
    from stackowl.exceptions import CircuitOpenError

    advice = remedy_for(CircuitOpenError("NeraAiRaw", 240.0))
    assert advice, "the commonest unhealthy report in the corpus still says nothing"
    assert "240" in advice, "the retry window is on the exception and is not passed on"
    assert "nothing to do" in advice, (
        "a self-recovering breaker must be reported as such, or an operator is sent "
        "to fix something that is already fixing itself"
    )


@pytest.mark.tripwire
def test_a_double_timeout_says_the_host_is_a_suspect() -> None:
    """A timeout is the one `down` with no exception behind it, and the one most
    likely to be about the BOX rather than the subsystem. MEASURED on this install:
    four contributors were declared down at 2026-09-10T01:37Z while a full test suite
    was running on the same machine."""
    advice = remedy_for(TimeoutError())
    assert advice and "load" in advice, (
        "a non-answer is still reported as an outage with nothing telling a reader "
        "to check the host first"
    )


# --------------------------------------------------------------------------- #
# 2. The structural rule — every arrived failure must ASK
# --------------------------------------------------------------------------- #


def _unhealthy_sites_in_except_blocks() -> list[tuple[str, int, bool]]:
    """(file, line, asks_remedy_for) for every unhealthy HealthStatus in an except."""
    found: list[tuple[str, int, bool]] = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover — the syntax gate catches these first
            continue
        handler_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                handler_lines.update(
                    sub.lineno for sub in ast.walk(node) if hasattr(sub, "lineno")
                )
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "HealthStatus"):
                continue
            if node.lineno not in handler_lines:
                continue
            kwargs = {k.arg: k.value for k in node.keywords}
            status = kwargs.get("status")
            if not (isinstance(status, ast.Constant)
                    and status.value in ("down", "degraded")):
                continue
            remedy = kwargs.get("remedy")
            asks = (
                isinstance(remedy, ast.Call)
                and getattr(remedy.func, "id", "") == "remedy_for"
            )
            found.append((str(path.relative_to(_SRC)), node.lineno, asks))
    return found


@pytest.mark.tripwire
def test_every_arrived_failure_asks_the_one_place_that_decides() -> None:
    """STRUCTURAL, deliberately — the rule is "ask", never "answer".

    A per-site judgement is what produced 0 of 10. Requiring the CALL rather than a
    non-empty string means a new contributor inherits whatever is known today and
    gains whatever is learned tomorrow, without its author having to think about it —
    and `remedy_for` returning None keeps D14.4's rule intact at the one place that
    can enforce it.
    """
    sites = _unhealthy_sites_in_except_blocks()
    assert sites, "the scan found nothing — the walk is broken, not the tree"
    silent = [(f, ln) for f, ln, asks in sites if not asks]
    assert not silent, (
        f"{len(silent)} of {len(sites)} unhealthy statuses built from an arrived "
        f"exception do not pass it to `remedy_for` — so whatever the exception "
        f"knows, the operator will not be told: {silent}"
    )


@pytest.mark.tripwire
def test_the_scan_can_actually_fail() -> None:
    """VACUITY CONTROL. The guard above passes trivially if the walk finds no
    `except`-nested sites, or if `HealthStatus` is renamed and the name match goes
    dead. This pins the population it is measuring."""
    sites = _unhealthy_sites_in_except_blocks()
    assert len(sites) >= 10, (
        f"only {len(sites)} arrived-failure sites found; the walk measured 10 on "
        "2026-09-10 and it can only be this low if the scan stopped working"
    )
    source = ast.parse(
        (_SRC / "health" / "aggregator.py").read_text(encoding="utf-8")
    )
    assert any(
        isinstance(n, ast.Call) and getattr(n.func, "id", "") == "remedy_for"
        for n in ast.walk(source)
    ), "the aggregator stopped asking — the scan would then be checking nobody"


# --------------------------------------------------------------------------- #
# 3. End to end — the contributor that fired during the outage
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
@pytest.mark.asyncio
async def test_the_db_contributor_carries_the_advice_out_of_the_except(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not the function in isolation — the branch that reported the real outage."""
    from stackowl.health import contributors

    db_path = tmp_path / "stackowl.db"
    db_path.write_text("")  # exists, so the predictable branch is NOT the one taken

    def _boom(_p: object) -> object:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(contributors.sqlite3, "connect", _boom)
    status: HealthStatus = await contributors.DbContributor(db_path).health_check()

    assert status.status == "down"
    assert status.remedy, (
        "the ping-raised branch is still silent — this is the branch that fired "
        "during the four-hour outage, not the file-missing one"
    )
