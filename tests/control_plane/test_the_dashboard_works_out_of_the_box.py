"""It works when a customer clones and runs it — and what that makes real.

**THE OPERATOR ANSWERED ESC-172 ON 2026-09-12:** *"Make it always available in
all interfaces by default in platform. So when customer runs it it will work out
of box."* So `control_plane.bind_address` defaults to `0.0.0.0` and the dashboard
is reachable from the customer's own browser with no configuration at all.

**FLIPPING THAT DEFAULT ALONE WOULD HAVE BROKEN THE DASHBOARD COMPLETELY**, and
that is the half of this change worth reading. The CSRF check built its expected
host from `f"{bind_address}:{port}"`. On loopback that is exactly the string a
browser sends, so it was correct and invisible. `0.0.0.0:8787` is a string no
browser ever sends: a person opening `http://192.168.1.50:8787` sends
`Host: 192.168.1.50:8787`, the comparison fails, and every request is refused —
while every existing loopback test stays green. A config default and a security
check were coupled through a literal, and only widening the bind revealed it.

**AND IT CARRIES THE BRAKE DEBT-310 NAMED.** That record shipped the login route
saying: *"NO RATE LIMIT ... It becomes real the moment ESC-172 is answered 'widen
the bind', and it is named here so that answer carries this with it rather than
discovering it afterwards."* It was answered, so the limiter is in this change
and not a later one.

WHAT THIS DOES NOT CLAIM. Against the shipped `admin`/`admin` the limiter is
worth nothing — the first guess wins. It protects a password the operator has
CHANGED. The default itself is defended by being announced: at WARNING on every
boot, and as a banner on the page after every sign-in.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.config.control_plane_settings import ControlPlaneSettings
from stackowl.control_plane.login_guard import (
    MAX_FAILURES,
    MAX_TRACKED_SOURCES,
    LoginAttempts,
)
from stackowl.control_plane.server import ControlPlaneServer

_TOKEN = "t" * 43


class _Req:
    def __init__(self, body: dict[str, Any] | None = None, *, remote: str = "10.0.0.9",
                 **headers: str) -> None:
        self.headers = dict(headers)
        self.remote = remote
        self._body = body if body is not None else {}

    async def json(self) -> dict[str, Any]:
        return self._body


def _server(**cfg: Any) -> ControlPlaneServer:
    settings_cp = ControlPlaneSettings(**cfg)

    class _Settings:
        control_plane = settings_cp

    srv = ControlPlaneServer(_Settings())  # type: ignore[arg-type]
    srv._token = _TOKEN  # noqa: SLF001
    return srv


class TestItShipsReachable:
    @pytest.mark.tripwire
    def test_the_default_bind_is_every_interface(self) -> None:
        """A capability ships ENABLED here, and a dashboard nobody can open is
        not enabled. The operator switch is an opt-OUT: narrow it to 127.0.0.1
        to make it local-only."""
        assert ControlPlaneSettings().bind_address == "0.0.0.0"  # noqa: S104

    @pytest.mark.tripwire
    def test_it_is_still_ON_by_default(self) -> None:
        """The vacuity control: a reachable bind on a disabled server serves
        nothing, so this guard would be measuring an unreachable default."""
        assert ControlPlaneSettings().enabled is True


class TestABrowserOnARealAddressIsServed:
    """The regression the wildcard bind would have caused, proven through the
    ROUTE rather than through `check_origin` alone — the unit test can pass
    while the handler still passes the wrong arguments."""

    @pytest.mark.tripwire
    async def test_a_browser_on_a_LAN_address_is_not_refused(self) -> None:
        srv = _server()
        res = await srv._handle_login(  # noqa: SLF001
            _Req({"username": "admin", "password": "admin"},
                 Origin="http://192.168.1.50:8787", Host="192.168.1.50:8787")
        )

        assert res.status == 200, (
            "a browser opening the dashboard on a real address was refused — "
            "this is the coupling between the bind literal and the CSRF check"
        )
        assert json.loads(res.text)["token"] == _TOKEN

    @pytest.mark.tripwire
    async def test_a_foreign_page_is_STILL_refused_on_a_wildcard_bind(self) -> None:
        """The widening must not have cost the defence. An attacker page sends
        its own Origin and the browser sets Host to the real target."""
        srv = _server()
        res = await srv._handle_login(  # noqa: SLF001
            _Req({"username": "admin", "password": "admin"},
                 Origin="http://evil.example", Host="192.168.1.50:8787")
        )

        assert res.status == 401


class TestGuessingIsRefused:
    @pytest.mark.tripwire
    async def test_a_source_that_keeps_failing_is_cut_off(self) -> None:
        srv = _server(username="real", password="secret")  # noqa: S106
        wrong = {"username": "real", "password": "no"}

        for _ in range(MAX_FAILURES):
            assert (await srv._handle_login(_Req(wrong))).status == 401  # noqa: SLF001

        blocked = await srv._handle_login(_Req(wrong))  # noqa: SLF001
        assert blocked.status == 429, "guessing was never refused"

        # AND THE RIGHT PASSWORD DOES NOT GET THROUGH EITHER, which is the point:
        # a limiter that opens for a correct guess has not limited anything.
        right = await srv._handle_login(  # noqa: SLF001
            _Req({"username": "real", "password": "secret"})  # noqa: S106
        )
        assert right.status == 429

    @pytest.mark.tripwire
    async def test_ANOTHER_source_is_unaffected(self) -> None:
        """The control. A counter that locks everyone out when one host guesses
        is a denial of service with extra steps."""
        srv = _server(username="real", password="secret")  # noqa: S106
        wrong = {"username": "real", "password": "no"}

        for _ in range(MAX_FAILURES + 1):
            await srv._handle_login(_Req(wrong, remote="10.0.0.9"))  # noqa: SLF001

        res = await srv._handle_login(  # noqa: SLF001
            _Req({"username": "real", "password": "secret"}, remote="10.0.0.10")  # noqa: S106
        )
        assert res.status == 200

    @pytest.mark.tripwire
    async def test_a_successful_sign_in_forgets_the_failures(self) -> None:
        """A person who mistypes eight times and then gets it right must not be
        one typo from a lockout an hour later."""
        srv = _server(username="real", password="secret")  # noqa: S106

        for _ in range(MAX_FAILURES - 1):
            await srv._handle_login(_Req({"username": "real", "password": "no"}))  # noqa: SLF001
        ok = await srv._handle_login(  # noqa: SLF001
            _Req({"username": "real", "password": "secret"})  # noqa: S106
        )
        assert ok.status == 200

        for _ in range(MAX_FAILURES - 1):
            res = await srv._handle_login(  # noqa: SLF001
                _Req({"username": "real", "password": "no"})
            )
        assert res.status == 401, "the counter was not cleared by the success"


class TestTheCounterCannotItselfBecomeTheAttack:
    @pytest.mark.tripwire
    def test_the_tracking_map_is_BOUNDED(self) -> None:
        """Its key is attacker-controlled. An uncapped dict keyed by source
        address is the denial of service this exists to prevent."""
        attempts = LoginAttempts()
        for i in range(MAX_TRACKED_SOURCES + 50):
            attempts.record_failure(f"10.0.{i // 256}.{i % 256}")

        assert attempts.tracked() <= MAX_TRACKED_SOURCES

    @pytest.mark.tripwire
    def test_failures_fall_out_of_the_window(self) -> None:
        """A refusal that never expires is a permanent ban earned by ten typos."""
        attempts = LoginAttempts()
        for _ in range(MAX_FAILURES):
            attempts.record_failure("10.0.0.9", now=1000.0)
        assert attempts.is_refused("10.0.0.9", now=1000.0) is True

        assert attempts.is_refused("10.0.0.9", now=1000.0 + 301.0) is False
