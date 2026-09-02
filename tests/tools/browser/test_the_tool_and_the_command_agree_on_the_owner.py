"""A browser session the TOOL created was invisible to the COMMAND that lists it.

`sessions.py` declares the contract in its own docstring — CLI "local", Telegram
`telegram:{chat_id}` — and it was implemented TWICE, differently:

* `tools/browser/tools.py::_owner_key_from_state` returned `"local"`
  unconditionally, because when it was written a tool could not reach the turn:
  *"_dispatch only forwards LLM kwargs. For v1 we use 'local'."*
* `commands/browser_command.py::_owner_key_for_session` built
  `telegram:{session_key}` and said it *"Mirrors tools.py logic"* — which it did
  not.

So on a Telegram turn the model opened a session owned by `local`, and
`/browser sessions` looked under `telegram:{...}` and found none of it. Not two
copies of one rule: two DIFFERENT rules for the same user, one of them claiming
to be the other.

THE PREMISE IS GONE. `TraceContext` carries `session_key` and `channel` as
contextvars propagated across async hops — the same mechanism `PlanStore` uses to
key a plan to its lane, added while fixing a plan that died at the end of its
turn. A tool can read the channel now.

`owner_key_for_turn` is the one implementation. Explicit arguments win so a
caller holding a PipelineState passes what it has; a tool passes nothing. Both
get the same answer.

IT FAILS TOWARD THE OLD BEHAVIOUR: an unresolvable context yields `"local"`,
which is the single-user default and exactly what the whole system used before
this existed. A failure degrades to the previous behaviour, never to a wrong
owner — which would hand one user's session to another.
"""

from __future__ import annotations

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.tools.browser.sessions import owner_key_for_turn


@pytest.fixture(autouse=True)
def _clean_context():  # noqa: ANN201
    token = TraceContext.start(session_key="", channel="")
    yield
    TraceContext.reset(token)


def test_the_tool_and_the_command_agree_on_a_telegram_turn() -> None:
    """The defect, stated as the property that was violated."""
    from stackowl.commands import browser_command
    from stackowl.tools.browser import tools

    token = TraceContext.start(session_key="72055773", channel="telegram")
    try:
        from types import SimpleNamespace

        state = SimpleNamespace(channel="telegram", session_key="72055773")
        tool_key = tools._owner_key_from_state()  # noqa: SLF001
        cmd_key = browser_command._owner_key_for_session(state)  # type: ignore[arg-type]  # noqa: SLF001
    finally:
        TraceContext.reset(token)

    assert tool_key == cmd_key == "telegram:72055773", (
        "the tool and the command disagree about who owns the session — the "
        "command lists sessions the tool never filed there"
    )


def test_cli_is_still_local() -> None:
    """The single-user default must not change: "local" is what every existing
    session is filed under."""
    token = TraceContext.start(session_key="whatever", channel="cli")
    try:
        assert owner_key_for_turn() == "local"
    finally:
        TraceContext.reset(token)


def test_an_unresolvable_context_fails_toward_LOCAL() -> None:
    """The expensive direction. Guessing an owner would hand one user's browser
    session to another; "local" is merely the old behaviour."""
    assert owner_key_for_turn() == "local"
    assert owner_key_for_turn(channel="telegram", session_key="") == "local"
    assert owner_key_for_turn(channel="", session_key="72055773") == "local"


def test_explicit_arguments_win_over_the_ambient_context() -> None:
    """A caller holding a PipelineState is authoritative for its own turn."""
    token = TraceContext.start(session_key="ambient", channel="telegram")
    try:
        assert owner_key_for_turn(channel="slack", session_key="C123") == "slack:C123"
    finally:
        TraceContext.reset(token)


def test_neither_caller_carries_its_own_copy_of_the_rule() -> None:
    """Structural. The whole defect was two implementations, one of them
    describing itself as the other."""
    import inspect

    from stackowl.commands import browser_command
    from stackowl.tools.browser import tools

    for src in (inspect.getsource(tools._owner_key_from_state),  # noqa: SLF001
                inspect.getsource(browser_command._owner_key_for_session)):  # noqa: SLF001
        assert "owner_key_for_turn" in src
        assert 'f"telegram:' not in src, "a caller is building the key itself again"
