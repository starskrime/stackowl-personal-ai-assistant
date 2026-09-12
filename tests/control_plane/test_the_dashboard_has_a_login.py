"""Signing in with a username and password, from config.

Operator request, 2026-09-12: "Build a login page for that dashboard and let it
get username and password from stackowl config. Default credentials should be
admin/admin."

**IT IS NOT A SECOND WAY TO AUTHENTICATE.** The API routes keep the one bearer
credential they have always had; `POST /api/v1/login` is the HUMAN way to obtain
it, so a person fills a form instead of running a CLI command and pasting a
secret. One authenticator, one `_guard`, one token — "never a second engine"
applied to authority.

THE ASYMMETRY IS THE SECURITY ARGUMENT. Login is the one route that cannot go
through `_guard`, because `_guard` demands the credential login exists to hand
out. It keeps the ORIGIN check and drops only the bearer check — without the
origin check, any page on the internet could POST here from a browser that can
reach loopback and read the token out of the reply. `test_login_still_refuses_a
_foreign_origin` pins that, and the locked-before-it-is-built guard was widened
STRUCTURALLY rather than by name: an unguarded route must either touch no state
at all, or verify a credential itself (origin + a constant-time compare).

AND THE DEFAULT IS ANNOUNCED RATHER THAN ASSUMED SAFE. `admin/admin` ships by
request, and a default credential is only as dangerous as the bind — which is one
setting away from a network, and is what ESC-172 is open about. So the platform
warns at WARNING on boot with the bind beside it, and every successful sign-in
tells the PAGE, because the person who can fix it is the one looking at the
dashboard rather than the one grepping the journal.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stackowl.config.control_plane_settings import ControlPlaneSettings
from stackowl.control_plane.server import ControlPlaneServer

_TOKEN = "t0ken-for-tests-only"


class _Req:
    def __init__(self, body: Any = None, **headers: str) -> None:
        self.headers = dict(headers)
        self._body = body

    async def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _server(**cfg: Any) -> ControlPlaneServer:
    class _Settings:
        control_plane = ControlPlaneSettings(**cfg)

    srv = ControlPlaneServer(_Settings())  # type: ignore[arg-type]
    srv._token = _TOKEN  # noqa: SLF001
    return srv


def _body(res: Any) -> dict[str, Any]:
    return json.loads(res.text)


class TestSigningIn:
    @pytest.mark.tripwire
    async def test_the_default_credentials_are_admin_admin(self) -> None:
        """Shipped ON with a known default, by operator request."""
        res = await _server()._handle_login(  # noqa: SLF001
            _Req({"username": "admin", "password": "admin"})
        )

        assert res.status == 200
        assert _body(res)["token"] == _TOKEN

    @pytest.mark.tripwire
    async def test_credentials_come_from_CONFIG_not_from_a_constant(self) -> None:
        """The vacuity control for the test above: if the route compared against
        a hardcoded pair, changing the config would not change the answer."""
        srv = _server(username="bakir", password="s3cret")

        assert (await srv._handle_login(  # noqa: SLF001
            _Req({"username": "bakir", "password": "s3cret"})
        )).status == 200
        assert (await srv._handle_login(  # noqa: SLF001
            _Req({"username": "admin", "password": "admin"})
        )).status == 401

    @pytest.mark.tripwire
    async def test_a_wrong_password_is_refused_and_hands_out_nothing(self) -> None:
        res = await _server()._handle_login(  # noqa: SLF001
            _Req({"username": "admin", "password": "wrong"})
        )

        assert res.status == 401
        assert _TOKEN not in res.text

    @pytest.mark.tripwire
    async def test_a_wrong_USERNAME_is_refused_the_same_way(self) -> None:
        """Indistinguishable from a wrong password, deliberately — a different
        response would make the endpoint a username oracle."""
        res = await _server()._handle_login(  # noqa: SLF001
            _Req({"username": "nobody", "password": "admin"})
        )

        assert res.status == 401
        assert _body(res) == {"error": "invalid credentials"}

    @pytest.mark.tripwire
    async def test_an_unreadable_body_is_a_400_not_a_crash(self) -> None:
        res = await _server()._handle_login(_Req(None))  # noqa: SLF001

        assert res.status == 400


class TestTheAsymmetryThatMakesItSafe:
    @pytest.mark.tripwire
    async def test_login_still_refuses_a_foreign_origin(self) -> None:
        """The one check login keeps. Without it any page on the internet could
        POST here from a browser that can reach loopback and read the token out
        of the reply."""
        res = await _server()._handle_login(  # noqa: SLF001
            _Req({"username": "admin", "password": "admin"},
                 Origin="http://evil.example", Host="127.0.0.1:8787")
        )

        assert res.status == 401
        assert _TOKEN not in res.text

    @pytest.mark.tripwire
    def test_both_fields_are_compared_in_CONSTANT_TIME_and_both_always(self) -> None:
        """Returning early on an unknown username makes the response time a
        username oracle."""
        import inspect

        from stackowl.control_plane import server as mod

        src = inspect.getsource(mod.ControlPlaneServer._handle_login)
        assert src.count("compare_digest(") == 2, (
            "both the username and the password must be compared in constant time"
        )
        assert "user_ok and pass_ok" in src, (
            "the two comparisons must be combined AFTER both have run, or the "
            "first mismatch short-circuits and leaks which field was wrong"
        )


class TestTheDefaultIsAnnounced:
    @pytest.mark.tripwire
    def test_the_setting_knows_it_is_still_the_default(self) -> None:
        assert ControlPlaneSettings().credentials_are_default is True
        assert ControlPlaneSettings(password="x").credentials_are_default is False
        assert ControlPlaneSettings(username="x").credentials_are_default is False

    @pytest.mark.tripwire
    async def test_a_successful_login_TELLS_THE_PAGE(self) -> None:
        """A warning only an operator who greps the journal can see is the
        DEBUG-evidence failure wearing a different level."""
        res = await _server()._handle_login(  # noqa: SLF001
            _Req({"username": "admin", "password": "admin"})
        )
        assert _body(res)["default_credentials"] is True

        res2 = await _server(username="u", password="p")._handle_login(  # noqa: SLF001
            _Req({"username": "u", "password": "p"})
        )
        assert _body(res2)["default_credentials"] is False

    @pytest.mark.tripwire
    def test_the_boot_warning_names_the_BIND_beside_the_default(self) -> None:
        """A default credential is only as dangerous as the bind. The pair is
        what an operator widening `bind_address` needs to find."""
        import inspect

        from stackowl.control_plane import server as mod

        src = inspect.getsource(mod.ControlPlaneServer._warn_if_default_credentials)
        assert ".warning(" in src, "the default-credential notice is not at WARNING"
        assert "reachable_off_this_machine" in src

    @pytest.mark.tripwire
    def test_the_password_is_masked_wherever_configuration_is_rendered(self) -> None:
        """DEBT-309 shipped one week earlier because `/config list` rendered
        seven credentials. `flatten` takes the LEAF key, so `control_plane.
        password` is caught by name — verified here rather than assumed."""
        from stackowl.commands.config_helpers import flatten

        out: list[tuple[str, str]] = []
        flatten("control_plane", {"password": "hunter2", "username": "admin"}, set(), out)
        rendered = dict(out)

        assert rendered["control_plane.password"] == "***"
        assert rendered["control_plane.username"] == "admin"


class TestThePageAsksForBoth:
    @pytest.mark.tripwire
    def test_the_form_takes_a_username_and_a_password(self) -> None:
        from stackowl.control_plane.page import INDEX_HTML

        assert 'id="username"' in INDEX_HTML
        assert 'id="password"' in INDEX_HTML
        assert 'type="password"' in INDEX_HTML

    @pytest.mark.tripwire
    def test_the_PASSWORD_is_never_put_in_session_storage(self) -> None:
        """Only the token the server hands back is stored. Remembering the
        password would put a reusable credential where any script on this origin
        can read it, to save one typing."""
        import re

        from stackowl.control_plane.page import INDEX_HTML

        stored = re.findall(r"sessionStorage\.setItem\([^)]*\)", INDEX_HTML)
        assert stored, "nothing is stored at all — this guard has gone blind"
        for call in stored:
            assert "password" not in call, f"the password reaches storage: {call}"
