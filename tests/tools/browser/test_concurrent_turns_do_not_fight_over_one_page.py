"""Two turns for the same owl must not share one page.

MEASURED IN PRODUCTION 2026-08-28, and this is a regression I introduced. Two
traces navigated on the same owner's session at once::

    00:21:01  fb2e9cec  -> httpbin.org      (sessions.open)
    00:21:02  recover-  -> linkedin.com     "reusing this owner's live session"
    00:21:03  fb2e9cec  FAILS  Error: Page.goto: NS_BINDING_ABORTED
    00:21:07  fb2e9cec  retries, reuses the same session
    00:21:41  fb2e9cec  TimeoutError 30000ms   (recover- navigating throughout)

NS_BINDING_ABORTED is Firefox for "a second navigation cancelled yours". The two
turns were driving ONE page, so each ``goto`` killed the other's.

WHERE IT CAME FROM. e7e444a2 fixed Bakir's "it always returns the example domain
page": ``get_page`` with no handle used to MINT A NEW PAGE every call, so
``browser_navigate`` loaded the URL on page 1 and the following
``browser_snapshot`` described a blank page 2. The fix made "no handle" mean "the
page I am working on" by returning ``sess.current_handle``.

THAT FIX IS RIGHT AND ITS SCOPE WAS WRONG. "The page I am working on" is a
property of a TURN, not of a session. One ``current_handle`` per session means
every concurrent turn for that owl points at the same page. Sequential calls
within a turn were fixed; concurrent turns were broken in the same stroke.

So the handle is keyed on the ambient trace id (TraceContext), which is exactly
"the current turn" and needs no parameter threaded through the 25 browser tools.

BOTH PROPERTIES ARE PINNED BELOW, because fixing one by breaking the other is how
this defect was created in the first place.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.tools.browser.sessions import BrowserSession


class _FakePage:
    """Must implement close(), or eviction "passes" via its own except branch.

    The first version of this fake had only is_closed(). Eviction then raised
    AttributeError, was caught by the fail-open handler, and the cap tests went
    green without the real close path ever running — a fixture that could not
    show the bug it was written for.
    """

    def __init__(self, n: int) -> None:
        self.n = n
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self._closed = True


class _FakeContext:
    """Mints a distinguishable page each time, like a real BrowserContext."""

    def __init__(self) -> None:
        self.made = 0

    async def new_page(self) -> _FakePage:
        self.made += 1
        return _FakePage(self.made)


@pytest.fixture
def browser_registry() -> Any:
    """A REAL BrowserSessionRegistry holding one session; only Playwright is faked.

    The registry, its locks, its page cap and get_page's real branching are all
    exercised — the fake stops at ``context.new_page()``, which is the only part
    that would need a live browser. A doubled registry would prove nothing about
    the code that actually broke.
    """
    from stackowl.config.browser import BrowserSettings
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    class _Runtime:
        pass

    registry = BrowserSessionRegistry(_Runtime(), BrowserSettings())
    registry._sessions["s1"] = BrowserSession(
        session_id="s1", owner_key="owl:secretary", profile_name=None,
        context=_FakeContext(),
    )
    return registry


async def _handle_under_trace(registry: Any, trace_id: str) -> str:
    """Ask for 'the page I am working on' while a given turn is in scope."""
    token = TraceContext.start(trace_id=trace_id)
    try:
        _, _, handle = await registry.get_page("s1")
        return handle
    finally:
        TraceContext.reset(token)


@pytest.mark.asyncio
async def test_two_concurrent_turns_get_DIFFERENT_pages(browser_registry: Any) -> None:
    """THE regression. One page shared by two turns is what aborted the navigation."""
    a, b = await asyncio.gather(
        _handle_under_trace(browser_registry, "trace-aaaa"),
        _handle_under_trace(browser_registry, "trace-bbbb"),
    )

    assert a != b, (
        "two concurrent turns were handed the SAME page — each navigation will "
        "abort the other's with NS_BINDING_ABORTED"
    )


@pytest.mark.asyncio
async def test_the_same_turn_keeps_ONE_page(browser_registry: Any) -> None:
    """e7e444a2's fix, which must not regress while fixing the above.

    If this goes red the "always returns the example domain page" bug is back:
    navigate loads the URL on one page and snapshot describes a fresh blank one.
    """
    first = await _handle_under_trace(browser_registry, "trace-same")
    second = await _handle_under_trace(browser_registry, "trace-same")

    assert first == second, (
        "the same turn was handed a NEW page on its second call — this is the "
        "blank-tab bug e7e444a2 fixed"
    )


@pytest.mark.asyncio
async def test_a_call_with_no_trace_still_works(browser_registry: Any) -> None:
    """The control for the fallback path.

    Plenty of runs carry no trace at all (boot recovery, sweeps, direct CLI). They
    must still get a stable page rather than a fresh blank one per call, or the
    original bug returns for exactly those callers.
    """
    first = (await browser_registry.get_page("s1"))[2]
    second = (await browser_registry.get_page("s1"))[2]

    assert first == second


@pytest.mark.asyncio
async def test_more_turns_than_the_page_cap_still_get_a_page(browser_registry: Any) -> None:
    """Per-turn pages are a FINITE resource, and this deployment already exceeds it.

    max_concurrent_pages_per_session defaults to 4, and 5 concurrent jobmarket
    tasks were measured running on the SAME owl (secretary) on 2026-08-28. So the
    very first day this shipped, the 5th turn would have hit
    BrowserSessionLimitError — trading a page-contention bug for a page-exhaustion
    one and reading, to Bakir, as "the browser is broken again".

    The least-recently-used turn releases its page instead. A turn that has gone
    quiet longest is the best candidate, and if it comes back it simply gets a
    fresh page on its next call.
    """
    handles = []
    for i in range(6):  # cap is 4
        handles.append(await _handle_under_trace(browser_registry, f"trace-{i}"))

    assert len(handles) == 6, "a turn was refused a page"
    sess = browser_registry._sessions["s1"]
    assert len(sess.pages) <= 4, f"page cap breached: {len(sess.pages)} pages open"


@pytest.mark.asyncio
async def test_the_per_turn_map_does_not_grow_for_ever(browser_registry: Any) -> None:
    """No decay is the fourth recurring defect shape in this tree.

    One entry per trace id, never pruned, is an append-only map on a session that
    can live for hours — it would accumulate an entry for every turn the owl ever
    ran. Bounded by the same eviction that bounds the pages.
    """
    for i in range(30):
        await _handle_under_trace(browser_registry, f"trace-{i}")

    sess = browser_registry._sessions["s1"]
    assert len(sess.current_handles) <= 4, (
        f"the per-turn map grew to {len(sess.current_handles)} entries — it only "
        "ever appends"
    )


@pytest.mark.asyncio
async def test_the_evicted_page_is_actually_CLOSED(browser_registry: Any) -> None:
    """Eviction must release the browser resource, not just forget the handle.

    Dropping the dict entry while leaving the page open would keep the cap
    satisfied in our own bookkeeping while the real browser accumulated tabs —
    a write with no reader, and invisible until the browser fell over.
    """
    sess = browser_registry._sessions["s1"]
    for i in range(4):
        await _handle_under_trace(browser_registry, f"trace-{i}")
    first_page = next(iter(sess.pages.values()))

    await _handle_under_trace(browser_registry, "trace-overflow")

    assert first_page.is_closed(), "the evicted page was dropped but never closed"
    assert len(sess.pages) <= 4
