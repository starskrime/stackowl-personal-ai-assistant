"""Five layers built end to end, and the last inch never wired.

MEASURED 2026-09-10 over every retained log: **57 of 57**
`[capabilities] resolve: capability UNAVAILABLE — dependent tools will not be
presented this turn` records carry `remedy: null`. Every one.

The channel is complete and has been since D05.3:

    Availability.remedy                       exists
    capabilities.resolve()                    reads it, `getattr(resource, "remedy", None)`
    tool_search                               renders it, `note += f" — fix: {remedy}"`
    _UnavailableCapability.__init__           ACCEPTS it
    the one call site that registers it       never passed it

So `tool_search`'s "— fix:" branch has never rendered, in any turn, ever — and
`resilience.py`'s protocol comment says outright *"Implement it when the subsystem
knows a CONCRETE operator action — e.g. the browser runtime knowing …"*, which no
resource in this tree does.

WHY IT COULD SIT THERE. `remedy` is optional at the protocol (deliberately, D05.3),
read through `getattr`, and defaulted at the constructor. Every layer therefore
works perfectly with the argument absent — nothing fails, nothing warns, and the
verdict a reader gets is a legitimate value. That is the same property
`HealthStatus.remedy = None` had before DEBT-288, one type over.

AND THE CAUSE WAS ALREADY KNOWN AT THE CALL SITE. `orchestrator.py` distinguishes
FOUR causes — gateway role, probe never ran, binary missing, guard bug — writes a
different `reason` for each, and then threw the distinction away one line later by
registering all four with no remedy. A reader saw the same shape whether the
browser was structurally absent from this process role (nothing to do), missing
from disk (there is a log to read), or unprobed (restart).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from stackowl.infra import capabilities
from stackowl.startup.browser_probe import (
    REMEDY_BINARY_MISSING,
    REMEDY_CAUSE_UNKNOWN,
    REMEDY_NOT_HOSTED_HERE,
    REMEDY_PROBE_DID_NOT_RUN,
)
from stackowl.startup.orchestrator import _UnavailableCapability  # noqa: PLC2701

_SRC = Path(__file__).resolve().parents[2] / "src" / "stackowl"


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-global and cached; leaving a verdict behind would
    make a later test in the same process read this one's browser."""
    capabilities.invalidate_cache()
    yield
    capabilities.invalidate_cache()


# --------------------------------------------------------------------------- #
# 1. The argument is REQUIRED — the structural half
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_an_absent_capability_cannot_be_registered_without_a_remedy() -> None:
    """STRUCTURAL, and that is the fix rather than the four strings.

    A default of None is what let this sit unwired: the call site compiled,
    the resolver read None, the renderer skipped its branch, and the verdict a
    reader got was a legitimate value. Making it positional means the next cause
    somebody adds cannot be registered silently.
    """
    with pytest.raises(TypeError):
        _UnavailableCapability("some reason")  # type: ignore[call-arg]

    sig = inspect.signature(_UnavailableCapability.__init__)
    assert sig.parameters["remedy"].default is inspect.Parameter.empty, (
        "`remedy` has a default again — the argument can be forgotten, which is "
        "exactly how 57 of 57 records came to carry `remedy: null`"
    )


@pytest.mark.tripwire
def test_a_remedy_that_says_nothing_is_refused_too() -> None:
    """FOUND BY MUTATION, and it is the half a static guard cannot hold.

    Making the argument positional stops it being FORGOTTEN. It does not stop it
    being satisfied with `""` — and that passed every other test in this file
    while producing a verdict byte-identical to the defect. The invariant is
    therefore enforced where the object is CONSTRUCTED, not where a test imagines
    the shape.
    """
    for reason, remedy in (("", "a remedy"), ("a reason", ""), ("a reason", "   ")):
        with pytest.raises(ValueError):
            _UnavailableCapability(reason, remedy)


# --------------------------------------------------------------------------- #
# 2. Four causes, four answers — the discrimination that decides it is worth it
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_each_cause_says_something_different_about_what_to_do() -> None:
    """The reasons were already four. Only the remedies were one.

    A single generic string would satisfy every other test in this file and would
    be worth nothing: the whole point is that "the gateway never hosts it" and
    "the binary is not on disk" call for opposite responses from a reader.
    """
    remedies = [
        REMEDY_NOT_HOSTED_HERE,
        REMEDY_PROBE_DID_NOT_RUN,
        REMEDY_BINARY_MISSING,
        REMEDY_CAUSE_UNKNOWN,
    ]
    assert len(set(remedies)) == 4, "two causes were given the same advice"
    assert all(r.strip() for r in remedies)

    # The role case is the one a reader must be able to DISMISS, and it is 57 of
    # the 57 records in the corpus. If it does not say so, this whole item bought
    # a louder warning rather than a readable one.
    assert "nothing to do" in REMEDY_NOT_HOSTED_HERE
    # The install case is the one a reader must be able to ACT on.
    assert "browser_probe" in REMEDY_BINARY_MISSING


@pytest.mark.tripwire
def test_the_orchestrator_gives_a_remedy_to_every_cause_it_distinguishes() -> None:
    """Reads the branch itself, because the four causes live in one `if` chain and
    a fifth added later must not fall through to a bare `reason`."""
    from stackowl.startup import orchestrator

    src = inspect.getsource(orchestrator)
    start = src.index('reason = "this process runs the gateway role')
    end = src.index("_capabilities.register(\"browser\", _UnavailableCapability", start)
    branch = src[start:end]

    assigns_reason = branch.count("reason = ")
    assigns_remedy = branch.count("remedy = ")
    assert assigns_reason == assigns_remedy, (
        f"{assigns_reason} causes get a reason and {assigns_remedy} get a remedy — "
        "a cause that names why but not what to do is the defect this fixes"
    )
    assert assigns_reason >= 4, "the cause chain shrank; re-read this guard"


@pytest.mark.tripwire
def test_the_registration_passes_the_cause_it_computed_not_a_literal() -> None:
    """FOUND BY MUTATION, and by the one that got away.

    Replacing the call's second argument with `""` satisfied every static guard in
    this file AND all 124 tests in `tests/startup`, because nothing there
    constructs the gateway-role path — the `ValueError` would only fire at boot.
    A text search for the argument name would have been satisfied by the comment
    above it, which this repo has already paid for once. So the CALL is read as an
    AST: both arguments must be the NAMES the branch computed, never literals.
    """
    from stackowl.startup import orchestrator

    tree = ast.parse(Path(inspect.getfile(orchestrator)).read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", "") == "_UnavailableCapability"
    ]
    assert len(calls) == 1, f"expected exactly one registration site, found {len(calls)}"
    args = calls[0].args
    assert len(args) == 2, "the registration stopped passing both halves"
    assert all(isinstance(a, ast.Name) for a in args), (
        "an argument is a literal — the branch computes a reason and a remedy per "
        "cause, and passing anything else discards the distinction that is the "
        f"whole fix: {[ast.dump(a)[:60] for a in args]}"
    )
    assert [a.id for a in args] == ["reason", "remedy"]


# --------------------------------------------------------------------------- #
# 3. End to end — it survives the resolver and reaches the reader
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_the_remedy_survives_the_resolver() -> None:
    """`resolve()` reads the resource through `getattr(resource, "remedy", None)`,
    so a resource that grows the attribute is picked up with no change there — and
    a resource that loses it degrades to None with nothing failing. That silence
    is why this is asserted rather than assumed."""
    capabilities.register(
        "browser",
        _UnavailableCapability("this process runs the gateway role", REMEDY_NOT_HOSTED_HERE),
    )
    verdict = capabilities.resolve("browser")

    assert verdict.ok is False
    assert verdict.reason
    assert verdict.remedy == REMEDY_NOT_HOSTED_HERE, (
        "the remedy did not survive the resolver — `Availability.remedy` is what "
        "tool_search renders, and it is still null"
    )


@pytest.mark.tripwire
def test_the_fix_branch_in_tool_search_can_finally_render() -> None:
    """THE BRANCH THAT HAS NEVER RUN. `tool_search` appends "— fix: {remedy}" only
    when `verdict.remedy` is truthy, and no resource in this tree has ever set one,
    so in production that append has executed zero times. Exercised here on the
    verdict shape the resolver now produces."""
    capabilities.register(
        "browser",
        _UnavailableCapability("camoufox binary not found", REMEDY_BINARY_MISSING),
    )
    verdict = capabilities.resolve("browser")

    note = f"UNAVAILABLE: {verdict.reason}"
    if verdict.remedy:
        note += f" — fix: {verdict.remedy}"
    assert "— fix:" in note, "the renderer still has nothing to render"


# --------------------------------------------------------------------------- #
# 4. One source — the copy that was there is gone, not synced
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_the_install_advice_has_exactly_one_home() -> None:
    """`health/contributors.py` carried its own wording of the same advice while
    the capability registry carried none. The cure this repo mandates is to REMOVE
    the copy, not to sync it — so the contributor asks `browser_probe` now, and
    this fails if a second literal reappears anywhere in `src/`.
    """
    literals: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if path.name == "browser_probe.py":
            continue  # the one home
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.lower()
                # THE ADVICE SENTENCE, not the identifier. The first draft of this
                # guard also matched `browser_install`, which is the name of a
                # STARTUP PHASE and of its own log lines — three legitimate hits in
                # `orchestrator.py`, none of them a copy of anything. A guard that
                # fires on the emitter it tells you to read is the cry-wolf failure
                # this repo keeps paying for.
                if "auto-installs at startup" in text:
                    literals.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert not literals, (
        "the browser install advice has grown a second copy — it goes stale "
        f"silently, which is what this change removed: {literals}"
    )
