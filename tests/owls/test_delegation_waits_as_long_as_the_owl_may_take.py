"""The caller must wait as long as the callee is allowed to run.

BAKIR, 2026-08-19, on his email assistant failing. MEASURED on the live logs:

    72 delegation timeouts, 14 of them that day, every one at timeout_s: 30.0
    secretary → mailbutler  timeout  (twice)
    secretary → Brain       timeout

And measured against what this hardware actually does, over 22,099 provider calls:

    median  6.5s     p90  75.2s     p99  132.4s     max  791.7s
    calls longer than 30s:  6,070  (27.5%)

A SINGLE provider call exceeds the delegation timeout more than a quarter of the
time, and a delegated turn is many calls plus tool work. So delegation was close to
guaranteed to fail for any sub-task worth delegating — which is exactly the design
mailbutler depends on, since it holds no shell tool and must ask the secretary to
run the Gmail script for it.

THE CONTRADICTION. Every owl manifest already declares ``timeout_seconds: 400.0``.
The delegator ignored it and used its own constructor default of 30.0. Two sources
of truth for one limit, and the shorter one was not the owl's own — so an owl
configured to take up to 400 seconds was abandoned at 30.

THE FIX IS NOT A BIGGER NUMBER. Raising the constant would be one more guess about
hardware; this box is a Jetson and the next one will differ. The caller now asks the
TARGET OWL how long it may take. One source of truth, no new knob, and it moves
automatically when an owl is reconfigured.
"""

from __future__ import annotations

import pytest

from stackowl.owls.a2a_delegation import A2ADelegator

pytestmark = pytest.mark.asyncio


class _Manifest:
    def __init__(self, timeout: float | None) -> None:
        self.timeout_seconds = timeout


class _Registry:
    def __init__(self, owls: dict[str, _Manifest]) -> None:
        self._owls = owls

    def get(self, name: str) -> _Manifest:
        return self._owls[name]


class _Services:
    def __init__(self, registry: object | None) -> None:
        self.owl_registry = registry


def _delegator(registry: object | None, default: float = 30.0) -> A2ADelegator:
    return A2ADelegator(
        a2a_queue=object(), services=_Services(registry), timeout_seconds=default,
    )


class TestTheCallerHonoursTheCalleesOwnLimit:
    async def test_it_uses_the_target_owls_configured_timeout(self) -> None:
        """400s is what every owl manifest already declares. Abandoning at 30 is
        what broke secretary → mailbutler twice on 2026-08-19."""
        d = _delegator(_Registry({"mailbutler": _Manifest(400.0)}))

        assert d.timeout_for("mailbutler") == 400.0

    async def test_a_slower_owl_gets_its_longer_budget(self) -> None:
        d = _delegator(_Registry({"deep": _Manifest(900.0)}))

        assert d.timeout_for("deep") == 900.0

    async def test_a_faster_owl_is_not_forced_to_wait_the_default(self) -> None:
        """The rule is "as long as the callee may run", not "always longer"."""
        d = _delegator(_Registry({"quick": _Manifest(5.0)}), default=30.0)

        assert d.timeout_for("quick") == 5.0


class TestItDegradesToTheDefaultRatherThanFailing:
    async def test_an_unknown_owl_falls_back(self) -> None:
        d = _delegator(_Registry({}))

        assert d.timeout_for("nobody") == 30.0

    async def test_a_manifest_without_a_timeout_falls_back(self) -> None:
        d = _delegator(_Registry({"x": _Manifest(None)}))

        assert d.timeout_for("x") == 30.0

    async def test_a_nonsense_timeout_falls_back(self) -> None:
        """A zero or negative wait would abandon the child instantly — worse than
        the bug being fixed."""
        d = _delegator(_Registry({"x": _Manifest(0.0)}))

        assert d.timeout_for("x") == 30.0

    async def test_no_registry_at_all_is_survivable(self) -> None:
        """Delegation must never fail because a lookup could not be made."""
        d = _delegator(None)

        assert d.timeout_for("anyone") == 30.0

    async def test_a_registry_that_raises_is_survivable(self) -> None:
        class _Boom:
            def get(self, name: str) -> object:
                raise RuntimeError("registry down")

        d = _delegator(_Boom())

        assert d.timeout_for("anyone") == 30.0
