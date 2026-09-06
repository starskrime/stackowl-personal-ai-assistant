"""An event name is a contract. It may not be four independent string literals.

WHAT THE BOOT LOG SAYS, every boot:

    [startup] wiring audit: 38 handlers — 29 seeded, 9 on_demand, 0 event;
    0 dangling   {"dangling_events": []}

"0 dangling events" is produced by comparing one hand-written list against
another hand-written list. ``event_bridge._ALLOWED_EVENTS`` spells three event
names as literals; ``orchestrator.declared_event_publishers`` spells the same
three again, two as literals and one imported from its publisher; the publisher
``cost_tracker`` spells two of them a third time at its ``emit`` calls. Nothing
checks that the three spellings agree.

THIS HAS ALREADY GONE WRONG ONCE, and the code says so in its own words:

    DEBT-7 — this was `frozenset()`, so the dangling-event check compared
    subscribers against NOTHING and could only ever answer "dangling". It
    flagged the two budget events correctly by accident and would have said the
    same about perfectly-wired ones, which also means it could never have caught
    a genuinely NEW dangling subscription.

That was the vacuous direction. The other direction is still open and is worse,
because it is silent: rename or delete a publisher's emit and the auditor's own
copy of the string stays behind, so the audit reports the subscription as WIRED
forever. The guard whose entire purpose is finding dangling half-edges holds a
hand-maintained half-edge of its own.

THE CODEBASE ALREADY DOES THIS CORRECTLY ONCE. ``conversation_cost_report``
exports ``COST_REPORT_EVENT`` and the orchestrator imports it, so deleting that
publisher breaks the import — loudly, at boot, instead of quietly at audit time.
This makes that the rule rather than the exception: every subscribed event is
named by exactly one constant, owned by the module that emits it.

MEASURED CONTEXT, and deliberately NOT claimed as the defect. 130,285 of 130,288
cost rows are $0.00 and total spend ever is $0.019, because this deployment's
model is unpriced — so ``budget_exceeded`` and ``budget_80pct_alert`` have
publishers that will not fire here. That is a deployment fact, not a code fault;
it is recorded because it means "0 dangling" is already less reassuring than it
reads, and because a static declaration check cannot see it either way.
"""

from __future__ import annotations

import pathlib

import pytest

from stackowl.notifications.event_bridge import _ALLOWED_EVENTS
from stackowl.startup.wiring_audit import audit_scheduler_wiring

pytestmark = pytest.mark.anyio


class _Registry:
    """A HandlerRegistry stand-in with no handlers — this suite is about EVENTS."""

    def all(self) -> dict[str, object]:
        return {}


class _Db:
    async def fetch_all(self, _sql: str, _params: object = None) -> list[dict[str, str]]:
        return []


# --------------------------------------------------------------------------- #
# One constant per event, owned by the publisher                               #
# --------------------------------------------------------------------------- #


def _module_level_string_constants() -> dict[str, list[str]]:
    """Every ``UPPER_NAME = "literal"`` at module level in ``src/stackowl``, as
    value -> the modules that define it.

    DERIVED, NOT ENUMERATED, and the difference is the whole point of this file.
    This test used to compare ``_ALLOWED_EVENTS`` against a hand-written set of three
    imported constants — a FOURTH copy of the very list whose triplication it exists
    to forbid. On 2026-09-05 a fourth event was subscribed WITH a publisher constant
    and an emit behind it, exactly as the rule requires, and this test failed anyway:
    it was checking membership of a list, not compliance with a rule. A guard that
    must be edited every time the thing it guards is used correctly is a maintenance
    burden wearing a guard's clothes.
    """
    import ast

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"
    owners: dict[str, list[str]] = {}
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file is another test's job
            continue
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign | ast.Assign):
                continue
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            value = node.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    owners.setdefault(value.value, []).append(str(path))
    return owners


def test_every_subscribed_event_is_named_by_a_publisher_constant() -> None:
    """THE ROOT CAUSE. ``_ALLOWED_EVENTS`` held three bare literals, so the
    subscriber's spelling and the publisher's spelling were independent facts
    that happened to match."""
    owners = _module_level_string_constants()
    unowned = [e for e in _ALLOWED_EVENTS if not owners.get(e)]
    assert not unowned, (
        "the bridge subscribes to a name no module exports as a constant, so the "
        f"subscription is an independent spelling of a string: {unowned}"
    )
    ambiguous = {e: owners[e] for e in _ALLOWED_EVENTS if len(set(owners[e])) > 1}
    assert not ambiguous, (
        "an event name is defined as a constant in more than one module — there "
        f"must be exactly one owner: {ambiguous}"
    )


def test_every_subscribed_event_is_actually_EMITTED_by_its_owner() -> None:
    """A constant is only worth having if the owning module sends it. Exporting a
    name and never emitting it is the dangling half-edge one layer in — the audit
    would report the subscription as wired, and nothing would ever arrive.

    Generalised from ``test_the_publisher_emits_the_name_it_exports``, which asked
    this of ``cost_tracker`` alone and so could not have caught a new publisher that
    exported without emitting."""
    owners = _module_level_string_constants()
    silent: list[str] = []
    for event in sorted(_ALLOWED_EVENTS):
        owner = pathlib.Path(next(iter(set(owners[event]))))
        source = owner.read_text(encoding="utf-8")
        # The constant's own NAME, as used at the emit call — never the literal.
        const = next(
            line.split("=")[0].strip()
            for line in source.splitlines()
            if line.startswith(tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
            and "=" in line
            and f'"{event}"' in line
        )
        if f"emit({const}" not in source:
            silent.append(f"{owner.name} exports {const} but never emits it")
    assert not silent, "\n  ".join(silent)


@pytest.mark.tripwire
def test_the_declaration_covers_everything_the_bridge_SUBSCRIBES() -> None:
    """WHAT WENT WRONG ON 2026-09-05, and what now catches it in 40 seconds.

    The audit compares subscriptions against ``declared_event_publishers``. That
    declaration was a LOCAL inside ``orchestrator._phase_gateway``, so nothing could
    read it: adding a properly-wired subscription without also updating a list a
    hundred lines away produced a boot log reporting a live subscription as DANGLING,
    discoverable only by restarting. The declaration moved to ``wiring_audit`` for
    exactly this assertion — a declaration nothing can compare is not a declaration.
    """
    from stackowl.startup.wiring_audit import declared_event_publishers

    missing = set(_ALLOWED_EVENTS) - declared_event_publishers()
    assert not missing, (
        "the bridge subscribes to events with no declared publisher — the boot "
        f"wiring audit will report these as dangling: {sorted(missing)}"
    )


@pytest.mark.tripwire
def test_the_audit_chain_holds_no_literal_copy_of_an_event_name() -> None:
    """THE GUARD THAT MAKES IT STICK. Each of the three modules in the chain —
    publisher, subscriber, auditor — must reference the constant, never re-spell
    the string. A fourth copy added later is exactly how the auditor comes to
    disagree with reality while reporting success."""
    import inspect

    from stackowl.notifications import event_bridge
    from stackowl.providers import cost_tracker
    from stackowl.startup import orchestrator, wiring_audit

    # THE CHAIN, DERIVED. This was a hand-written table of three modules and their
    # names — a fifth copy, in the file forbidding copies, and it silently stopped
    # covering the orchestrator when the declaration moved out of it. The chain is
    # every module that could hold a spelling: the subscriber, the auditor, the
    # declaration, and each event's own owner.
    offenders: list[str] = []
    owners = _module_level_string_constants()
    for name in sorted(_ALLOWED_EVENTS):
        modules = [event_bridge, orchestrator, wiring_audit, cost_tracker]
        # KEYED BY PATH, not by dotted name. Keyed by both, a module that is also an
        # owner appeared TWICE — once exempted, once not — and its own definition
        # line reported itself as a re-spelling. The instrument has to identify a
        # module the same way in both halves of its own comparison.
        sources = {str(inspect.getsourcefile(m)): inspect.getsource(m) for m in modules}
        sources[owners[name][0]] = pathlib.Path(owners[name][0]).read_text(
            encoding="utf-8"
        )
        for where, source in sources.items():
            # The DEFINITION line is the one legitimate literal, and it lives in the
            # OWNER only. Stripping `= "<name>"` from every module — as this did until
            # a mutant walked through it — exempts a second module's definition too,
            # which is the exact duplicate the rule forbids. Measured: a planted
            # `_MUTANT_COPY = "consent.confined_execution_granted"` in the bridge was
            # invisible here and was caught only by the ownership check next door.
            body = source
            if where == owners[name][0]:
                body = body.replace(f'= "{name}"', "")
            if f'"{name}"' in body or f"'{name}'" in body:
                offenders.append(f"{where} re-spells {name!r}")
    assert not offenders, (
        "an event name is spelled as a literal outside its owning constant — a "
        "rename there is silent:\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# The instrument must still be able to say "dangling"                          #
# --------------------------------------------------------------------------- #


async def test_a_genuinely_unpublished_subscription_is_still_reported() -> None:
    """THE VACUITY CONTROL. DEBT-7's first value made this check answer
    "dangling" for everything; the fix could just as easily make it answer
    "wired" for everything, and both look like a clean boot log. A zero over a
    zero is not a pass, so the check is exercised against a subscription that
    genuinely has no publisher."""
    report = await audit_scheduler_wiring(
        _Db(), _Registry(),
        allowed_events={"an_event_nobody_emits"},
        declared_publishers=set(_ALLOWED_EVENTS),
    )
    assert report.dangling_events == ["an_event_nobody_emits"], report.dangling_events


async def test_the_live_wiring_reports_no_dangling_subscription() -> None:
    """And the real configuration passes — measured, not assumed. This is the
    assertion the boot log makes every morning; it belongs in a test too, so a
    publisher deleted between boots fails here first."""
    from stackowl.startup.wiring_audit import declared_event_publishers

    report = await audit_scheduler_wiring(
        _Db(), _Registry(),
        allowed_events=_ALLOWED_EVENTS,
        # THE PRODUCTION DECLARATION, not a re-spelling of it. This test held its own
        # copy of the three names, so it asserted that two hand-written lists in the
        # TEST agreed — never that the boot audit would pass. It could not have caught
        # the 2026-09-05 dangling subscription, which is precisely the failure it
        # claims to be "the assertion the boot log makes every morning".
        declared_publishers=declared_event_publishers(),
    )
    assert report.dangling_events == []
