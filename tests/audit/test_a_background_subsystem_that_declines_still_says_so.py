"""A background subsystem that declines to act must still leave a record.

WHAT THIS COST, AND WHY A TEST RATHER THAN A THIRD COPY OF THE RULE. `CLAUDE.md`
says *"Production runs at INFO. A `log.*.debug` line does not exist when you need
it"*, and `item-loop/SKILL.md` says it again. The rule was written after D08.1's
fourth acceptance check sat open for days on DEBUG-only evidence. **Two copies,
zero enforcement** — which is this repo's own named shape, "two copies of one
rule; one source, have the other ask it" — so it recurred.

MEASURED 2026-09-09 across every retained log: 652,309 INFO, 13,547 WARNING,
11,250 ERROR, 2 CRITICAL, **0 DEBUG**. A DEBUG line is not a faint signal here.
It is no signal.

HOW IT RECURRED. `route_rca_verdict` — the consumer of the self-healing loop's
RCA verdicts — logged its entry and its `not verdict.verified` decline at DEBUG
and only its consumption at INFO. Its last INFO line is 2026-09-03;
`incident_escalation: RCA complete` has fired 41 times since. A whole session
could not answer whether self-healing was DEAD or merely DECLINING, because both
write nothing. The 20 `tool_build.execute: no channel/session to scope consent`
refusals downstream of it were only ever visible because *that* line is ERROR.

AND THE SAME LEVEL HID THE PLATFORM'S OWN CONFESSIONS. Nine sites said "no db
wired", "no skill curator wired", "no embedding registry", "flag off — noop" —
the built-but-not-wired family `CLAUDE.md` calls the shape that accounts for
nearly every real defect here — at a level nobody can read. They are defensive
`| None = None` defaults, so in a correctly assembled deployment they fire ZERO
times: promoting them costs nothing in health and speaks exactly when something
is missing.

SCOPED TO BACKGROUND PACKAGES ON PURPOSE. A per-turn tool that returns quietly is
still observed — its turn has a user, a reply and a cost record. A scheduler tick
has none of those, so the log is the only witness there is.
"""

from __future__ import annotations

import importlib.util
import logging
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_report():
    spec = importlib.util.spec_from_file_location(
        "logging_visibility", _ROOT / "scripts" / "logging_visibility.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["logging_visibility"] = mod
    spec.loader.exec_module(mod)
    return mod


#: The background functions whose quiet return is DELIBERATE, each with the reason.
#:
#: THIS LIST MAY ONLY SHRINK. The test below fails on a key that is NOT here (a new
#: blind spot) AND on a key here that no longer appears (a stale entry) — because an
#: allowlist nobody prunes is how a document goes on advertising a flag that was
#: deleted eight days earlier.
_ACCEPTED: dict[str, str] = {
    # --- webhooks, added with the package on 2026-09-11 (DEBT-303) ---
    # ALL FOUR ARE THE SAME SHAPE, AND NONE IS THE INVERSION THIS GATE HUNTS: the
    # function's OUTCOME is already at INFO, and the DEBUG line is the 4-point exit
    # marker sitting beside it. Checked one at a time rather than waved through,
    # because an exemption written to make a gate pass is worth less than no gate.
    "src/stackowl/webhooks/receiver.py::_parse_and_enqueue":
        "the outcome is INFO at 'event enqueued', one line above this exit marker",
    "src/stackowl/webhooks/receiver.py::_handle_request":
        "acceptance is recorded by the callee's INFO 'event enqueued'; the loud "
        "returns here are the rejections, which is the right way round",
    "src/stackowl/webhooks/handler_job.py::execute":
        "the outcome is INFO at 'event processed (stub)', immediately above",
    "src/stackowl/webhooks/receiver_helpers.py::resolve_source_secret":
        "INVERTED RELATIVE TO THE GATE'S PREMISE, not a blind spot: the loud return "
        "is the FAILURE (WARNING 'resolution failed') and the quiet one is success. "
        "The gate cannot tell which return is the decline, so it flags this; a "
        "reader is told when the secret cannot be resolved, which is what matters",
    # Called once per job dispatch — INFO here would be per-tick noise, and the
    # branch it guards is arithmetic, not a decision about whether to act.
    "src/stackowl/scheduler/scheduler_helpers.py::compute_next_run":
        "per-dispatch arithmetic; the loud path already reports the outcome",
    # Boot-time idempotent seeding. 'already present' is the normal case on every
    # boot after the first, so it is a no-op confirmation rather than a decline.
    "src/stackowl/scheduler/assembly.py::_seed_daily_schedule":
        "idempotent boot seed; 'already present' is the steady state",
    "src/stackowl/scheduler/assembly.py::_seed_minutes_schedule":
        "idempotent boot seed; 'already present' is the steady state",
    "src/stackowl/scheduler/assembly.py::_maybe_notify_unset_timezone":
        "fires per tick once notified; the notification itself is the record",
    "src/stackowl/scheduler/assembly.py::_check_and_notify":
        "fires per tick once notified; the notification itself is the record",
    # A healthy sweep finding nothing is the steady state, every tick.
    "src/stackowl/scheduler/handlers/task_liveness_sweep.py::execute":
        "'no stale tasks' is the healthy per-tick steady state",
    # An empty answer is already accounted for by the caller's own result record.
    "src/stackowl/scheduler/handlers/goal_execution.py::_deliver_answer":
        "empty answer; the job's result row carries the outcome",
    # Per-notification success paths — the router's own 'delivered' line is INFO
    # and is the single record for a delivery.
    "src/stackowl/notifications/deliverer.py::_transport":
        "router.deliver: delivered is the INFO record for this send",
    "src/stackowl/notifications/deliverer.py::_transport_file":
        "router.deliver: delivered is the INFO record for this send",
    "src/stackowl/notifications/router.py::health":
        "polled health probe; the caller records the verdict",
    "src/stackowl/notifications/undelivered_outbox.py::list_pending":
        "a read, not a decision to act",
    # Read/aggregate helpers inside a session that logs its own outcome.
    "src/stackowl/learning/failure_outcome_miner.py::reconcile_ownership":
        "sweep exit; the loud paths carry every actual reconciliation",
    "src/stackowl/learning/failure_outcome_miner.py::adopt_legacy_siblings":
        "sweep exit; the loud paths carry every actual adoption",
    # Its "no provider registry wired" leg is now WARNING; these two are routine
    # short-circuits, and the degraded/failed legs are already WARNING and ERROR.
    "src/stackowl/objectives/driver.py::_synthesize_completion":
        "routine synthesis short-circuits; degraded and failed legs are loud",
    "src/stackowl/parliament/convergence.py::check":
        "per-round predicate; the session logs the convergence decision",
    "src/stackowl/parliament/orchestrator.py::_resolve_interjection_target_locked":
        "per-interjection lookup inside a session that logs its outcome",
    "src/stackowl/parliament/orchestrator.py::_finalize_session":
        "the session's own finalize record is INFO",
    "src/stackowl/parliament/positions_synthesis.py::complete_synthesis_with_retry":
        "retry helper; the caller logs the synthesis outcome",
    "src/stackowl/parliament/round_runner.py::_run_owl":
        "per-owl round step; the round logs its own outcome",
}


@pytest.mark.tripwire
def test_no_background_subsystem_declines_without_a_record() -> None:
    """The ratchet. A NEW asymmetric function fails here, by name."""
    report = _load_report()
    found = {f.key for f in report.background_findings()}
    new = sorted(found - set(_ACCEPTED))
    assert not new, (
        "these background functions log loudly on one return path and only at "
        "DEBUG on another, so their decline leaves no production record:\n  "
        + "\n  ".join(new)
        + "\n\nProduction writes no DEBUG at all. Either raise the quiet branch to "
        "a level production emits, or add the key to _ACCEPTED with the reason it "
        "is genuinely not an outcome."
    )


@pytest.mark.tripwire
def test_the_allowlist_has_no_stale_entries() -> None:
    """An allowlist nobody prunes rots into a claim about code that has moved on."""
    report = _load_report()
    found = {f.key for f in report.background_findings()}
    stale = sorted(set(_ACCEPTED) - found)
    assert not stale, (
        "these keys are exempted but no longer asymmetric — the exemption is now a "
        "false statement about the tree, so delete it:\n  " + "\n  ".join(stale)
    )


def test_the_detector_can_actually_fail() -> None:
    """VACUITY CONTROL. Both assertions above pass trivially if the scanner finds
    nothing — which is exactly what a broken AST walk looks like. Give it a tree
    that unambiguously contains the shape and require it to be named."""
    report = _load_report()
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "src" / "stackowl" / "scheduler"
        root.mkdir(parents=True)
        (root / "fake_handler.py").write_text(
            "from stackowl.infra.observability import log\n"
            "def decide(x):\n"
            "    if x:\n"
            "        log.scheduler.info('acted')\n"
            "        return 1\n"
            "    log.scheduler.debug('declined — nobody will ever read this')\n"
            "    return 0\n",
            encoding="utf-8",
        )
        found = report.scan_tree(pathlib.Path(td) / "src" / "stackowl", ("scheduler",))
    keys = {f.key for f in found}
    assert "src/stackowl/scheduler/fake_handler.py::decide" in keys, (
        "the scanner cannot see the shape it exists to find — the ratchet above is "
        f"vacuous. saw: {keys}"
    )


class _Capture(logging.Handler):
    """Records straight off the named logger.

    NOT `caplog`, deliberately. `configure_logging` sets `propagate = False` on the
    `stackowl` logger, and caplog reads through the ROOT logger — so this test would
    pass alone and fail in any session where something had configured logging first.
    That is precisely the cross-test-pollution shape only the full suite detects, and
    a guard about observability must not itself depend on a global nobody controls.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.mark.tripwire
async def test_a_declined_rca_verdict_says_so_at_a_level_production_writes() -> None:
    """THE INSTANCE THAT MOTIVATED ALL OF THIS, driven rather than read.

    An unverified verdict is the self-healing router's COMMONEST outcome. Asserting
    on `inspect.getsource` would pass against a function that never runs; this calls
    the real one and requires a record production would actually write.
    """
    from stackowl.learning.failure_outcome_miner import RcaVerdict
    from stackowl.scheduler.handlers.rca_verdict_router import route_rca_verdict

    verdict = RcaVerdict(
        capability_class="web_knowledge",
        failure_class="timeout",
        skill_name="a_declined_verdict",
        description="d",
        when_to_use="w",
        root_cause="r",
        fix_pattern="f",
        verified=False,
    )
    logger = logging.getLogger("stackowl.scheduler")
    handler = _Capture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        await route_rca_verdict(verdict, "fix")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    records = [r for r in handler.records if "no consumption" in r.getMessage()]
    assert records, (
        "the router declined and said nothing recognisable at all — expected a "
        f"'no consumption' record. saw: {[r.getMessage()[:70] for r in handler.records]}"
    )
    assert any(r.levelno >= logging.INFO for r in records), (
        "the router's decline is logged below INFO, so production — which has "
        "written 0 DEBUG records across 677,108 lines — cannot see it. That is the "
        "exact blind spot that made 'self-healing is dead' and 'self-healing is "
        "declining' the same log."
    )
