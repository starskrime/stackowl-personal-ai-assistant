"""An RCA killed mid-flight must not look like one that never ran.

MEASURED 2026-09-01 over the retained logs:

* ``[rca] staged.analyze: entry`` **462** times against ``exit`` **355** — 107
  analyses (23%) never finished;
* **85 of those 107 had a RESTART as their next event**;
* **232 boots in four days**, one every ~25 minutes, because CodeWatcher
  exec-replaces the core on every commit.

A staged RCA budgets up to 960 seconds. Work that takes sixteen minutes cannot
survive a process replaced every twenty-five, and roughly one in five never did.
That is not an edge case — a self-updating platform restarting constantly is the
NORMAL condition here, and the analysis was built as though it were not.

NOTHING RECORDED IT, WHICH IS THE ACTUAL DEFECT. ``record_diagnosis`` is written
on COMPLETION only. An interrupted analysis therefore left the signature looking
"never diagnosed": the next tick started the identical analysis from zero, spent
another ~140,000 tokens, and was interrupted again. 85 x 140k is roughly 12
million tokens that produced no verdict, no record, and no way to notice — the
platform could not tell "we tried and were killed" from "we never tried".

THE START MARKER SUPPRESSES, AND THAT IS THE POINT rather than a side effect.
Four days of evidence say an analysis interrupted once will be interrupted
again; re-entering it every ten minutes is the furnace. The 24h expiry still
applies so a genuinely-needed analysis is retried tomorrow, and the incident
itself is still DETECTED every tick — only the expensive re-analysis is held.

WHAT THE SAME CAUSE REACHES. Any long-running background work on this platform:
it has no durable "I started" anywhere, so a restart is indistinguishable from
never having begun. The RCA is where it was measurable because it is the
expensive one.

NOT FIXED HERE: making the analysis itself RESUMABLE. That is the larger answer
and it is an architecture question, not a fourth attempt at bookkeeping. What
ships is the measurement that makes it a decision rather than a guess —
``interrupted_diagnoses`` now counts exactly how much work is being lost.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.incident_escalation import (
    DIAGNOSED_EVENT,
    STARTED_EVENT,
    record_diagnosis_abandoned,
    interrupted_diagnoses,
    recently_diagnosed,
    record_diagnosis,
    record_diagnosis_started,
)
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio

_NOW = 1_788_000_000.0
_SIG = "outcome:web_fetch:stop"


@pytest.fixture
async def pool(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "ledger.db"
    db = DbPool(db_path=path)
    await db.open()
    seed_schema(path)
    yield db
    await db.close()


async def test_a_started_analysis_suppresses_a_repeat(pool) -> None:  # noqa: ANN001
    """The furnace, stopped. Before this, an interrupted RCA left no row and the
    next tick spent another ~140,000 tokens on the identical analysis."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    assert _SIG in await recently_diagnosed(pool, now=_NOW + 60.0)


async def test_a_completed_analysis_still_suppresses(pool) -> None:  # noqa: ANN001
    """The behaviour that already existed must not regress."""
    await record_diagnosis(
        pool, signature=_SIG, incident_id="incident-a", verified=False, now=_NOW,
    )
    assert _SIG in await recently_diagnosed(pool, now=_NOW + 60.0)


async def test_suppression_still_expires(pool) -> None:  # noqa: ANN001
    """A start marker must not become a permanent ban — a problem still present
    tomorrow deserves a fresh look, and self-healing that never retries is the
    failure mode this whole arc exists to prevent."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    assert _SIG not in await recently_diagnosed(pool, now=_NOW + 25 * 3600.0)


# --------------------------------------------------------------------------- #
# The measurement that was impossible before                                   #
# --------------------------------------------------------------------------- #


async def test_a_start_with_no_finish_is_counted_as_interrupted(pool) -> None:  # noqa: ANN001
    """The 85-of-462 case, which previously left no trace at all."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 60.0) == [_SIG]


async def test_a_completed_analysis_is_not_interrupted(pool) -> None:  # noqa: ANN001
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    await record_diagnosis(
        pool, signature=_SIG, incident_id="incident-a", verified=True, now=_NOW + 10.0,
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 60.0) == []


async def test_a_completion_only_counts_if_it_came_AFTER_the_start(pool) -> None:  # noqa: ANN001
    """Yesterday's success must not mask today's interruption, or the count
    silently under-reports exactly when the platform is worst."""
    await record_diagnosis(
        pool, signature=_SIG, incident_id="incident-old", verified=True, now=_NOW,
    )
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-new", now=_NOW + 100.0,
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 200.0) == [_SIG]


async def test_an_old_interruption_falls_out_of_the_window(pool) -> None:  # noqa: ANN001
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 25 * 3600.0) == []


async def test_a_broken_ledger_read_says_it_cannot_tell(pool, monkeypatch) -> None:  # noqa: ANN001
    """Empty must mean "cannot tell", never "none" — a metric that reports zero
    on a failed query is how a regression gets called a fix."""

    async def _boom(*a: object, **k: object) -> list[dict]:
        raise RuntimeError("no such table")

    monkeypatch.setattr(pool, "fetch_all", _boom)
    assert await interrupted_diagnoses(pool, now=_NOW) == []
    assert await recently_diagnosed(pool, now=_NOW) == set()


async def test_a_failed_start_write_never_costs_the_tick(pool, monkeypatch) -> None:  # noqa: ANN001
    """Bookkeeping may not fail a sweep — the RCA must still run."""

    async def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(pool, "execute", _boom)
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )  # must not raise


def test_the_start_is_recorded_BEFORE_the_analysis_runs() -> None:
    """Structural, and the whole item in one assertion: recording it afterwards
    is precisely the bug — a process replaced during those sixteen minutes
    leaves nothing behind."""
    import inspect

    from stackowl.scheduler.handlers import incident_escalation

    source = inspect.getsource(incident_escalation.IncidentEscalationHandler.execute)
    assert "record_diagnosis_started(" in source, (
        "the start marker is gone — an interrupted analysis is invisible again"
    )
    assert source.index("record_diagnosis_started(") < source.index(
        "await self._resolve_incident("
    ), "the marker must be written BEFORE the analysis, or it cannot survive it"


def test_the_loss_metric_is_not_gated_on_there_being_NEW_work() -> None:
    """THE MEASUREMENT WENT BLIND EXACTLY WHEN IT MATTERED.

    ``interrupted_diagnoses`` exists to answer "how much analysis work are we
    losing", and the call sat behind ``if self._db is not None and
    new_incidents:``. Counting the gate's ACTUAL input — ticks that had incidents
    before suppression — 403 of 618 ticks had it OPEN and ~215 (35%) had it
    CLOSED. Those 215 are the QUIETEST ticks: no active incidents to detect, so
    nothing else would surface an unfinished analysis either. A loss counter that
    only runs while new work arrives is blind exactly when the losses sit.

    TWO NUMBERS IN AN EARLIER VERSION OF THIS DOCSTRING WERE WRONG, and are kept
    here because the mistakes are the more useful lesson. "The line had NEVER
    appeared" — it had appeared 250 times; the control grepped for the word
    "interrupted", which the message does not contain. And "86% of ticks had
    new == 0" — the exit line logs ``new`` AFTER suppression reassigns
    ``new_incidents``, so that field is not the gate's input. One field, two
    meanings, read the wrong way.

    Asserted structurally rather than by string match: the guard EXPRESSION must
    not mention ``new_incidents``. A future edit that reintroduces the coupling
    fails here rather than going silent for another 7,000 log lines.
    """
    import ast
    import inspect
    import textwrap

    from stackowl.scheduler.handlers import incident_escalation

    src = textwrap.dedent(
        inspect.getsource(incident_escalation.IncidentEscalationHandler.execute)
    )
    tree = ast.parse(src)

    def _mentions(node: ast.AST, name: str) -> bool:
        return any(
            isinstance(n, ast.Name) and n.id == name for n in ast.walk(node)
        )

    guarded_by_new_work = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "interrupted_diagnoses"
            for c in ast.walk(node)
        )
        and _mentions(node.test, "new_incidents")
    ]
    assert not guarded_by_new_work, (
        "the interrupted-analysis metric is gated on new_incidents again — it "
        "will go silent on the 86% of ticks that have no new work, which is "
        "exactly when unfinished analyses accumulate unnoticed"
    )


# --------------------------------------------------------------------------- #
# An analysis that RETURNED without a verdict is not an interrupted one        #
# --------------------------------------------------------------------------- #


async def test_a_failed_analysis_does_not_buy_24h_of_suppression(pool) -> None:  # noqa: ANN001
    """TWO MECHANISMS WITH OPPOSITE INTENTIONS, and the later one won silently.

    ``incident_escalation`` deliberately does NOT call ``record_diagnosis`` when
    the RCA returns no verdict, and says why: "so the NEXT tick retries the RCA
    for this same persistent incident instead of silently giving up on it forever
    after one failed attempt — a provider outage during the incident is precisely
    when the RCA call itself is most likely to also fail."

    But ``record_diagnosis_started`` is written BEFORE the attempt, and
    ``recently_diagnosed`` counts STARTED events as well as finished ones. So the
    signature that branch deliberately leaves unregistered is suppressed for 24
    hours regardless, and the stated intent never takes effect.

    MEASURED 2026-09-03 during a seven-hour provider outage: at 16:53 the loop
    started an RCA for the signature ``health:provider:NeraAiRaw:down`` — using
    the model to investigate why the model was unreachable. It could not succeed,
    left a started row with no completion, and burned that signature's re-analysis
    window. ``interrupted_diagnoses`` then listed THREE variants of the same
    outage.

    THE DISCRIMINATOR IS EXACT, and needs no provider registry. The started
    marker exists to survive an analysis that NEVER RETURNS — a process
    replacement mid-flight, which consumed ~140,000 tokens and must not be
    repeated every ten minutes. If the code reaches the verdict check at all, the
    attempt RETURNED: it was not interrupted, and it consumed nothing worth
    protecting. Clearing the marker there restores the retry the other branch
    already intended."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-x", now=_NOW,
    )
    assert await recently_diagnosed(pool, now=_NOW + 60.0) == {_SIG}

    await record_diagnosis_abandoned(pool, signature=_SIG, incident_id="incident-x")

    assert await recently_diagnosed(pool, now=_NOW + 60.0) == set(), (
        "a returned-but-verdictless analysis still suppresses the signature for "
        "24h, defeating the retry the verdict-is-None branch exists to allow"
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 60.0) == [], (
        "it is also still counted as work lost mid-flight, which it was not"
    )


async def test_clearing_only_removes_THIS_attempt(pool) -> None:  # noqa: ANN001
    """A different incident's start for the same signature must survive — two
    analyses can be in flight across a restart boundary, and clearing the wrong
    one would re-open a signature that IS legitimately suppressed."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-b", now=_NOW + 1.0,
    )
    await record_diagnosis_abandoned(pool, signature=_SIG, incident_id="incident-a")
    assert await recently_diagnosed(pool, now=_NOW + 60.0) == {_SIG}


async def test_a_completed_analysis_is_untouched_by_clearing(pool) -> None:  # noqa: ANN001
    """Clearing the start must never remove the RESULT. A verdict that shipped is
    the thing the 24h window is legitimately for."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-c", now=_NOW,
    )
    await record_diagnosis(
        pool, signature=_SIG, incident_id="incident-c", verified=True, now=_NOW + 5.0,
    )
    await record_diagnosis_abandoned(pool, signature=_SIG, incident_id="incident-c")
    assert await recently_diagnosed(pool, now=_NOW + 60.0) == {_SIG}


async def test_clearing_a_row_that_is_not_there_is_harmless(pool) -> None:  # noqa: ANN001
    """Never raises: a ledger write may not cost a sweep, and the caller runs
    this on a failure path where something has already gone wrong."""
    await record_diagnosis_abandoned(pool, signature="nope", incident_id="nope")


def test_the_abandonment_is_recorded_on_the_verdictless_PATH() -> None:
    """THE WIRING, which the helper tests above cannot see.

    Caught by mutation: disabling the call in ``execute`` left all fifteen other
    tests green, because they drive ``record_diagnosis_abandoned`` directly and
    never through the handler. A helper that works and is never called is the
    shape this codebase keeps paying for.

    Asserted structurally: the call must sit inside a branch guarded on the
    verdict being None. A call moved outside that guard would abandon analyses
    that DID produce a verdict, re-opening signatures the window legitimately
    suppresses."""
    import ast
    import inspect
    import textwrap

    from stackowl.scheduler.handlers import incident_escalation

    src = textwrap.dedent(
        inspect.getsource(incident_escalation.IncidentEscalationHandler.execute)
    )
    tree = ast.parse(src)

    guarded = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "record_diagnosis_abandoned"
            for c in ast.walk(node)
        )
        and "verdict" in ast.dump(node.test)
    ]
    assert guarded, (
        "record_diagnosis_abandoned is not called from a verdict-guarded branch "
        "in execute() — a returned-but-verdictless analysis will suppress its "
        "signature for the full 24h window again"
    )
