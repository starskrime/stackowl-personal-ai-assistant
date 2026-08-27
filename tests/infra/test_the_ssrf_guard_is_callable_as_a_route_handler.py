"""The SSRF guard has to be something Playwright can actually CALL.

THIS IS WHY THE BROWSER DID NOT WORK. Traced 2026-08-27 after two wrong theories
(the engine, then memory) and a night of the browser being unusable.

Playwright inspects a route handler's arity to decide between ``handler(route)``
and ``handler(route, request)``. ``guard_playwright_navigation`` declares TWO
parameters — ``route`` and the keyword-only ``guard`` — so Playwright counted two
and called it with two positionals. ``guard`` is keyword-only, so every call
raised::

    TypeError: guard_playwright_navigation() takes 1 positional argument
               but 2 were given

It was registered with ``ctx.route("**/*", ...)`` — on EVERY REQUEST of every
interactive browser session. So every navigation failed, ``browser_navigate``
timed out, the runtime was declared dead, sessions were purged, and the recycle
storm followed. Ten browser calls, zero successes.

THE BROWSER WAS NEVER BROKEN, AND NEITHER WAS THE ENGINE. Both Camoufox and
Chromium "died" identically because the fault was engine-agnostic: it was ours.

THE CLEAREST EVIDENCE was sitting in the logs the whole time. ``web_fetch``
succeeded on every single attempt while the browser failed on every single one —
because web_fetch happened to wrap the guard in a one-argument function. Same
guard, one call site correct and one not, and the two behaved exactly as the
signature predicted.

These tests pin the ARITY, because that is the property that broke. A test that
only checked "the guard blocks internal IPs" would have passed throughout.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from stackowl.infra.net.ssrf_guard import (
    SsrfGuard,
    guard_playwright_navigation,
    make_route_guard,
)


def test_the_route_handler_takes_exactly_one_positional_parameter() -> None:
    """THE regression. Playwright decides how to call a handler by counting its
    parameters, so the count IS the contract."""
    handler = make_route_guard()
    params = list(inspect.signature(handler).parameters.values())

    assert len(params) == 1, (
        f"a route handler must declare exactly one parameter — Playwright counts "
        f"them to choose between handler(route) and handler(route, request). "
        f"Got {[p.name for p in params]}"
    )
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_the_raw_guard_still_looks_like_two_parameters() -> None:
    """Pins WHY the factory has to exist, so nobody deletes it as ceremony.

    The raw guard is fine as a function and unusable as a handler. Keeping this
    explicit means the next person sees the trap rather than rediscovering it
    through a browser that mysteriously will not navigate.
    """
    params = list(inspect.signature(guard_playwright_navigation).parameters)

    assert params == ["route", "guard"], (
        "if this changed, re-check whether the raw guard is now safe to pass "
        "directly to ctx.route — and update make_route_guard's docstring"
    )


@pytest.mark.asyncio
async def test_the_handler_can_be_invoked_the_way_playwright_invokes_it() -> None:
    """Calling convention, not just shape. A one-parameter closure that still
    exploded on a real call would pass the arity check and fail in production."""
    seen: list[str] = []

    class _Route:
        request = type("R", (), {"url": "https://example.com/", "is_navigation_request": lambda self: True})()

        async def continue_(self, **_: Any) -> None:
            seen.append("continued")

        async def abort(self, *_a: Any, **_k: Any) -> None:
            seen.append("aborted")

        async def fallback(self, **_: Any) -> None:
            seen.append("fallback")

    handler = make_route_guard(SsrfGuard())
    await handler(_Route())

    assert seen, "the handler did not act on the route at all"


def test_every_route_registration_uses_the_factory() -> None:
    """The property that keeps this fixed everywhere.

    Two call sites had the bug — browser sessions and the website-watch handler —
    and a third (web_fetch) had hand-rolled its own correct wrapper. Three copies
    of one rule, one of which was right by accident. This fails if a new call site
    passes the raw guard to ctx.route again.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if 'route("**/*", guard_playwright_navigation)' in text:
            offenders.append(str(path.relative_to(root)))

    assert not offenders, (
        "these pass the raw guard straight to ctx.route, which Playwright will "
        f"call with two positionals and break every request: {offenders}"
    )
