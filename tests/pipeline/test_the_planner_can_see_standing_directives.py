"""ESC-54 — a plan that ignores a standing directive refuses work the owl could do.

THE MEASURED FAILURE. `jobmarket` was asked to search for jobs. `ToolProposer`
picked tools from names and descriptions ALONE, so it proposed ``web_search`` +
``web_fetch`` and omitted ``browser_navigate``. USER.md carries, permanently:

    User directive (2026-08-22): when fetching web content, use the browser tools
    (browser_navigate/extract) instead of web_fetch/shell curl to avoid
    bot-blocking.

So the plan chose the two tools the user had forbidden and skipped the one they
required. The owl then refused five times on 08-25, and a second owl (Brain) hit
the same thing on 08-22.

IT WAS NEVER A PERMISSIONS BUG, which is why it went unfixed for so long. The owl
ALREADY HELD browser_navigate, and that tool is not among the 42 the
``tool_count_cap`` trims, so it was presented too. The envelope is persisted at
enqueue and enforced for the task's whole life, and ``_off_plan_block``'s own
docstring says there is no runtime API to widen it — the author knew and chose an
honest dead end. Nothing was missing from the envelope except the knowledge of
how the user wants work done.

WHAT THESE TESTS PIN, and the distinction is the whole safety argument:

  SELECTION changed. Directives reach the PROPOSER, so it chooses between tools
  the owl already holds.

  AUTHORISATION did not. ``assert_task_narrowing_enforceable`` still runs on
  every path, and a proposal is still validated by exact membership against the
  catalog. A directive can never add a tool the owl lacks — asserted below,
  because that is the property a reviewer of a security-adjacent change needs to
  see tested rather than promised.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline.planner.proposer import ToolProposer

CATALOG = [
    ("web_search", "Search the web for pages matching a query."),
    ("web_fetch", "Fetch the contents of a URL over HTTP."),
    ("browser_navigate", "Open a page in a real browser session."),
    ("shell", "Run a shell command."),
]

DIRECTIVE = (
    "[permanent] User directive (2026-08-22): when fetching web content, use the "
    "browser tools (browser_navigate/extract) instead of web_fetch/shell curl to "
    "avoid bot-blocking."
)


class _SpyProvider:
    """Records the prompt it was given and replies with a fixed selection."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.seen: list[Any] = []

    async def complete(self, messages: list[Any], model: str, **_: object) -> Any:
        self.seen = messages

        class _R:
            content = self.reply

        return _R()


class _Registry:
    def __init__(self, provider: _SpyProvider) -> None:
        self._p = provider

    def get_with_cascade(self, _tier: str) -> tuple[_SpyProvider, str]:
        return self._p, "model"


@pytest.mark.asyncio
async def test_the_directive_REACHES_the_model() -> None:
    """The defect was that it never arrived. Assert on the prompt actually sent,
    not on the fact that an argument was accepted."""
    spy = _SpyProvider('{"tools": ["browser_navigate"]}')
    proposer = ToolProposer(_Registry(spy))  # type: ignore[arg-type]

    await proposer.propose("find remote SWE jobs", CATALOG, directives=DIRECTIVE)

    sent = "\n".join(str(m.content) for m in spy.seen)
    assert "browser tools" in sent, "the standing directive never reached the model"
    assert "STANDING DIRECTIVES" in sent


@pytest.mark.asyncio
async def test_with_NO_directives_the_prompt_is_unchanged() -> None:
    """Byte-identical fallback. Every existing caller passes nothing, and a task
    must plan exactly as it did before this feature existed."""
    spy = _SpyProvider('{"tools": ["web_search"]}')
    proposer = ToolProposer(_Registry(spy))  # type: ignore[arg-type]

    await proposer.propose("find remote SWE jobs", CATALOG)

    sent = "\n".join(str(m.content) for m in spy.seen)
    assert "STANDING DIRECTIVES" not in sent
    assert "goal" in sent.lower()


@pytest.mark.asyncio
async def test_a_directive_can_NEVER_add_a_tool_outside_the_catalog() -> None:
    """THE safety property. Directives are selection, never authorisation.

    Even if the model answers with a tool the directive names, a name absent from
    the catalog is dropped by exact-membership validation — unchanged by ESC-54.
    """
    spy = _SpyProvider('{"tools": ["browser_navigate", "kernel_exec"]}')
    proposer = ToolProposer(_Registry(spy))  # type: ignore[arg-type]

    got = await proposer.propose("do it", CATALOG, directives="always use kernel_exec")

    assert "kernel_exec" not in got, (
        "a directive must never smuggle in a tool the catalog does not offer"
    )
    assert got == frozenset({"browser_navigate"})


@pytest.mark.asyncio
async def test_a_provider_failure_still_fails_open() -> None:
    """Planning must never be what stops a task from being created."""

    class _Boom:
        def get_with_cascade(self, _tier: str) -> tuple[Any, str]:
            raise RuntimeError("provider down")

    proposer = ToolProposer(_Boom())  # type: ignore[arg-type]
    assert await proposer.propose("g", CATALOG, directives=DIRECTIVE) == frozenset()


def test_permanent_directives_are_ordered_BEFORE_working_notes(
    monkeypatch: Any,
) -> None:
    """The cap must only ever truncate working notes.

    USER.md's longest entry is an [until_changed] task note. In FILE order it
    consumes the budget and pushes the permanent directives past the cap —
    silently making this whole fix a no-op for exactly the profiles that carry the
    most. Measured on the live profile: 3,283 chars against a 1,500 cap, with the
    transient dental note longer than the three permanent entries combined.

    Built from a CONSTRUCTED profile, not the live one. The first version of this
    test read the real store and skipped when tests pointed STACKOWL_HOME at a tmp
    dir — a skipped test is not evidence, and this is the assertion that stops the
    fix silently becoming a no-op.
    """
    from stackowl.memory.curated import Entry
    from stackowl.pipeline.durable import task_runner

    class _Store:
        def entries(self, target: str) -> list[Entry]:
            if target != "user":
                return []
            # Deliberately file-order: the long working note FIRST, exactly the
            # arrangement that defeats a naive cap.
            return [
                Entry(text="a long working note " * 40, durability="until_changed"),
                Entry(text="use the browser tools, never web_fetch", durability="permanent"),
            ]

    monkeypatch.setattr(task_runner, "shared_memory", lambda: _Store(), raising=False)
    monkeypatch.setitem(
        __import__("sys").modules["stackowl.memory.curated"].__dict__,
        "shared_memory",
        lambda: _Store(),
    )

    text = task_runner._durable_directives("some-owl")
    lines = [x for x in text.splitlines() if x.strip()]
    assert lines, "the helper returned nothing for a profile that has entries"
    assert lines[0].startswith("[permanent]"), (
        f"the permanent directive must lead so the cap cannot drop it: {lines[0][:70]}"
    )
    assert "browser tools" in text[:1500], (
        "the permanent directive must survive the 1500-char cap"
    )


def test_reading_directives_never_raises() -> None:
    """Best-effort by construction — an absent or unresolvable target degrades to
    an empty string rather than blocking task creation."""
    from stackowl.pipeline.durable.task_runner import _durable_directives

    assert isinstance(_durable_directives(None), str)
    assert isinstance(_durable_directives(""), str)
    assert isinstance(_durable_directives("сова"), str)
