"""A05.1's invariants, as assertions.

This tree already contains the thing these guards exist to prevent:
``mcp/server.py::_sse_auth_ok`` reads ``if not expected_token: return True`` —
an HTTP transport that serves unauthenticated when nobody configured a token,
with a loud startup WARNING. **A warning is not a gate.** Closing that one
breaks a working setup and is therefore the operator's call (ESC-171); this file
makes sure the NEW surface cannot acquire the same shape by accident.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

import pytest

from stackowl.control_plane import auth as auth_mod
from stackowl.control_plane.auth import (
    ALL_SEVERITIES,
    READ,
    UNAUTHORIZED_BODY,
    CredentialUnavailable,
    authenticate,
    check_origin,
    is_loopback,
)

_TOKEN = "s3cret-token-value-for-tests"


class TestI1NoCredentialMeansNoServer:
    """The server refuses to bind rather than serving openly."""

    @pytest.mark.tripwire
    def test_authenticate_with_no_expected_token_refuses(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        """THE COUNTER-EXAMPLE, pinned. `_sse_auth_ok` returns True here."""
        assert authenticate(f"Bearer {_TOKEN}", "") is None
        assert authenticate(None, "") is None

    @pytest.mark.tripwire
    def test_the_no_token_branch_does_not_return_a_principal(self) -> None:
        """Read as SOURCE, because the behaviour test above passes for a
        function that returns None for every input. This pins that the empty
        -token branch is a REFUSAL and not a pass-through."""
        src = textwrap.dedent(inspect.getsource(authenticate))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = ast.unparse(node.test)
            if "expected_token" not in test:
                continue
            returns = [
                ast.unparse(n.value) if n.value else "None"
                for n in ast.walk(node)
                if isinstance(n, ast.Return)
            ]
            assert returns and all(r == "None" for r in returns), (
                "the empty-credential branch returns something other than None — "
                f"got {returns}. That is the fail-open shape this guard exists for."
            )

    @pytest.mark.tripwire
    def test_the_server_raises_rather_than_binding_without_a_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bind that happened would be observable as a port; a raise is not.
        So this asserts the ORDER: the credential is obtained before any
        aiohttp object is constructed."""
        from stackowl.control_plane import server as server_mod

        src = textwrap.dedent(inspect.getsource(server_mod.ControlPlaneServer.run))
        cred_at = src.index("ensure_credential()")
        app_at = src.index("web.Application()")
        assert cred_at < app_at, (
            "the server constructs its app before obtaining a credential, so a "
            "credential failure would leave a bound port behind"
        )

    @pytest.mark.tripwire
    def test_credential_unavailable_is_an_error_not_a_sentinel(self) -> None:
        assert issubclass(CredentialUnavailable, Exception)


class TestTheCredentialSurvivesABoot:
    """The failure this item's own design document called "the silent one"."""

    @pytest.mark.tripwire
    def test_a_file_stored_credential_is_found_when_the_keyring_is_absent(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE BUG THIS GUARD WAS WRITTEN FOR WAS MINE, and it was in the first
        draft of `ensure_credential`.

        `store_secret` writes to the OS keyring when it can and to a 0600 file
        when it cannot, and it does not tell its caller which. A resolver that
        only asked the keyring would mint a FRESH TOKEN ON EVERY BOOT of a
        headless box — which is this one — silently invalidating whatever the
        operator had saved. Nothing would have failed; every boot would simply
        have produced a working server with a token nobody held.
        """
        from stackowl.control_plane.auth import SECRET_SERVICE, ensure_credential
        from stackowl.paths import StackowlHome

        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir()
        (secrets_dir / f"{SECRET_SERVICE}.key").write_text("stored-earlier", encoding="utf-8")
        monkeypatch.setattr(StackowlHome, "secrets_dir", classmethod(lambda cls: secrets_dir))

        # No keyring: make the keychain backend fail the way a headless box does.
        from stackowl.config.secret_resolver import SecretResolver

        real = SecretResolver.resolve

        def _no_keyring(value: str) -> str:
            if value.startswith("keychain:"):
                raise RuntimeError("no session keyring on this host")
            return real(value)

        monkeypatch.setattr(SecretResolver, "resolve", staticmethod(_no_keyring))

        assert ensure_credential() == "stored-earlier", (
            "the stored credential was not found, so a new one would be minted "
            "and every saved token would start failing — silently"
        )

    @pytest.mark.tripwire
    def test_both_backends_are_consulted(self) -> None:
        """Pins the RULE, not today's data: on a host WITH a keyring the test
        above passes for a resolver that only reads files, and vice versa."""
        from stackowl.control_plane import auth as mod

        src = textwrap.dedent(inspect.getsource(mod.ensure_credential))
        assert "keychain:" in src and "file:" in src, (
            "ensure_credential consults only one secret backend; store_secret "
            "writes to either and does not say which"
        )


class TestI2AuthenticationIsNotMiddleware:
    """Middleware does not run for a WebSocket upgrade."""

    @pytest.mark.tripwire
    def test_no_aiohttp_middleware_is_registered(self) -> None:
        """A05.6 will want a live channel. If authentication moves into an
        app-level middleware, that upgrade is served unauthenticated and every
        handler written between now and then has to be re-plumbed."""
        from stackowl.control_plane import server as server_mod

        src = inspect.getsource(server_mod)
        assert "middlewares" not in src and "@web.middleware" not in src, (
            "the control plane registered aiohttp middleware. Authentication "
            "must be an explicit per-handler call — middleware does not run for "
            "a WebSocket upgrade (A05.1, invariant I2)."
        )

    @pytest.mark.tripwire
    def test_every_route_handler_calls_authenticate(self) -> None:
        """Derived from the router registrations rather than a hand-list, so a
        route added later cannot quietly skip the check.

        IT FOLLOWS THE DELEGATION NOW, and that is the whole change A05.4 made
        here. Until the second route, each handler called `authenticate` itself
        and this walked for that literal. The three checks — origin, token, READ
        — then moved into `_guard`, because two routes each doing them inline is
        two copies of one rule and the second copy is where one of the three gets
        forgotten. The INVARIANT is unchanged: authentication reaches every
        registered route. Only the hop count did.
        """
        from stackowl.control_plane import server as server_mod

        src = textwrap.dedent(inspect.getsource(server_mod.ControlPlaneServer))
        tree = ast.parse(src)

        handlers = {
            ast.unparse(node.args[1]).split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("add_")
            and len(node.args) >= 2
        }
        assert handlers, "no routes found — this guard has gone blind"

        defs = {
            n.name: n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        }
        guarded = 0
        for name in handlers:
            fn = defs.get(name)
            assert fn is not None, f"route handler {name!r} not found"
            body = ast.unparse(fn)
            if "self._guard(" in body:
                guarded += 1
                continue

            # THE ONE EXEMPTION, AND IT IS STRUCTURAL RATHER THAN A NAME.
            # A browser cannot put an `Authorization` header on a top-level
            # navigation, so the page itself must be servable without one — a
            # dashboard nobody can open is the state A05.1 shipped while its
            # record said the surface was done. An unguarded route is therefore
            # allowed only if it CANNOT reach platform state: no instance
            # attribute at all. That is a property of the code, not a promise,
            # and it is what stops the exemption widening into "the index route
            # may do whatever it likes".
            # THE SECOND EXEMPTION, ADDED FOR THE LOGIN ROUTE AND KEPT
            # STRUCTURAL. `_guard` demands the bearer token; a route whose JOB is
            # to hand that token out cannot present it first. So it is allowed to
            # skip `_guard` — but only by AUTHENTICATING BY ITS OWN MEANS, which
            # is a property of the code and not a name on a list: it must check
            # the origin (or any page on the internet could POST from a browser
            # that can reach loopback and read the token out of the reply) and it
            # must compare a secret in constant time.
            #
            # The invariant this file exists for is unchanged: EVERY registered
            # route either presents a credential or verifies one. What widened is
            # how it may verify, not whether it must.
            body_src = ast.unparse(fn)
            mints_a_credential = (
                "self._origin_ok(" in body_src and "compare_digest(" in body_src
            )
            if mints_a_credential:
                guarded += 1
                continue

            touched = sorted({
                n.attr for n in ast.walk(fn)
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name) and n.value.id == "self"
            })
            assert not touched, (
                f"route handler {name!r} skips the guard AND reads {touched} off "
                "the server. An unauthenticated route may serve a constant and "
                "nothing else, or verify a credential itself (origin + a "
                "constant-time compare) — the moment it does neither it needs "
                "the guard"
            )

        assert guarded, (
            "no route goes through the guard at all — the exemption above has "
            "swallowed the rule it was carved out of"
        )

        # And the hop actually authenticates. Without this the chain above could
        # be satisfied by a `_guard` that had quietly stopped checking anything,
        # which is a worse failure than the one the delegation replaced.
        guard = defs.get("_guard")
        assert guard is not None, "the guard every handler delegates to is gone"
        assert "authenticate(" in ast.unparse(guard), (
            "`_guard` no longer calls authenticate() — every route now delegates "
            "its token check to a function that does not make one"
        )


class TestI3AnUnknownCredentialIsRefused:
    @pytest.mark.tripwire
    def test_a_wrong_token_is_refused(self) -> None:
        assert authenticate("Bearer wrong", _TOKEN) is None

    @pytest.mark.tripwire
    @pytest.mark.parametrize("header", [None, "", "Token abc", "Bearer", "bearer x"])
    def test_a_malformed_header_is_refused(self, header: str | None) -> None:
        assert authenticate(header, _TOKEN) is None

    @pytest.mark.tripwire
    def test_a_correct_token_authenticates_with_a_capability_SET(self) -> None:
        principal = authenticate(f"Bearer {_TOKEN}", _TOKEN)
        assert principal is not None
        assert isinstance(principal.granted, frozenset), (
            "granted must be a SET, not a boolean. A boolean would have to become "
            "a set the first time anyone asks for a read-only credential, and "
            "every handler written against the boolean would change with it."
        )
        assert principal.granted == ALL_SEVERITIES
        assert principal.may(READ)

    @pytest.mark.tripwire
    def test_the_request_path_takes_no_default_owner(self) -> None:
        """`owner_id: str = DEFAULT_PRINCIPAL_ID` appears at 37 construction
        sites in this tree (measured 2026-09-11) and is exactly how an unscoped
        call becomes invisible. It must not appear on the REQUEST path."""
        from stackowl.control_plane import server as server_mod

        src = inspect.getsource(server_mod)
        assert "DEFAULT_PRINCIPAL_ID" not in src, (
            "the server module names DEFAULT_PRINCIPAL_ID. The default belongs "
            "on the resolver, never on the request path."
        )


class TestI4TheTokenIsNeverLogged:
    #: Identifiers that hold a credential's plaintext somewhere in these modules.
    _SECRET_NAMES = frozenset({"_token", "minted", "presented", "expected_token",
                               "existing", "secret"})

    @pytest.mark.tripwire
    def test_no_log_call_in_either_module_passes_the_token(self) -> None:
        """Walks IDENTIFIERS, not the unparsed text.

        The first version substring-matched `ast.unparse(call)` and failed on
        this module's own honest line, `"exit — minted a new credential"` —
        the WORD "minted" inside a message literal, not the variable. That is
        the same conflation this repo records elsewhere: a pattern that matches
        something other than the thing. A guard that fires on correct work is
        how a guard gets deleted rather than satisfied, so it asks the AST for
        names now and string literals cannot reach it.
        """
        from stackowl.control_plane import server as server_mod

        for mod in (auth_mod, server_mod):
            tree = ast.parse(inspect.getsource(mod))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Attribute)
                        and isinstance(node.func.value.value, ast.Name)
                        and node.func.value.value.id == "log"):
                    continue
                for sub in ast.walk(node):
                    ident = None
                    if isinstance(sub, ast.Name):
                        ident = sub.id
                    elif isinstance(sub, ast.Attribute):
                        ident = sub.attr
                    if ident in self._SECRET_NAMES:
                        raise AssertionError(
                            f"{mod.__name__} passes the identifier {ident!r} to a "
                            f"log call — a credential's plaintext must never reach "
                            f"a log record (I4). Offending call: "
                            f"{ast.unparse(node)[:120]}"
                        )

    @pytest.mark.tripwire
    def test_that_guard_can_actually_see_a_leak(self) -> None:
        """VACUITY CONTROL, on a CONSTRUCTED leak rather than the live tree.

        The assertion above passes when it matches nothing, which is exactly
        what a broken walk also looks like — and the first version of this walk
        WAS broken, in the other direction. Neither the live modules nor a
        threshold can prove the detector works; a planted leak can.
        """
        leaky = ast.parse(
            'log.control_plane.info("x", extra={"_fields": {"t": minted}})\n'
        )
        found = [
            sub.id
            for node in ast.walk(leaky)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "log"
            for sub in ast.walk(node)
            if isinstance(sub, ast.Name) and sub.id in self._SECRET_NAMES
        ]
        assert found == ["minted"], (
            f"the walk cannot see a planted leak — got {found}. The guard above "
            "would then pass over a real one."
        )

    @pytest.mark.tripwire
    def test_a_message_literal_mentioning_a_secret_word_is_NOT_a_leak(self) -> None:
        """The false positive that cost this guard its first run. `"minted a new
        credential"` is the honest sentence a reader needs; only the VALUE is
        forbidden."""
        benign = ast.parse(
            'log.control_plane.info("exit — minted a new credential")\n'
        )
        found = [
            sub.id
            for node in ast.walk(benign)
            if isinstance(node, ast.Call)
            for sub in ast.walk(node)
            if isinstance(sub, ast.Name) and sub.id in self._SECRET_NAMES
        ]
        assert found == [], f"a message literal was read as a leak — got {found}"

    @pytest.mark.tripwire
    def test_a_refusal_logs_a_remedy_and_not_the_credential(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        authenticate("Bearer wrong", _TOKEN)
        hits = [r for r in capture_logs if "auth.authenticate" in str(r.get("msg", ""))]
        assert hits, "a refusal said nothing at all"
        assert any("remedy" in (r.get("fields") or {}) for r in hits), (
            "a refusal must name what to do about it; the operator is the caller here"
        )
        for r in hits:
            assert _TOKEN not in str(r), "the token reached a log record"


class TestI5TheRefusalIsUniform:
    @pytest.mark.tripwire
    def test_one_body_for_every_refusal(self) -> None:
        """Enumeration resistance, the shape `webhooks/receiver.py` already uses
        (F139). The distinguishing detail goes to the WARNING log."""
        from stackowl.control_plane import server as server_mod

        src = textwrap.dedent(
            inspect.getsource(server_mod.ControlPlaneServer._reject)
        )
        assert "UNAUTHORIZED_BODY" in src
        assert UNAUTHORIZED_BODY == "unauthorized"


class TestOriginIsCheckedBeforeTheToken:
    @pytest.mark.tripwire
    def test_order_is_origin_then_token(self) -> None:
        """ORDERING IS BEHAVIOUR. A browser request from another origin may carry
        a valid credential, so checking the token first authenticates an attack
        before rejecting it.

        READS `_guard`, NOT `_handle_health`. Until A05.4 this asserted the
        ordering inside one named handler, which was true and pinned the wrong
        thing: a SECOND route with no origin check at all would have passed it
        untouched. The decision lives in one place now and
        `test_every_registered_route_goes_through_the_guard` is what makes that
        one place cover all of them.
        """
        from stackowl.control_plane import server as server_mod

        src = textwrap.dedent(
            inspect.getsource(server_mod.ControlPlaneServer._guard)
        )
        # `_origin_ok` since the login route arrived: the origin rule moved into
        # one shared method so login could ask it WITHOUT taking the token half.
        # The invariant is unchanged and so is the reason — only the name of the
        # call `_guard` makes. And the origin rule itself is still real, which
        # the second assertion pins: the shared method must actually call it.
        assert src.index("self._origin_ok(") < src.index("authenticate("), (
            "the token is checked before the origin — reverse them"
        )
        shared = textwrap.dedent(
            inspect.getsource(server_mod.ControlPlaneServer._origin_ok)
        )
        assert "check_origin(" in shared, (
            "the shared origin method no longer checks the origin — the "
            "indirection above is now pointing at nothing"
        )

    @pytest.mark.tripwire
    def test_no_route_handler_performs_ITS_OWN_auth(self) -> None:
        """The half `test_every_route_handler_calls_authenticate` cannot see.

        THAT test already swept every registered route — A05.1 built it derived
        from the router, not hand-listed — and it asks whether the check is
        REACHED. This asks whether it is reached ONLY THROUGH THE ONE PLACE.

        The distinction earns its keep because the auth decision is THREE checks
        — origin, token, READ — and a handler can satisfy the first test while
        doing two of the three itself. With one route they sat inline honestly;
        the second route is exactly when that becomes two copies of one rule.

        What was genuinely pinned to ONE HANDLER BY NAME was the ORIGIN ORDERING:
        `test_order_is_origin_then_token` read `_handle_health`'s source, so a
        second route checking the token first — or not checking the origin at all
        — would have passed it untouched. That test now reads `_guard`, and this
        one makes it cover every route by forbidding a handler its own copy.
        """
        import ast

        from stackowl.control_plane import server as server_mod

        cls = ast.parse(
            textwrap.dedent(inspect.getsource(server_mod.ControlPlaneServer))
        ).body[0]
        methods = {
            n.name: n
            for n in ast.walk(cls)
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
        }

        registered: set[str] = set()
        for node in ast.walk(methods["run"]):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("add_")
                and len(node.args) == 2
                and isinstance(node.args[1], ast.Attribute)
            ):
                registered.add(node.args[1].attr)

        assert len(registered) >= 3, (
            f"only {sorted(registered)} route handler(s) found — this sweep "
            "passes vacuously on one route, which is the state it was written "
            "to leave behind"
        )

        for handler in sorted(registered):
            body = textwrap.dedent(ast.unparse(methods[handler]))
            for inline in ("check_origin(", "authenticate(", ".may("):
                assert inline not in body, (
                    f"{handler} performs `{inline}` itself. The decision belongs "
                    "in `_guard`, or the next route will carry two of the three "
                    "checks and look finished"
                )

    @pytest.mark.tripwire
    def test_a_request_with_no_origin_is_allowed(self) -> None:
        """curl and native clients set no Origin; the header only means anything
        when a browser sets it."""
        assert check_origin(None, None) is True

    @pytest.mark.tripwire
    def test_a_foreign_origin_is_refused(self) -> None:
        assert check_origin("http://evil.example", "127.0.0.1:8787") is False

    @pytest.mark.tripwire
    def test_a_browser_ON_A_LAN_ADDRESS_is_allowed_while_the_bind_is_a_WILDCARD(
        self,
    ) -> None:
        """THE REGRESSION THIS CONTRACT CHANGE EXISTS TO PREVENT.

        `check_origin` used to compare against `f"{bind_address}:{port}"`. On
        loopback that is exactly what a browser sends, so it worked and every
        test was green. The operator answered ESC-172 on 2026-09-12 — bind every
        interface by default, work out of the box — and `0.0.0.0:8787` is a
        string NO browser ever sends: a person opening
        `http://192.168.1.50:8787` sends that as their Host, it would not have
        matched, and the dashboard would have refused EVERY request while the
        loopback tests stayed green.
        """
        assert check_origin("http://192.168.1.50:8787", "192.168.1.50:8787") is True
        assert check_origin("http://stackowl.local:8787", "stackowl.local:8787") is True

    @pytest.mark.tripwire
    def test_a_SUFFIX_of_the_host_is_not_the_host(self) -> None:
        """The comparison is exact. `origin.endswith(host)` admitted
        `http://evil-127.0.0.1:8787`; hard to exploit and still a suffix test
        standing in for an equality test, which is weaker on a LAN address than
        it ever was on loopback."""
        assert check_origin("http://evil-192.168.1.50:8787", "192.168.1.50:8787") is False

    @pytest.mark.tripwire
    def test_a_browser_that_sends_an_origin_and_NO_host_is_refused(self) -> None:
        """Nothing to compare against. Allowing it would make the check
        skippable by omitting a header."""
        assert check_origin("http://192.168.1.50:8787", None) is False


class TestTheLoopbackPredicate:
    @pytest.mark.tripwire
    @pytest.mark.parametrize(
        "address", ["127.0.0.1", "127.0.1.1", "::1", "::ffff:127.0.0.1", "localhost"]
    )
    def test_loopback_forms(self, address: str) -> None:
        """`127.0.1.1` and the IPv4-mapped form are the two a `== "127.0.0.1"`
        compare misses, and both are real bind addresses."""
        assert is_loopback(address) is True

    @pytest.mark.tripwire
    @pytest.mark.parametrize("address", ["0.0.0.0", "::", "192.168.1.5", "", "not-an-ip"])
    def test_reachable_or_unparseable_is_not_loopback(self, address: str) -> None:
        """Unparseable fails towards SILENCE: a wrong warning sends an operator
        chasing a configuration that is fine."""
        assert is_loopback(address) is False


class TestI7ItIsItsOwnSupervisedTask:
    @pytest.mark.tripwire
    def test_distinct_task_id_from_the_webhook_receiver(self) -> None:
        """One shared app would mean one shared failure account: `SupervisedTask`
        parks a task permanently after repeated failures, so either surface's
        fault would take the other down."""
        from stackowl.control_plane.server import ControlPlaneServer
        from stackowl.webhooks.receiver import WebhookReceiver

        cp = ControlPlaneServer.__new__(ControlPlaneServer)
        wh = WebhookReceiver.__new__(WebhookReceiver)
        assert cp.task_id != wh.task_id
        assert cp.task_id == "control_plane"

    @pytest.mark.tripwire
    def test_it_does_not_reuse_the_webhook_app(self) -> None:
        from stackowl.control_plane import server as server_mod

        src = inspect.getsource(server_mod)
        assert "WebhookReceiver" not in src.split('"""', 2)[-1], (
            "the control plane reaches into the webhook receiver; they share "
            "auth code and a library, nothing else"
        )


class TestTheServerShipsOn:
    @pytest.mark.tripwire
    def test_enabled_defaults_true(self) -> None:
        """A feature ships ON. If nothing sets the flag, you shipped decoration."""
        from stackowl.config.control_plane_settings import ControlPlaneSettings

        assert ControlPlaneSettings().enabled is True

    @pytest.mark.tripwire
    def test_the_token_is_not_a_config_field(self) -> None:
        """Its plaintext must never enter a YAML file an operator might paste
        into a bug report."""
        from stackowl.config.control_plane_settings import ControlPlaneSettings

        assert "auth_token" not in ControlPlaneSettings.model_fields
        assert "token" not in ControlPlaneSettings.model_fields
