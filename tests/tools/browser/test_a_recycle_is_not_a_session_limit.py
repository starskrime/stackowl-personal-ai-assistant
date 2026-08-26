"""A crashed browser must not report itself as a full session pool.

MEASURED 2026-08-26. ``BrowserSessionLimitError`` is raised at three sites for
three unrelated conditions:

    max concurrent browser sessions reached   (a real limit)
    max pages per session reached             (a real limit)
    browser runtime recycled during session open   (a CRASH)

All TWELVE occurrences in production were the third. The first has never fired
once. So the error anyone actually sees says the pool is full, when the truth is
that the browser process died — and the obvious next move, hunting a session
leak, is the wrong one.

I MADE THAT EXACT MISTAKE. I wrote "sessions are opened and never released" into
progress.yml as the actionable lead and spent a measurement round on a leak that
does not exist: the TTL sweep IS started (orchestrator.py:1106), ``close()`` has
real callers, and camoufox was alive throughout. The name was the entire
misdirection.

It is a SUBCLASS, deliberately. Two call sites in ``tools.py`` already catch
``BrowserSessionLimitError``, and a crashed browser should still take whatever
back-off path a saturated pool takes. Nothing about control flow changes here —
only what the reader is told.

Same family as three other defects found the same night: a timeout log that said
"freed for retry/re-arm" when nothing was freed, a grant log that says "with the
user's approval" when nobody approved, and two test doubles whose docstrings
vouched for the wrong parameter name. A name that asserts something other than
what happened costs a diagnosis every time it is believed.
"""

from __future__ import annotations

import pytest

from stackowl.tools.browser.sessions import (
    BrowserRuntimeRecycledError,
    BrowserSessionLimitError,
)


def test_a_recycle_is_still_caught_by_existing_handlers() -> None:
    """The compatibility guarantee. tools.py catches the base type in two places,
    and a crashed browser should keep taking that path."""
    with pytest.raises(BrowserSessionLimitError):
        raise BrowserRuntimeRecycledError("browser runtime recycled during session open")


def test_a_recycle_can_be_told_apart_from_a_real_limit() -> None:
    """The point of the change: the two are now distinguishable, so the next
    person's diagnosis starts in the right place."""
    assert issubclass(BrowserRuntimeRecycledError, BrowserSessionLimitError)
    assert not issubclass(BrowserSessionLimitError, BrowserRuntimeRecycledError)

    real_limit = BrowserSessionLimitError("max concurrent browser sessions reached (8)")
    assert not isinstance(real_limit, BrowserRuntimeRecycledError), (
        "a genuine pool limit must NOT be mistaken for a crash either — the "
        "confusion has to be fixed in both directions or it is just moved"
    )


def test_the_message_says_it_is_not_a_limit() -> None:
    """Belt and braces for anyone reading a log line rather than catching a type,
    which is how this was encountered in the first place."""
    from stackowl.tools.browser import sessions

    src = sessions.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "NOT a session limit" in text, (
        "the raised message must say what it is not; the log line is what an "
        "operator actually reads"
    )


def test_the_recycle_site_raises_the_specific_type() -> None:
    """Pins the wiring, not just the class. A new exception nothing raises is this
    codebase's most common defect — design that exists and is never reached."""
    import inspect

    from stackowl.tools.browser import sessions

    body = inspect.getsource(sessions.BrowserSessionRegistry.open)
    assert "BrowserRuntimeRecycledError" in body, (
        "open() must raise the specific type for the recycle case, or this is a "
        "class with no callers"
    )
    assert "recycled during session open" in body
