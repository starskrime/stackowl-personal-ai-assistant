"""Signing in to the dashboard — and what proves you own it.

Operator request, 2026-09-12: "Build a login page for that dashboard and let it
get username and password from stackowl config. Default credentials should be
admin/admin." DEBT-310 built it; Q29 is what that default cost.

**THE DEFECT.** The dashboard binds every interface by design (ESC-172). An
`admin/admin` login received the real, permanent bearer token and every data
route served it — the page showed a banner. Anyone on the network owned the
dashboard of every fresh clone. The first fix kept the published password as
proof of ownership, and review showed the first network visitor could still
claim the install, a token still leaked, and a store that could not be read
reopened the race.

**WHAT PROVES OWNERSHIP NOW (L1-L4).** A one-time SETUP CODE, shown on the
platform's terminal and sent to the owner's Telegram. An install with no stored
password is in SETUP: login hands out no token (409), every data route refuses
(403), and only `POST /api/v1/setup` with the code leaves it. The password is a
salted scrypt hash in the secret store and never a setting; an unreadable store
is never read as "no password"; `stackowl control-plane reset-password` on the
host is the way back in, without a restart.

IT IS STILL NOT A SECOND WAY TO AUTHENTICATE. The API routes keep their one bearer
credential; login, setup and the password change are the HUMAN ways to obtain it.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
import shutil
import subprocess
import textwrap
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stackowl.config.control_plane_settings import ControlPlaneSettings
from stackowl.control_plane.server import ControlPlaneServer

_TOKEN = "t0ken-for-tests-only"
_NEW_PASSWORD = "a-long-new-passphrase"  # noqa: S105 — test fixture value


@pytest.fixture(autouse=True)
def dashboard_password() -> None:
    """THIS FILE STARTS IN SETUP MODE — nothing stored.

    `conftest.py` stores a password for every other control-plane test, because
    those test the data and not the gate. Here the gate is the subject.
    """
    return None


class _Req:
    def __init__(self, body: Any = None, *, remote: str = "10.0.0.9", **headers: str) -> None:
        self.headers = dict(headers)
        self.remote = remote
        self._body = body

    async def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _Settings:
    def __init__(self, cp: ControlPlaneSettings, owner: int | None) -> None:
        self.control_plane = cp
        self.telegram_channel = SimpleNamespace(
            allowed_user_ids=frozenset() if owner is None else frozenset({owner})
        )


def _server(*, deliverer: Any = None, owner: int | None = None, **cfg: Any) -> ControlPlaneServer:
    """A server whose token is ALSO in the store, as `ensure_credential` leaves it at boot."""
    from stackowl.config.secret_writer import store_secret
    from stackowl.control_plane.auth import SECRET_SERVICE

    store_secret(SECRET_SERVICE, _TOKEN)
    srv = ControlPlaneServer(
        _Settings(ControlPlaneSettings(**cfg), owner),  # type: ignore[arg-type]
        deliverer=deliverer,
    )
    srv._token = _TOKEN  # noqa: SLF001
    return srv


def _body(res: Any) -> dict[str, Any]:
    return json.loads(res.text)


def _bearer(token: str = _TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _owner() -> Any:
    from stackowl.control_plane.password import ControlPlanePassword

    return ControlPlanePassword()


def _secret_file(service: str) -> Path:
    from stackowl.paths import StackowlHome

    return StackowlHome.secrets_dir() / f"{service}.key"


def _hash_file() -> Path:
    from stackowl.control_plane.password import HASH_SERVICE

    return _secret_file(HASH_SERVICE)


def _code_file() -> Path:
    from stackowl.control_plane.password import CODE_SERVICE

    return _secret_file(CODE_SERVICE)


def _issue_code() -> str:
    return str(_owner().issue_code("platform").display)


def _stored_token() -> str | None:
    from stackowl.control_plane.auth import read_credential

    return read_credential()


async def _login(srv: ControlPlaneServer, username: str, password: str, **kw: Any) -> Any:
    return await srv._handle_login(  # noqa: SLF001
        _Req({"username": username, "password": password}, **kw)
    )


async def _setup(srv: ControlPlaneServer, code: str, new: str = _NEW_PASSWORD, **kw: Any) -> Any:
    return await srv._handle_setup(  # noqa: SLF001
        _Req({"code": code, "new_password": new}, **kw)
    )


async def _change(
    srv: ControlPlaneServer, current: str, new: str, username: str = "admin", **kw: Any,
) -> Any:
    return await srv._handle_password(  # noqa: SLF001
        _Req({"username": username, "current_password": current, "new_password": new}, **kw)
    )


def _full(*_a: Any, **_k: Any) -> None:
    raise OSError("no space left on device")


def _guarded_handlers() -> list[str]:
    """Every route handler that goes through `_guard` — derived, never listed."""
    return sorted(
        name for name in dir(ControlPlaneServer)
        if name.startswith("_handle_")
        and "self._guard(" in inspect.getsource(getattr(ControlPlaneServer, name))
    )


class _Deliverer:
    """Records what the server hands the ProactiveDeliverer."""

    def __init__(
        self, *, status: str = "delivered", raises: bool = False,
        statuses: dict[str, list[str]] | None = None,
    ) -> None:
        self.sent: list[tuple[Any, bool]] = []
        self._status = status
        self._raises = raises
        self._statuses = statuses or {}

    async def deliver(self, notification: Any, *, surface_undelivered: bool = True) -> str:
        self.sent.append((notification, surface_undelivered))
        if self._raises:
            raise RuntimeError("telegram is down")
        queue = self._statuses.get(notification.channel_name)
        return queue.pop(0) if queue else self._status


# --------------------------------------------------------------------------- setup


class TestAFreshInstallHandsOutNothing:
    @pytest.mark.tripwire
    async def test_admin_admin_gets_NO_token_and_a_setup_code_is_issued(self) -> None:
        srv = _server()

        res = await _login(srv, "admin", "admin")

        assert res.status == 409
        assert _body(res) == {"error": "setup_required"}
        assert _TOKEN not in res.text
        await srv._settle_background()  # noqa: SLF001
        assert _owner().current_code() is not None, "setup mode issued no code"

    @pytest.mark.tripwire
    async def test_EVERY_data_route_refuses_in_setup_mode(self) -> None:
        """Derived from the handlers, so a route added later is covered."""
        srv = _server()
        handlers = _guarded_handlers()
        assert len(handlers) >= 8, f"only {handlers} found — this guard has gone blind"

        for name in handlers:
            res = await getattr(srv, name)(_Req(None, **_bearer()))
            assert res.status == 403, f"{name} served data with no password set"
            assert _body(res) == {"error": "setup_required"}, name

    @pytest.mark.tripwire
    async def test_without_a_token_the_refusal_is_still_the_uniform_401(self) -> None:
        from stackowl.control_plane.auth import UNAUTHORIZED_BODY

        res = await _server()._handle_health(_Req(None))  # noqa: SLF001

        assert res.status == 401
        assert res.text == UNAUTHORIZED_BODY

    @pytest.mark.tripwire
    async def test_the_code_reaches_the_owners_TELEGRAM_and_the_TERMINAL(self) -> None:
        deliverer = _Deliverer()
        srv = _server(deliverer=deliverer, owner=4242)

        await srv._ensure_setup_code("login")  # noqa: SLF001

        issued = _owner().current_code()
        assert issued is not None and issued.sent
        assert [(n.channel_name, n.target) for n, _ in deliverer.sent] == [
            ("telegram", 4242), ("cli", None),
        ]
        from stackowl.notifications.deliverer import _UNREMEMBERED_CATEGORIES

        for note, surface in deliverer.sent:
            assert issued.display in note.message
            assert surface is False, "a secret was offered to the undelivered-outbox banner"
            assert note.category in _UNREMEMBERED_CATEGORIES, (
                "the setup code would be written into conversation history, where it "
                "can reach a model provider's prompt while still valid"
            )

    @pytest.mark.tripwire
    async def test_the_deliverer_REMEMBERS_nothing_of_a_setup_code(self) -> None:
        """MEASURED on the live box 2026-09-12: a delivered setup code was found in
        `workspace/stackowl.db`, recorded as something the agent said (ESC-19's
        hook). The category is what keeps it out."""
        from stackowl.notifications.deliverer import SETUP_CODE_CATEGORY, ProactiveDeliverer
        from stackowl.notifications.router import Notification

        recorded: list[str] = []

        class _Store:
            def __getattr__(self, name: str) -> Any:
                async def _record(*_a: Any, **_k: Any) -> None:
                    recorded.append(name)
                return _record

        deliverer = ProactiveDeliverer.__new__(ProactiveDeliverer)
        deliverer._conversation_store = _Store()  # type: ignore[assignment]  # noqa: SLF001

        await deliverer._remember_what_we_said(Notification(  # noqa: SLF001
            message="StackOwl dashboard setup code: ABCD-EFGH-JKMN", urgency="critical",
            category=SETUP_CODE_CATEGORY, channel_name="telegram", target=4242,
        ))
        assert recorded == [], f"the setup code was remembered: {recorded}"

        # The control: the same message under an ordinary category IS remembered,
        # so the empty list above is the category's doing, not a dead fake.
        await deliverer._remember_what_we_said(Notification(  # noqa: SLF001
            message="an ordinary proactive message", urgency="normal",
            category="morning_brief", channel_name="telegram", target=4242,
        ))
        assert recorded, "the fake store records nothing — this test has gone blind"

    @pytest.mark.tripwire
    async def test_a_restart_REUSES_an_unexpired_code_and_does_not_send_it_again(self) -> None:
        first = _Deliverer()
        await _server(deliverer=first, owner=4242)._ensure_setup_code("start")  # noqa: SLF001
        issued = _owner().current_code()
        assert issued is not None and first.sent

        again = _Deliverer()
        await _server(deliverer=again, owner=4242)._ensure_setup_code("start")  # noqa: SLF001

        assert again.sent == [], "a restart sent the code again"
        reused = _owner().current_code()
        assert reused is not None and reused.code == issued.code

    @pytest.mark.tripwire
    async def test_a_delivery_failure_is_LOGGED_and_never_blocks_setup(
        self, capture_logs: list[dict[str, Any]],
    ) -> None:
        srv = _server(deliverer=_Deliverer(raises=True), owner=4242)

        await srv._ensure_setup_code("login")  # noqa: SLF001

        assert any(
            r.get("level") == "ERROR" and "delivery raised" in str(r.get("msg"))
            for r in capture_logs
        )
        issued = _owner().current_code()
        assert issued is not None and not issued.sent, (
            "a code that reached nobody was marked sent, so it would never be retried"
        )
        assert (await _setup(srv, issued.display)).status == 200

    @pytest.mark.tripwire
    async def test_a_code_the_HOST_issued_goes_to_telegram_only(self) -> None:
        """The host CLI already printed it where the operator typed the command."""
        _owner().issue_code("host")
        deliverer = _Deliverer()

        await _server(deliverer=deliverer, owner=4242)._ensure_setup_code("health")  # noqa: SLF001

        assert [n.channel_name for n, _ in deliverer.sent] == ["telegram"]

    @pytest.mark.tripwire
    async def test_a_code_that_reached_NOBODY_is_not_marked_sent_so_the_next_boot_sends_it(
        self,
    ) -> None:
        """MEASURED on the live box 2026-09-12: a core with no deliverer wired marked
        its code sent, and the next boots said "sent earlier, not sent again"."""
        await _server()._ensure_setup_code("login")  # noqa: SLF001 — no deliverer wired
        issued = _owner().current_code()
        assert issued is not None and not issued.sent, "a code nobody received was marked sent"

        deliverer = _Deliverer()
        await _server(deliverer=deliverer, owner=4242)._ensure_setup_code("start")  # noqa: SLF001

        assert [n.channel_name for n, _ in deliverer.sent] == ["telegram", "cli"]
        sent = _owner().current_code()
        assert sent is not None and sent.code == issued.code and sent.sent


class TestSettingTheFirstPassword:
    @pytest.mark.tripwire
    async def test_the_code_stores_a_HASH_rotates_the_token_and_opens_the_data(self) -> None:
        srv = _server()
        code = _issue_code()

        res = await _setup(srv, code)

        assert res.status == 200, res.text
        token = _body(res)["token"]
        assert token and token != _TOKEN, "the token was not rotated"
        assert srv._token == token == _stored_token()  # noqa: SLF001
        opened = await srv._handle_health(_Req(None, **_bearer(token)))  # noqa: SLF001
        assert opened.status == 503 and _body(opened)["wired"] is False, (
            "past the gate, the unwired aggregator answers"
        )
        stored = _hash_file().read_text(encoding="utf-8")
        assert stored.startswith("scrypt$")
        assert _NEW_PASSWORD not in stored, "the password was stored in plaintext"
        assert not _code_file().exists(), "the setup code was not used up"
        assert (await _login(srv, "admin", _NEW_PASSWORD)).status == 200

    @pytest.mark.tripwire
    async def test_the_code_forgives_case_spaces_and_lookalike_letters(self) -> None:
        srv = _server()
        code = _issue_code()
        typed = code.lower().replace("-", " ").replace("0", "o").replace("1", "l")

        assert (await _setup(srv, typed)).status == 200

    @pytest.mark.tripwire
    @pytest.mark.parametrize("how", ["wrong", "expired", "used"])
    async def test_a_wrong_expired_or_used_code_is_a_uniform_401_counted_and_stores_nothing(
        self, how: str,
    ) -> None:
        from stackowl.config.secret_writer import store_located_secret
        from stackowl.control_plane.auth import UNAUTHORIZED_BODY
        from stackowl.control_plane.password import CODE_SERVICE, SetupCode

        srv = _server()
        if how == "wrong":
            _issue_code()
            presented = "0000-0000-0000"
        elif how == "expired":
            fresh = _owner().issue_code("platform")
            store_located_secret(CODE_SERVICE, SetupCode(
                fresh.code, int(time.time()) - 24 * 3600 - 1, "platform", True,
            ).serialise())
            presented = fresh.display
        else:
            presented = _issue_code()
            assert (await _setup(srv, presented)).status == 200
            _owner().clear()
            srv = _server()

        res = await _setup(srv, presented, new="another-long-passphrase")

        assert res.status == 401
        assert res.text == UNAUTHORIZED_BODY
        assert srv._login_attempts.tracked() == 1, "the attempt was not counted"  # noqa: SLF001
        assert not _hash_file().exists()

    @pytest.mark.tripwire
    async def test_guessing_the_code_is_BRAKED(self) -> None:
        from stackowl.control_plane.login_guard import MAX_FAILURES

        srv = _server()
        code = _issue_code()
        for _ in range(MAX_FAILURES):
            assert (await _setup(srv, "0000-0000-0000")).status == 401

        assert (await _setup(srv, code)).status == 429, "a correct guess got through the brake"

    @pytest.mark.tripwire
    @pytest.mark.parametrize("weak", ["short-pw", "admin"])
    async def test_a_weak_password_is_400_and_does_NOT_use_the_code(self, weak: str) -> None:
        srv = _server()
        code = _issue_code()

        res = await _setup(srv, code, new=weak)

        assert res.status == 400
        assert _body(res)["error"] == "weak_password"
        assert _owner().current_code() is not None, "a refused password used the code up"
        assert not _hash_file().exists()
        assert (await _setup(srv, code)).status == 200, "the owner could not try again"

    @pytest.mark.tripwire
    async def test_the_USERNAME_is_not_a_password(self) -> None:
        srv = _server(username="operator-longname")

        res = await _setup(srv, _issue_code(), new="Operator-Longname")

        assert res.status == 400
        assert _body(res)["error"] == "weak_password"

    @pytest.mark.tripwire
    async def test_setup_refuses_a_foreign_origin(self) -> None:
        srv = _server()

        res = await _setup(srv, _issue_code(), Origin="http://evil.example", Host="127.0.0.1:8787")

        assert res.status == 401
        assert not _hash_file().exists()

    @pytest.mark.tripwire
    async def test_a_field_that_is_not_a_string_is_a_400(self) -> None:
        """A crafted client's number must not be stored as its Python repr (EC10)."""
        srv = _server()
        _issue_code()

        res = await srv._handle_setup(_Req({"code": 123, "new_password": ["x"]}))  # noqa: SLF001

        assert res.status == 400
        assert not _hash_file().exists()

    @pytest.mark.tripwire
    async def test_a_READY_install_refuses_setup_even_with_a_stored_code(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        """A reset racing a password change in another process can leave both a
        hash and a code. The code must not reopen the first-claim race."""
        owner_password = set_dashboard_password()
        srv = _server()

        res = await _setup(srv, _issue_code(), new="an-attackers-passphrase")

        assert res.status == 401
        assert (await _login(srv, "admin", owner_password)).status == 200

    @pytest.mark.tripwire
    async def test_two_setups_at_once_exactly_ONE_wins(self) -> None:
        srv = _server()
        code = _issue_code()

        first, second = await asyncio.gather(
            _setup(srv, code, new="the-first-passphrase"),
            _setup(srv, code, new="the-second-passphrase"),
        )

        assert sorted([first.status, second.status]) == [200, 401]
        winner = first if first.status == 200 else second
        won_with = "the-first-passphrase" if winner is first else "the-second-passphrase"
        assert srv._token == _body(winner)["token"] == _stored_token()  # noqa: SLF001
        assert (await _login(srv, "admin", won_with)).status == 200

    @pytest.mark.tripwire
    async def test_a_hash_store_that_cannot_write_CHANGES_NOTHING(
        self, monkeypatch: pytest.MonkeyPatch, capture_logs: list[dict[str, Any]],
    ) -> None:
        from stackowl.control_plane import password as password_mod

        srv = _server()
        code = _issue_code()
        monkeypatch.setattr(password_mod, "store_located_secret", _full)

        res = await _setup(srv, code)

        assert res.status == 500
        assert _body(res)["error"] == "password_store_unavailable"
        assert srv._token == _TOKEN and _stored_token() == _TOKEN  # noqa: SLF001
        assert not _hash_file().exists()
        assert _owner().code_matches(code), "a failed setup used the code up"
        assert any(
            r.get("level") == "ERROR" and "server.setup" in str(r.get("msg"))
            for r in capture_logs
        ), "the failed write was not logged loudly"

    @pytest.mark.tripwire
    async def test_a_failure_AFTER_the_hash_puts_the_hash_and_the_token_back(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.control_plane.password import (
            ControlPlanePassword,
            PasswordStoreUnavailable,
        )

        srv = _server()
        code = _issue_code()

        def _cannot(_self: Any) -> None:
            raise PasswordStoreUnavailable("the store went away")

        monkeypatch.setattr(ControlPlanePassword, "consume_code", _cannot)

        res = await _setup(srv, code)

        assert res.status == 500
        assert not _hash_file().exists(), "the hash written before the failure was left behind"
        assert srv._token == _TOKEN and _stored_token() == _TOKEN  # noqa: SLF001
        assert _owner().code_matches(code)

    @pytest.mark.tripwire
    async def test_a_token_store_that_cannot_write_CHANGES_NOTHING(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.control_plane import auth as auth_mod

        srv = _server()
        code = _issue_code()
        monkeypatch.setattr(auth_mod, "store_secret", _full)

        res = await _setup(srv, code)

        assert res.status == 500
        assert srv._token == _TOKEN  # noqa: SLF001
        assert not _hash_file().exists()
        assert _owner().code_matches(code)


# ------------------------------------------------------------------------ sign-in


class TestSigningIn:
    @pytest.mark.tripwire
    async def test_the_owners_password_signs_in(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        owner_password = set_dashboard_password()

        res = await _login(_server(), "admin", owner_password)

        assert res.status == 200
        assert _body(res) == {"token": _TOKEN}

    @pytest.mark.tripwire
    async def test_admin_admin_NEVER_signs_in(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        set_dashboard_password()

        assert (await _login(_server(), "admin", "admin")).status == 401

    @pytest.mark.tripwire
    async def test_a_wrong_password_is_refused_counted_and_hands_out_nothing(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        set_dashboard_password()
        srv = _server()

        res = await _login(srv, "admin", "wrong-password")

        assert res.status == 401
        assert _body(res) == {"error": "invalid credentials"}
        assert _TOKEN not in res.text
        assert srv._login_attempts.tracked() == 1  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_a_wrong_USERNAME_is_refused_the_same_way(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        """Indistinguishable from a wrong password — a different answer would make
        the endpoint a username oracle."""
        owner_password = set_dashboard_password()

        res = await _login(_server(), "nobody", owner_password)

        assert res.status == 401
        assert _body(res) == {"error": "invalid credentials"}

    @pytest.mark.tripwire
    async def test_the_username_comes_from_CONFIG(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        owner_password = set_dashboard_password()
        srv = _server(username="bakir")

        assert (await _login(srv, "bakir", owner_password)).status == 200
        assert (await _login(srv, "admin", owner_password)).status == 401

    @pytest.mark.tripwire
    async def test_an_unreadable_body_is_a_400_not_a_crash(self) -> None:
        assert (await _server()._handle_login(_Req(None))).status == 400  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_an_ARRAY_body_is_a_400_not_a_500(self) -> None:
        res = await _server()._handle_login(_Req(["admin", "admin"]))  # noqa: SLF001

        assert res.status == 400

    @pytest.mark.tripwire
    async def test_NON_ASCII_credentials_set_and_sign_in(self) -> None:
        """`compare_digest` raises on a non-ASCII str, which once made an accented
        username a 500 instead of a refusal."""
        srv = _server(username="bákir")
        accented = "pässwörd-ünïcode-λ"

        assert (await _setup(srv, _issue_code(), new=accented)).status == 200
        assert (await _login(srv, "bákir", accented)).status == 200
        assert (await _login(srv, "bakir", accented)).status == 401
        assert (await _login(srv, "bákir", "passwörd-ünïcode-λ")).status == 401

    @pytest.mark.tripwire
    async def test_login_still_refuses_a_foreign_origin(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        """Without it any page on the internet could POST here from a browser that
        can reach the dashboard and read the token out of the reply."""
        owner_password = set_dashboard_password()

        res = await _login(
            _server(), "admin", owner_password,
            Origin="http://evil.example", Host="127.0.0.1:8787",
        )

        assert res.status == 401
        assert _TOKEN not in res.text

    @pytest.mark.tripwire
    async def test_the_attempt_is_counted_BEFORE_the_hash_is_derived(
        self, set_dashboard_password: Callable[..., str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.control_plane.password import PasswordLookup

        set_dashboard_password()
        srv = _server()
        seen: list[int] = []
        real = PasswordLookup.matches

        def _spy(self: PasswordLookup, candidate: str) -> bool:
            seen.append(srv._login_attempts.tracked())  # noqa: SLF001
            return real(self, candidate)

        monkeypatch.setattr(PasswordLookup, "matches", _spy)

        await _login(srv, "admin", "wrong-password")

        assert seen == [1], f"the hash was derived before the attempt was counted: {seen}"

    @pytest.mark.tripwire
    async def test_guesses_in_PARALLEL_cannot_outrun_the_brake(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        from stackowl.control_plane.login_guard import MAX_FAILURES

        set_dashboard_password()
        srv = _server()

        results = await asyncio.gather(
            *(_login(srv, "admin", f"guess-number-{i}") for i in range(MAX_FAILURES + 5))
        )

        assert sum(1 for r in results if r.status == 429) == 5, [r.status for r in results]

    @pytest.mark.tripwire
    def test_ONE_proof_BOTH_halves_and_the_count_comes_FIRST(self) -> None:
        """Login and the password change share one proof (BH7), and a route may
        skip `_guard` only by calling it or the setup-code proof (BH6)."""
        from stackowl.control_plane.password import SetupCode, _StoredHash

        proof = inspect.getsource(ControlPlaneServer._prove_owner)  # noqa: SLF001
        assert "compare_digest(" in proof and ".matches" in proof
        assert "user_ok and pass_ok" in proof, (
            "the halves must be combined AFTER both have run, or the first mismatch "
            "short-circuits and leaks which field was wrong"
        )
        assert proof.index("record_failure(") < proof.index("to_thread("), (
            "the attempt is counted after the hash is derived"
        )
        for handler in ("_handle_login", "_handle_password"):
            src = inspect.getsource(getattr(ControlPlaneServer, handler))
            assert "self._prove_owner(" in src, f"{handler} has its own copy of the proof"
        setup = inspect.getsource(ControlPlaneServer._prove_setup_code)  # noqa: SLF001
        assert setup.rindex("record_failure(") < setup.index("stored.matches("), (
            "the code attempt is counted after it is compared"
        )
        assert setup.index("current_code") < setup.rindex("record_failure("), (
            "the store is read after the count, so a store error brakes the owner"
        )
        assert "compare_digest(" in inspect.getsource(_StoredHash.matches)
        assert "compare_digest(" in inspect.getsource(SetupCode.matches)


# ---------------------------------------------------------------- unreadable store


class TestAnUnreadableStoreIsNeverNoPassword:
    @pytest.mark.tripwire
    async def test_a_LOCKED_keyring_holding_the_password_is_503_and_heals_without_a_restart(
        self, set_dashboard_password: Callable[..., str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import keyring
        import keyring.errors

        from stackowl.control_plane.password import (
            HASH_SERVICE,
            ControlPlanePassword,
            PasswordStoreHealth,
        )

        owner_password = set_dashboard_password()
        record = _hash_file().read_text(encoding="utf-8")
        # The record lives in the keyring; the file is its locator.
        _hash_file().write_text(f"keychain:{HASH_SERVICE}", encoding="utf-8")
        locked = {"on": True}

        def _get(service: str, _user: str) -> str | None:
            if locked["on"]:
                raise keyring.errors.KeyringLocked("Failed to unlock the collection!")
            return record if service == HASH_SERVICE else None

        monkeypatch.setattr(keyring, "get_password", _get)
        srv = _server()

        login = await _login(srv, "admin", owner_password)
        assert login.status == 503
        assert _body(login)["error"] == "password_store_unavailable"
        assert _body(login)["remedy"]
        assert _TOKEN not in login.text
        data = await srv._handle_health(_Req(None, **_bearer()))  # noqa: SLF001
        assert data.status == 503
        assert _body(data)["error"] == "password_store_unavailable"
        health = await PasswordStoreHealth(ControlPlanePassword()).health_check()
        assert health.status == "down" and health.remedy

        locked["on"] = False
        assert (await _login(srv, "admin", owner_password)).status == 200, (
            "the next request did not read the store again"
        )

    @pytest.mark.tripwire
    async def test_an_unreadable_FILE_is_503_too(self) -> None:
        _hash_file().parent.mkdir(parents=True, exist_ok=True)
        _hash_file().mkdir()  # a directory where the record should be

        assert (await _login(_server(), "admin", "admin")).status == 503

    @pytest.mark.tripwire
    async def test_a_keyring_holding_NOTHING_of_ours_is_setup_not_unavailable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """THIS BOX. SecretService raises `KeyringLocked` on every call here, and
        nothing was ever stored in it. Reading that as "unavailable" would lock the
        install out for good; the locator is what tells the two apart."""
        import keyring
        import keyring.errors

        def _locked(*_a: Any, **_k: Any) -> None:
            raise keyring.errors.KeyringLocked("Failed to unlock the collection!")

        monkeypatch.setattr(keyring, "get_password", _locked)
        monkeypatch.setattr(keyring, "set_password", _locked)
        srv = _server()

        assert (await _login(srv, "admin", "admin")).status == 409
        await srv._settle_background()  # noqa: SLF001 — the refusal's own code issue
        issued = _owner().current_code()
        assert issued is not None, "no code was issued on a keyring that is always locked"
        assert (await _setup(srv, issued.display)).status == 200
        assert (await _login(srv, "admin", _NEW_PASSWORD)).status == 200

    @pytest.mark.tripwire
    async def test_a_DAMAGED_record_is_reported_then_the_install_is_in_setup(
        self, capture_logs: list[dict[str, Any]],
    ) -> None:
        from stackowl.control_plane.password import ControlPlanePassword, PasswordStoreHealth

        _hash_file().parent.mkdir(parents=True, exist_ok=True)
        _hash_file().write_text("scrypt$not-a-record", encoding="utf-8")
        srv = _server()

        assert (await _login(srv, "admin", "admin")).status == 409
        assert any(
            r.get("level") == "ERROR" and "damaged" in str(r.get("msg")) for r in capture_logs
        )
        health = await PasswordStoreHealth(ControlPlanePassword()).health_check()
        assert health.status == "down" and health.remedy
        # The code the refusal itself issued — a second writer issuing one directly
        # while that write is in flight is a race the store rightly refuses.
        await srv._settle_background()  # noqa: SLF001
        issued = _owner().current_code()
        assert issued is not None, "a damaged record left the install with no setup code"
        assert (await _setup(srv, issued.display)).status == 200
        assert _owner().lookup().state == "ready"


# ----------------------------------------------------------------------- change


class TestChangingThePassword:
    @pytest.mark.tripwire
    async def test_a_change_rotates_the_token_and_the_OLD_one_is_refused(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        from stackowl.control_plane.auth import UNAUTHORIZED_BODY

        owner_password = set_dashboard_password()
        srv = _server()

        res = await _change(srv, owner_password, _NEW_PASSWORD)

        assert res.status == 200, res.text
        token = _body(res)["token"]
        assert token != _TOKEN and _stored_token() == token
        old = await srv._handle_health(_Req(None, **_bearer(_TOKEN)))  # noqa: SLF001
        assert old.status == 401 and old.text == UNAUTHORIZED_BODY
        assert (await srv._handle_health(_Req(None, **_bearer(token)))).status == 503  # noqa: SLF001
        assert (await _login(srv, "admin", _NEW_PASSWORD)).status == 200
        assert (await _login(srv, "admin", owner_password)).status == 401

    @pytest.mark.tripwire
    async def test_a_wrong_current_password_is_a_uniform_401_counted_and_changes_nothing(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        from stackowl.control_plane.auth import UNAUTHORIZED_BODY

        owner_password = set_dashboard_password()
        srv = _server()

        res = await _change(srv, "not-the-password", _NEW_PASSWORD)

        assert res.status == 401 and res.text == UNAUTHORIZED_BODY
        assert srv._login_attempts.tracked() == 1  # noqa: SLF001
        assert srv._token == _TOKEN  # noqa: SLF001
        assert (await _login(srv, "admin", owner_password)).status == 200

    @pytest.mark.tripwire
    async def test_there_is_nothing_to_change_in_setup_mode(self) -> None:
        res = await _change(_server(), "admin", _NEW_PASSWORD)

        assert res.status == 409
        assert _body(res) == {"error": "setup_required"}

    @pytest.mark.tripwire
    async def test_a_weak_new_password_changes_nothing(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        owner_password = set_dashboard_password()
        srv = _server()

        res = await _change(srv, owner_password, "short")

        assert res.status == 400 and _body(res)["error"] == "weak_password"
        assert srv._token == _TOKEN  # noqa: SLF001
        assert (await _login(srv, "admin", owner_password)).status == 200

    @pytest.mark.tripwire
    async def test_two_changes_at_once_exactly_ONE_wins(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        owner_password = set_dashboard_password()
        srv = _server()

        first, second = await asyncio.gather(
            _change(srv, owner_password, "the-first-passphrase"),
            _change(srv, owner_password, "the-second-passphrase"),
        )

        assert sorted([first.status, second.status]) == [200, 401]
        winner = first if first.status == 200 else second
        won_with = "the-first-passphrase" if winner is first else "the-second-passphrase"
        assert srv._token == _body(winner)["token"] == _stored_token()  # noqa: SLF001
        assert (await _login(srv, "admin", won_with)).status == 200

    @pytest.mark.tripwire
    async def test_a_hash_store_that_cannot_write_CHANGES_NOTHING(
        self, set_dashboard_password: Callable[..., str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.control_plane import password as password_mod

        owner_password = set_dashboard_password()
        srv = _server()
        real = password_mod.store_located_secret
        monkeypatch.setattr(password_mod, "store_located_secret", _full)

        res = await _change(srv, owner_password, _NEW_PASSWORD)

        monkeypatch.setattr(password_mod, "store_located_secret", real)
        assert res.status == 500
        assert srv._token == _TOKEN and _stored_token() == _TOKEN  # noqa: SLF001
        assert (await _login(srv, "admin", owner_password)).status == 200


class TestTheTokenRotation:
    @pytest.mark.tripwire
    def test_a_read_back_mismatch_puts_the_previous_token_back_and_NEVER_mints_a_third(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import store_secret
        from stackowl.control_plane import auth as auth_mod

        store_secret(auth_mod.SECRET_SERVICE, _TOKEN)
        writes: list[str] = []
        real_store = auth_mod.store_secret
        real_read = auth_mod.read_credential
        reads = {"n": 0}

        def _counting(service: str, value: str) -> tuple[str, str]:
            writes.append(service)
            return real_store(service, value)

        def _stale_second() -> str | None:
            reads["n"] += 1
            return "a-stale-keyring-token" if reads["n"] == 2 else real_read()

        monkeypatch.setattr(auth_mod, "store_secret", _counting)
        monkeypatch.setattr(auth_mod, "read_credential", _stale_second)

        with pytest.raises(auth_mod.CredentialUnavailable):
            auth_mod.rotate_credential()

        assert real_read() == _TOKEN, "the rotated token was left in the store"
        assert len(writes) == 2, f"expected the write and its undo, got {writes}"
        rotate_src = textwrap.dedent(inspect.getsource(auth_mod.rotate_credential))
        called = {
            n.func.id for n in ast.walk(ast.parse(rotate_src))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "ensure_credential" not in called, (
            "the read-back goes through the MINTING lookup"
        )


# ------------------------------------------------------------------------ host


class TestTheHostCanReset:
    @pytest.mark.tripwire
    async def test_reset_prints_a_code_and_the_NEXT_request_is_setup_without_a_restart(
        self, set_dashboard_password: Callable[..., str],
    ) -> None:
        from typer.testing import CliRunner

        from stackowl.cli.app import app

        owner_password = set_dashboard_password()
        srv = _server()
        assert (await _login(srv, "admin", owner_password)).status == 200

        out = CliRunner().invoke(app, ["control-plane", "reset-password"])

        assert out.exit_code == 0, out.output
        match = re.search(r"Setup code: ([0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4})", out.output)
        assert match, out.output
        assert _stored_token() != _TOKEN, "the reset did not rotate the token"
        assert (await _login(srv, "admin", owner_password)).status == 409
        # THE OLD TOKEN IS DEAD AT ONCE: the gate reads the stored token per
        # request, so the host's rotation lands without a restart.
        stale = await srv._handle_health(_Req(None, **_bearer()))  # noqa: SLF001
        assert stale.status == 401, "the token from before the reset still opened the gate"
        rotated = _stored_token()
        assert rotated is not None
        refused = await srv._handle_health(_Req(None, **_bearer(rotated)))  # noqa: SLF001
        assert refused.status == 403 and _body(refused) == {"error": "setup_required"}
        assert (await _setup(srv, match.group(1))).status == 200

    @pytest.mark.tripwire
    def test_a_store_failure_exits_1_with_a_remedy_and_CHANGES_NOTHING(
        self, set_dashboard_password: Callable[..., str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from typer.testing import CliRunner

        from stackowl.cli.app import app
        from stackowl.config.secret_writer import store_secret
        from stackowl.control_plane import auth as auth_mod

        set_dashboard_password()
        store_secret(auth_mod.SECRET_SERVICE, _TOKEN)
        record = _hash_file().read_text(encoding="utf-8")
        monkeypatch.setattr(auth_mod, "store_secret", _full)

        out = CliRunner().invoke(app, ["control-plane", "reset-password"])

        assert out.exit_code == 1
        assert "Remedy" in out.output
        assert _hash_file().read_text(encoding="utf-8") == record
        assert not _code_file().exists()
        assert _stored_token() == _TOKEN

    @pytest.mark.tripwire
    def test_the_BARE_command_still_prints_what_it_printed(self) -> None:
        from typer.testing import CliRunner

        from stackowl.cli.app import app
        from stackowl.config.secret_writer import store_secret
        from stackowl.control_plane.auth import SECRET_SERVICE

        store_secret(SECRET_SERVICE, "printed-token")

        out = CliRunner().invoke(app, ["control-plane"])

        assert out.exit_code == 0, out.output
        lines = out.output.splitlines()
        expected = [
            "Control plane: http://0.0.0.0:8787",
            "  Token (file): printed-token",
            '  curl -H "Authorization: Bearer $TOKEN" http://0.0.0.0:8787/api/v1/health',
        ]
        positions = [lines.index(line) for line in expected]
        assert positions == sorted(positions), out.output
        # THE TWO NOTES (Q29): a setup-mode curl answers 403, and a password change
        # replaces this token.
        assert any(line.startswith("  Setup mode:") for line in lines), out.output
        assert "  Setting or changing the dashboard password rotates this token." in lines


# ------------------------------------------------------------------------ logs


class TestNothingSecretIsLogged:
    @pytest.mark.tripwire
    async def test_no_code_password_hash_or_token_reaches_a_log_record(
        self, capture_logs: list[dict[str, Any]],
    ) -> None:
        srv = _server(deliverer=_Deliverer(), owner=4242)
        await srv._ensure_setup_code("login")  # noqa: SLF001
        issued = _owner().current_code()
        assert issued is not None

        token = _body(await _setup(srv, issued.display))["token"]
        record = _hash_file().read_text(encoding="utf-8")
        assert (await _change(srv, _NEW_PASSWORD, "yet-another-passphrase")).status == 200

        blob = json.dumps(capture_logs, default=str)
        assert capture_logs, "nothing was logged at all — this guard has gone blind"
        for secret in (issued.code, issued.display, _NEW_PASSWORD,
                       "yet-another-passphrase", record, token):
            assert secret not in blob, "a credential reached a log record"


# ----------------------------------------------------------------------- settings


class TestThePasswordIsNotASetting:
    @pytest.mark.tripwire
    def test_there_is_no_password_field_and_no_default_credential(self) -> None:
        from pydantic import ValidationError

        assert "password" not in ControlPlaneSettings.model_fields
        assert not hasattr(ControlPlaneSettings, "credentials_are_default")
        with pytest.raises(ValidationError):
            ControlPlaneSettings(password="admin")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- page


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
        from stackowl.control_plane.page import INDEX_HTML

        stored = re.findall(r"sessionStorage\.setItem\([^)]*\)", INDEX_HTML)
        assert stored, "nothing is stored at all — this guard has gone blind"
        for call in stored:
            assert "password" not in call.lower() and "pw" not in call, (
                f"a password reaches storage: {call}"
            )


_KEY = "stackowl.control.token"
_EMPTY = {"subsystems": [], "schedules": [], "settings": [], "owls": [], "tasks": [],
          "agents": [], "lessons": [], "curated": [], "edges": [], "wired": True}

_PAGE_HARNESS = r"""
const fs = require("fs");
const SCEN = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const KNOWN = new Set(SCEN.ids);
function mkEl(id) {
  return {
    id, hidden: false, textContent: "", className: "", value: "",
    children: [], _listeners: {},
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(k, f) { this._listeners[k] = f; },
  };
}
const els = {};
global.document = {
  getElementById: (id) => (KNOWN.has(id) ? (els[id] = els[id] || mkEl(id)) : null),
  createElement: (t) => mkEl("<" + t + ">"),
};
global.location = { origin: "http://127.0.0.1:8787" };
const store = Object.assign({}, SCEN.session);
global.sessionStorage = {
  getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
  setItem(k, v) { store[k] = String(v); },
  removeItem(k) { delete store[k]; },
};
const calls = [];
global.fetch = (path, opts) => {
  const method = (opts && opts.method) || "GET";
  calls.push({
    path, method,
    body: opts && opts.body ? JSON.parse(opts.body) : null,
    auth: opts && opts.headers && opts.headers.Authorization ? opts.headers.Authorization : null,
  });
  const route = SCEN.routes[method + " " + path] || SCEN.routes[method + " *"];
  const text = typeof route.body === "string" ? route.body : JSON.stringify(route.body);
  return Promise.resolve({
    status: route.status, ok: route.status >= 200 && route.status < 300,
    json: () => new Promise((ok, bad) => { try { ok(JSON.parse(text)); } catch (e) { bad(e); } }),
    text: () => Promise.resolve(text),
  });
};
eval(fs.readFileSync(process.argv[2], "utf8"));
const settle = () => new Promise((r) => setTimeout(r, 40));
(async () => {
  await settle();
  for (const step of SCEN.steps) {
    for (const [id, v] of Object.entries(step.set || {})) { document.getElementById(id).value = v; }
    if (step.submit) { document.getElementById(step.submit)._listeners.submit({ preventDefault() {} }); }
    if (step.click) { document.getElementById(step.click)._listeners.click(); }
    await settle();
  }
  const hidden = {};
  for (const id of SCEN.watch) { const n = document.getElementById(id); hidden[id] = n ? n.hidden : null; }
  console.log(JSON.stringify({ calls, session: store, status: els.status.textContent, hidden }));
})();
"""


def _run_page(
    tmp_path: Path, *, routes: dict[str, Any], steps: list[dict[str, Any]] | None = None,
    session: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute the REAL page script under a stub DOM whose ids come from the markup."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover — present on this box and in CI
        pytest.skip("no node on PATH; the page cannot be executed here")
    from stackowl.control_plane.page import INDEX_HTML

    match = re.search(r"<script>(.*?)</script>", INDEX_HTML, re.S)
    assert match, "the page has no <script> block — this guard has gone blind"
    markup = re.sub(r"<script>.*?</script>", "", INDEX_HTML, flags=re.S)
    markup = re.sub(r"<style>.*?</style>", "", markup, flags=re.S)
    scenario = {
        "ids": sorted(set(re.findall(r'id="(\w+)"', markup))),
        "routes": routes, "steps": steps or [], "session": session or {},
        "watch": ["setup", "change", "health", "rail"],
    }
    (tmp_path / "page.js").write_text(match.group(1), encoding="utf-8")
    (tmp_path / "harness.js").write_text(textwrap.dedent(_PAGE_HARNESS), encoding="utf-8")
    (tmp_path / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    proc = subprocess.run(
        [node, str(tmp_path / "harness.js"), str(tmp_path / "page.js"),
         str(tmp_path / "scenario.json")],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert proc.returncode == 0, f"the page threw: {proc.stderr[-2000:]}"
    return dict(json.loads(proc.stdout.strip().splitlines()[-1]))


def _posts(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in out["calls"] if c["method"] == "POST"]


class TestThePageDrivesEveryState:
    @pytest.mark.tripwire
    def test_SETUP_posts_the_code_and_the_password_and_keeps_only_the_token(
        self, tmp_path: Path,
    ) -> None:
        accented = "pässwörd-ünïcode"
        out = _run_page(tmp_path, routes={
            "POST /api/v1/setup": {"status": 200, "body": {"token": "tok-after-setup"}},
            "GET *": {"status": 200, "body": _EMPTY},
        }, steps=[{
            "set": {"setupcode": "ABCD-EFGH-JKMN", "setuppw": accented, "setuppw2": accented},
            "submit": "setupform",
        }])

        assert [(c["path"], c["body"]) for c in _posts(out)] == [
            ("/api/v1/setup", {"code": "ABCD-EFGH-JKMN", "new_password": accented}),
        ]
        assert out["session"] == {_KEY: "tok-after-setup"}
        gets = [c for c in out["calls"] if c["method"] == "GET"]
        assert gets and all(c["auth"] == "Bearer tok-after-setup" for c in gets), (
            "the page did not load the platform with the token setup returned"
        )
        assert out["hidden"]["setup"] is True

    @pytest.mark.tripwire
    def test_mismatched_passwords_are_caught_BEFORE_anything_is_posted(
        self, tmp_path: Path,
    ) -> None:
        out = _run_page(tmp_path, routes={"GET *": {"status": 200, "body": _EMPTY}}, steps=[{
            "set": {"setupcode": "ABCD-EFGH-JKMN", "setuppw": "one-long-passphrase",
                    "setuppw2": "another-long-passphrase"},
            "submit": "setupform",
        }])

        assert _posts(out) == []
        assert "do not match" in out["status"]

    @pytest.mark.tripwire
    def test_CHANGE_posts_the_right_keys_and_stores_the_returned_token(
        self, tmp_path: Path,
    ) -> None:
        out = _run_page(tmp_path, session={_KEY: "tok-before"}, routes={
            "POST /api/v1/password": {"status": 200, "body": {"token": "tok-after-change"}},
            "GET *": {"status": 200, "body": _EMPTY},
        }, steps=[
            {"click": "changetoggle"},
            {"set": {"changeuser": "owner", "changecurrent": "the-old-passphrase",
                     "changepw": "the-new-passphrase", "changepw2": "the-new-passphrase"},
             "submit": "changeform"},
        ])

        assert [(c["path"], c["body"]) for c in _posts(out)] == [
            ("/api/v1/password", {"username": "owner", "current_password": "the-old-passphrase",
                                  "new_password": "the-new-passphrase"}),
        ]
        assert out["session"] == {_KEY: "tok-after-change"}
        assert out["hidden"]["change"] is True

    @pytest.mark.tripwire
    def test_a_login_answered_409_opens_the_SETUP_form(self, tmp_path: Path) -> None:
        out = _run_page(tmp_path, routes={
            "POST /api/v1/login": {"status": 409, "body": {"error": "setup_required"}},
            "GET *": {"status": 200, "body": _EMPTY},
        }, steps=[{"set": {"username": "admin", "password": "admin"}, "submit": "loginform"}])

        assert out["hidden"]["setup"] is False
        assert "setup code" in out["status"]
        assert out["session"] == {}

    @pytest.mark.tripwire
    def test_a_STALE_token_is_forgotten_and_sign_in_is_shown(self, tmp_path: Path) -> None:
        """A token rotated in another tab used to dead-end at `HTTP 401` (BH12)."""
        out = _run_page(tmp_path, session={_KEY: "tok-rotated-elsewhere"}, routes={
            "GET *": {"status": 401, "body": "unauthorized"},
        })

        assert out["session"] == {}
        assert "sign in again" in out["status"]
        assert out["hidden"]["health"] is True and out["hidden"]["rail"] is True

    @pytest.mark.tripwire
    def test_a_setup_required_data_answer_opens_the_setup_form(self, tmp_path: Path) -> None:
        out = _run_page(tmp_path, session={_KEY: "tok-from-before-a-reset"}, routes={
            "GET *": {"status": 403, "body": {"error": "setup_required"}},
        })

        assert out["session"] == {}
        assert out["hidden"]["setup"] is False

    @pytest.mark.tripwire
    def test_an_unreadable_store_shows_the_REMEDY_and_keeps_the_session(
        self, tmp_path: Path,
    ) -> None:
        out = _run_page(tmp_path, session={_KEY: "tok"}, routes={
            "GET *": {"status": 503, "body": {"error": "password_store_unavailable",
                                              "remedy": "unlock the keyring"}},
        })

        assert "unlock the keyring" in out["status"]
        assert out["session"] == {_KEY: "tok"}

    @pytest.mark.tripwire
    def test_a_503_WITHOUT_the_wired_marker_is_an_error_not_an_empty_panel(
        self, tmp_path: Path,
    ) -> None:
        """A proxy's error page or a crash answers 503 too; only a route's designed
        `wired: false` is a panel with nothing wired behind it."""
        out = _run_page(tmp_path, session={_KEY: "tok"}, routes={
            "GET *": {"status": 503, "body": "<html>Service Unavailable</html>"},
        })

        assert "HTTP 503" in out["status"], out
        assert out["hidden"]["health"] is True


# ------------------------------------------------------------------- delivery


class TestDeliveryIsHonest:
    @pytest.mark.tripwire
    async def test_a_HEADLESS_terminal_is_undeliverable_not_delivered(self) -> None:
        """The headless `cli` adapter drops what it is handed. Counting that as
        delivered marked a setup code sent that nobody could see."""
        from stackowl.channels.cli_adapter import HeadlessCliAdapter
        from stackowl.notifications.deliverer import ProactiveDeliverer

        adapter = HeadlessCliAdapter()
        deliverer = ProactiveDeliverer.__new__(ProactiveDeliverer)
        deliverer._registry = SimpleNamespace(get=lambda _channel: adapter)  # type: ignore[assignment]  # noqa: SLF001

        status = await deliverer._transport("cli", "a message nobody can read")  # noqa: SLF001

        assert status == "failed"
        assert adapter.dropped == []

    @pytest.mark.tripwire
    async def test_with_no_telegram_owner_a_FAILED_terminal_copy_is_not_marked_sent(
        self,
    ) -> None:
        deliverer = _Deliverer(status="failed")

        await _server(deliverer=deliverer)._ensure_setup_code("login")  # noqa: SLF001

        assert [n.channel_name for n, _ in deliverer.sent] == ["cli"]
        issued = _owner().current_code()
        assert issued is not None and not issued.sent, "a code nobody saw was marked sent"

    @pytest.mark.tripwire
    async def test_a_setup_code_is_NEVER_rerouted_to_the_fallback_channel(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.notifications.deliverer import SETUP_CODE_CATEGORY, ProactiveDeliverer
        from stackowl.notifications.router import Notification

        deliverer = ProactiveDeliverer.__new__(ProactiveDeliverer)
        deliverer._settings = SimpleNamespace(  # type: ignore[assignment]  # noqa: SLF001
            notifications=SimpleNamespace(fallback_channel="slack")
        )
        rerouted_to: list[str] = []

        async def _transport(channel: str, _message: str, **_k: Any) -> str:
            rerouted_to.append(channel)
            return "delivered"

        monkeypatch.setattr(deliverer, "_transport", _transport)

        def _note(category: str) -> Notification:
            return Notification(message="x", urgency="critical", category=category,
                                channel_name="telegram", target=4242)

        status = await deliverer._maybe_reroute(  # noqa: SLF001
            "telegram", _note(SETUP_CODE_CATEGORY), "failed"
        )
        assert status == "failed" and rerouted_to == [], "the setup code was rerouted"

        # The control: an ordinary message IS rerouted, so the empty list above is
        # the category's doing and not a reroute that never runs.
        status = await deliverer._maybe_reroute(  # noqa: SLF001
            "telegram", _note("morning_brief"), "failed"
        )
        assert status == "delivered" and rerouted_to == ["slack"]

    @pytest.mark.tripwire
    async def test_a_BOOT_retries_telegram_but_offers_the_terminal_ONCE(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.control_plane import server as server_mod

        monkeypatch.setattr(server_mod, "_BOOT_DELIVERY_RETRY_S", (0.001, 0.001))
        deliverer = _Deliverer(statuses={"telegram": ["failed", "failed", "delivered"]})

        await _server(deliverer=deliverer, owner=4242)._ensure_setup_code("start")  # noqa: SLF001

        channels = [n.channel_name for n, _ in deliverer.sent]
        assert channels.count("telegram") == 3, channels
        assert channels.count("cli") == 1, "the terminal was sent the code on every retry"
        issued = _owner().current_code()
        assert issued is not None and issued.sent

    @pytest.mark.tripwire
    async def test_a_BURST_of_refusals_spawns_ONE_code_task(self) -> None:
        srv = _server()

        for _ in range(5):
            srv._spawn_setup_code("login")  # noqa: SLF001

        assert len(srv._background) == 1  # noqa: SLF001
        await srv._settle_background()  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_stop_CANCELS_a_code_task_still_in_flight(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        srv = _server()
        started = asyncio.Event()

        async def _forever(_reason: str) -> bool:
            started.set()
            await asyncio.Event().wait()
            return True

        monkeypatch.setattr(srv, "_issue_and_deliver", _forever)
        srv._spawn_setup_code("start")  # noqa: SLF001
        await asyncio.wait_for(started.wait(), timeout=5)
        tasks = list(srv._background)  # noqa: SLF001

        await srv.stop()
        await asyncio.gather(*tasks, return_exceptions=True)

        assert tasks and all(t.cancelled() for t in tasks)
        assert not srv._background and not srv._code_pending  # noqa: SLF001

    @pytest.mark.tripwire
    async def test_a_failure_OUTSIDE_the_send_is_logged_with_a_remedy(
        self, monkeypatch: pytest.MonkeyPatch, capture_logs: list[dict[str, Any]],
    ) -> None:
        from stackowl.notifications import recipient

        def _broken(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("owner resolution broke")

        monkeypatch.setattr(recipient, "resolve_owner_addresses", _broken)

        await _server(deliverer=_Deliverer(), owner=4242)._ensure_setup_code("login")  # noqa: SLF001

        assert any(
            r.get("level") == "ERROR" and "server.setup_code" in str(r.get("msg"))
            and "remedy" in (r.get("fields") or {})
            for r in capture_logs
        ), "the code task died without a word"

    @pytest.mark.tripwire
    async def test_setup_mode_WARNS_once_not_once_per_panel(
        self, capture_logs: list[dict[str, Any]],
    ) -> None:
        srv = _server()

        for name in _guarded_handlers():
            await getattr(srv, name)(_Req(None, **_bearer()))

        refused = [r for r in capture_logs if r.get("level") == "WARNING"
                   and "has no password yet" in str(r.get("msg"))]
        assert len(refused) == 1, [r.get("msg") for r in refused]


# --------------------------------------------------------------- the code check


class TestTheCodeCheck:
    @pytest.mark.tripwire
    async def test_a_STORE_error_is_not_counted_as_a_guess(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.control_plane.password import (
            ControlPlanePassword,
            PasswordStoreUnavailable,
        )

        srv = _server()
        _issue_code()

        def _unreadable(_self: Any) -> None:
            raise PasswordStoreUnavailable("the keyring is locked")

        monkeypatch.setattr(ControlPlanePassword, "current_code", _unreadable)

        res = await _setup(srv, "0000-0000-0000")

        assert res.status == 503
        assert srv._login_attempts.tracked() == 0, "a store error braked the owner"  # noqa: SLF001

    @pytest.mark.tripwire
    def test_a_code_issued_in_the_FUTURE_is_expired(self) -> None:
        from stackowl.control_plane.password import SetupCode

        now = time.time()
        assert SetupCode("0123456789AB", int(now) + 3600, "platform", True).expired(now)
        assert not SetupCode("0123456789AB", int(now) + 60, "platform", True).expired(now)

    @pytest.mark.tripwire
    @pytest.mark.parametrize("record", [
        "scrypt$2$1$64$c2FsdA==$" + "A" * 43 + "=",
        "scrypt$2$1$1$c2FsdA==$" + "A" * 5460 + "AA==",
    ])
    def test_a_record_asking_for_a_huge_p_or_digest_is_DAMAGED(self, record: str) -> None:
        from stackowl.control_plane.password import _StoredHash

        with pytest.raises(ValueError):
            _StoredHash.parse(record)

    @pytest.mark.tripwire
    async def test_a_code_delete_that_fails_HALF_WAY_puts_the_code_back(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import SecretStoreUnreadable, delete_secret
        from stackowl.control_plane import password as password_mod

        srv = _server()
        code = _issue_code()

        def _half(service: str) -> None:
            delete_secret(service)
            raise SecretStoreUnreadable("the entry went, and then the error came")

        monkeypatch.setattr(password_mod, "delete_secret", _half)

        res = await _setup(srv, code)

        monkeypatch.setattr(password_mod, "delete_secret", delete_secret)
        assert res.status == 500
        assert _owner().code_matches(code), "the code was gone while the answer said unchanged"
        assert not _hash_file().exists()

    @pytest.mark.tripwire
    async def test_setup_mode_with_an_UNSENT_code_is_DEGRADED_with_a_remedy(self) -> None:
        from stackowl.control_plane.password import ControlPlanePassword, PasswordStoreHealth

        issued = _owner().issue_code("platform")

        health = await PasswordStoreHealth(ControlPlanePassword()).health_check()
        assert health.status == "degraded" and "reset-password" in (health.remedy or "")

        _owner().mark_code_sent(issued)
        assert (await PasswordStoreHealth(ControlPlanePassword()).health_check()).status == "ok"


# ------------------------------------------------------------------ reset undo


class TestTheResetUndoesItself:
    @pytest.mark.tripwire
    def test_a_clear_that_fails_puts_the_token_and_the_code_back(
        self, set_dashboard_password: Callable[..., str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import store_secret
        from stackowl.control_plane.auth import SECRET_SERVICE
        from stackowl.control_plane.password import (
            ControlPlanePassword,
            PasswordResetIncomplete,
            PasswordStoreUnavailable,
        )

        set_dashboard_password()
        store_secret(SECRET_SERVICE, _TOKEN)
        record = _hash_file().read_text(encoding="utf-8")

        def _cannot(_self: Any) -> None:
            raise PasswordStoreUnavailable("the store went away")

        monkeypatch.setattr(ControlPlanePassword, "clear", _cannot)

        with pytest.raises(PasswordStoreUnavailable) as caught:
            ControlPlanePassword().reset()

        assert not isinstance(caught.value, PasswordResetIncomplete)
        assert _hash_file().read_text(encoding="utf-8") == record
        assert _stored_token() == _TOKEN
        assert not _code_file().exists()

    @pytest.mark.tripwire
    def test_an_INCOMPLETE_rollback_is_said_not_hidden(
        self, set_dashboard_password: Callable[..., str], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from typer.testing import CliRunner

        from stackowl.cli.app import app
        from stackowl.config.secret_writer import SecretStoreUnreadable, store_secret
        from stackowl.control_plane import password as password_mod
        from stackowl.control_plane.auth import SECRET_SERVICE

        set_dashboard_password()
        store_secret(SECRET_SERVICE, _TOKEN)

        def _cannot(_self: Any) -> None:
            raise password_mod.PasswordStoreUnavailable("the store went away")

        def _undeletable(_service: str) -> None:
            raise SecretStoreUnreadable("the keyring is locked")

        monkeypatch.setattr(password_mod.ControlPlanePassword, "clear", _cannot)
        monkeypatch.setattr(password_mod, "delete_secret", _undeletable)

        out = CliRunner().invoke(app, ["control-plane", "reset-password"])

        assert out.exit_code == 1
        assert "Nothing was changed" not in out.output, out.output
        assert "INCOMPLETE" in out.output and "setup code" in out.output, out.output
