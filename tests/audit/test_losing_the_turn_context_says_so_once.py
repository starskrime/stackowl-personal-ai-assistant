"""Losing the services context must name itself, not 51 other subsystems.

`StepServices` carries 51 collaborators. When its ContextVar is unbound,
`get_services()` returns all 51 as ``None`` — and every consumer then reports ITS
OWN collaborator as missing. One unbound call becomes 51 different messages, not
one of which mentions the context.

MEASURED 2026-09-10, and the cost was the operator's. `web_fetch` logged
``runtime not initialized`` 30 times and told the model "Browser runtime not
initialized." The browser was RUNNING: a call reported the runtime missing at
00:42:30 while another `web_fetch` in the SAME PROCESS exited successfully at
00:42:36 and `browser_navigate` reused a live session five seconds later.
Downstream: three attempts, a tripped same-tool circuit breaker, a model that
claimed a result it never received, and five goal turns delivered as "I couldn't
fully complete this".

THE HANDLER LOGGED NOTHING, which `CLAUDE.md` makes a mandatory failure — "every
`except` logs", "no hidden errors: recover loudly or propagate". MEASURED across
`src/`: this was the ONLY `except LookupError` in the tree, and it was the one
that swallows every service the platform has.

DIAGNOSIS WAS THE WHOLE COST. Three readings were pursued and abandoned before
the context was suspected — a browser that failed to start, the gateway role
(which never runs turns at all), and a services object built before the runtime.
Each was refuted by evidence the platform already held. The message named a
subsystem that was up, so every reading was about that subsystem.
"""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

from stackowl.pipeline import services as svc


class _Capture(logging.Handler):
    """Read the named logger DIRECTLY.

    Not `caplog`: `configure_logging` sets ``propagate = False`` on `stackowl`,
    so a caplog-based assertion passes here and fails in any session that has
    configured logging first — a guard that works only in the test's own harness.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> _Capture:
    monkeypatch.setattr(svc, "_unbound_sites", set())
    handler = _Capture()
    logger = logging.getLogger("stackowl.engine")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(previous)


def _unbound() -> svc.StepServices:
    """Call from a distinct line so each test owns its own call site."""
    return svc.get_services()


@pytest.mark.tripwire
def test_an_unbound_context_is_reported_at_warning(captured: _Capture) -> None:
    """WARNING, not DEBUG. This deployment has written 677,108 records and ZERO
    are DEBUG, so a DEBUG line here would not exist when it is needed — which is
    exactly why the condition went unnamed."""
    result = _unbound()
    assert result.browser_runtime is None, "the empty fallback is what is under test"
    assert captured.records, "losing every service produced no log record at all"
    record = captured.records[0]
    assert record.levelno >= logging.WARNING, logging.getLevelName(record.levelno)


@pytest.mark.tripwire
def test_the_message_points_at_the_context_not_the_subsystem(captured: _Capture) -> None:
    """THE DISCRIMINATOR. A reader arriving from "X not initialized" has to be
    told that X may be perfectly healthy. Without that sentence this line is one
    more report of a missing collaborator, which is the thing being fixed."""
    _unbound()
    text = captured.records[0].getMessage()
    assert "CONTEXT" in text
    assert "collaborator" in text


@pytest.mark.tripwire
def test_it_reports_once_per_call_site_and_cannot_flood(captured: _Capture) -> None:
    """`get_services` runs once per tool per turn. An unconditional WARNING would
    flood the log it is trying to make readable, and a once-per-PROCESS line
    would hide every site but the first. One line per distinct caller is the
    bound, and the set is keyed by code location so it cannot grow past the
    number of lines that call this function."""
    for _ in range(25):
        _unbound()  # same line every time — one site
    assert len(captured.records) == 1, [r.getMessage()[:60] for r in captured.records]

    svc.get_services()  # a DIFFERENT line — a different site, reported once
    assert len(captured.records) == 2


@pytest.mark.tripwire
def test_a_bound_context_says_nothing(captured: _Capture) -> None:
    """The vacuity control. A warning that fires on the normal path would be
    switched off within a week, and then this guard would be measuring nothing."""
    token = svc.set_services(svc.StepServices(settings=object()))
    try:
        assert svc.get_services().settings is not None
    finally:
        svc.reset_services(token)
    assert not captured.records


@pytest.mark.tripwire
def test_the_only_lookuperror_handler_in_src_still_logs() -> None:
    """The reach, measured rather than asserted: `except LookupError` occurs ONCE
    in `src/`, and it is the one that discards every service. If a second appears,
    it inherits this defect unless it says something."""
    root = pathlib.Path(__file__).resolve().parents[2] / "src"
    silent: list[str] = []
    seen = 0
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            caught = node.type
            names = (
                [caught.id] if isinstance(caught, ast.Name)
                else [x.id for x in caught.elts if isinstance(x, ast.Name)]
                if isinstance(caught, ast.Tuple) else []
            )
            if "LookupError" not in names:
                continue
            seen += 1
            if not _says_something(node, tree):
                silent.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    assert seen >= 1, "no LookupError handler found at all — this guard is vacuous"
    assert not silent, (
        "a LookupError is caught and nothing is said about it:\n"
        + "\n".join(f"  {s}" for s in silent)
        + "\n\nCLAUDE.md makes this mandatory: every except logs, no hidden errors."
    )


def _says_something(handler: ast.ExceptHandler, tree: ast.Module) -> bool:
    """Does this handler leave a record — directly, or via one call?

    RESOLVES ONE LEVEL, deliberately. A handler that delegates to a named
    reporting helper is the CORRECT shape, not a violation: the fix this file
    guards does exactly that, so a rule reading only the handler's own body would
    have failed the very change it exists to protect. One level is enough to
    cover delegation and shallow enough that the rule stays a rule rather than a
    call-graph analysis nobody can predict.
    """
    body = ast.dump(ast.Module(body=handler.body, type_ignores=[]))
    if "log" in body:
        return True
    helpers = {
        node.name: ast.dump(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and "log" in helpers.get(node.func.id, "")
        for node in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
    )


def test_web_fetch_does_not_assert_a_subsystem_it_never_asked() -> None:
    """The message that cost three abandoned readings.

    It said "Browser runtime not initialized" — a claim about the RUNTIME — while
    knowing only that the services it was handed carry no browser.
    """
    import inspect

    from stackowl.tools.io import web_fetch

    src = inspect.getsource(web_fetch)
    assert 'error="Browser runtime not initialized."' not in src
    assert "no browser runtime in this turn's services" in src
