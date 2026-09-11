"""871 boots bound a port. Zero requests were ever served, and nothing said why.

MEASURED 2026-09-11 over every retained log, counted by `.msg` rather than by raw
line (see DEBT-301, which is why that distinction now matters):

    [webhook] receiver.run: entry                    873
    [webhook] receiver.run: exit — listening         873
    [webhook] receiver.handle: event enqueued (INFO)   0
    [webhook] receiver.handle: rate-limited (WARN)     0
    [webhook] receiver.handle: invalid signature (WARN) 0
    [webhook] receiver.handle: unknown source (WARN)   0

THE THIRD LINE USED TO READ `receiver.handle …  0`, WHICH PROVED NOTHING. That
message is emitted at DEBUG, and this deployment has written 0 DEBUG records out
of 677,108 — so its count is zero however the receiver behaved. The four loud
lines above replace it: each one could have been non-zero, and none is. See
DEBT-303, which is the same defect one surface over.

The server has bound a port on 873 boots and has never answered a request. It is
not idle by accident: the operator configured two real sources in
`~/.stackowl/stackowl.yaml` — `mygithub` and `acme`, each with a secret file on
disk — against a receiver bound to `127.0.0.1`. **A webhook sender cannot reach
loopback.**

Checked rather than assumed, because "unreachable" is a strong claim: no tunnel
process (`pgrep` matched only its own command line, the self-match this repo
already records), no reverse-proxy config naming the port, and `ss` shows nothing
but loopback on it.

SO THE ZERO WAS NEVER AMBIGUOUS — IT ONLY LOOKED IT. This codebase names the
ambiguous zero as its most expensive reading error: a count of 0 means either "not
yet" or "never possible", and only one of those is an open question. The receiver
had no way to tell a reader which it was in, because it only ever reported its own
success. LISTENING IS NOT REACHABLE.

The guide star this repo works to asks: if this degrades silently, what notices?
Here, nothing did — for 871 boots.

WHAT THE FIX DOES NOT DO: change the bind. Exposing the port is a security-posture
decision and belongs to the operator. This makes the silence audible and names the
remedy; it does not take it.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.webhooks.receiver import _is_loopback

_MESSAGE = "configured sources cannot be reached"


class TestTheLoopbackPredicate:
    """`ipaddress` answers this for every form; a string compare answers it for one."""

    @pytest.mark.tripwire
    @pytest.mark.parametrize(
        "address",
        ["127.0.0.1", "127.0.1.1", "::1", "::ffff:127.0.0.1", "localhost"],
    )
    def test_loopback_forms_are_recognised(self, address: str) -> None:
        """`127.0.1.1` and the IPv4-mapped `::ffff:127.0.0.1` are the two a
        `== "127.0.0.1"` compare misses, and both are real bind addresses."""
        assert _is_loopback(address) is True

    @pytest.mark.tripwire
    @pytest.mark.parametrize("address", ["0.0.0.0", "::", "*", "192.168.1.5"])
    def test_reachable_addresses_are_not_loopback(self, address: str) -> None:
        assert _is_loopback(address) is False

    @pytest.mark.tripwire
    @pytest.mark.parametrize("address", ["not-an-ip", "", "   "])
    def test_an_unparseable_address_is_treated_as_REACHABLE(self, address: str) -> None:
        """FAIL TOWARDS SILENCE, deliberately, and this is the one asymmetry here.

        A hostname this cannot resolve must not produce a warning. The cost of a
        wrong warning is an operator chasing a configuration that is fine — the
        cry-wolf failure this repo pays for more than it gains — while the cost of a
        missed one is the status quo of 871 silent boots.
        """
        assert _is_loopback(address) is False


class TestTheWarning:
    """It fires on the live shape, and on nothing else."""

    @staticmethod
    def _receiver(bind: str, sources: dict[str, bool]):  # noqa: ANN205
        from stackowl.webhooks.receiver import WebhookReceiver

        class _Src:
            def __init__(self, enabled: bool) -> None:
                self.enabled = enabled

        # Built OUTSIDE the class body on purpose: a comprehension inside one does
        # not close over the enclosing function's names, so `sources` is unresolvable
        # there. Python scoping, and the error is a NameError at call time.
        built = {n: _Src(e) for n, e in sources.items()}

        class _Webhook:
            bind_address = bind
            port = 8766
            sources = built

        class _Settings:
            webhook = _Webhook()

        recv = WebhookReceiver.__new__(WebhookReceiver)
        recv._settings = _Settings()  # noqa: SLF001 — constructing the minimal shape
        return recv

    @staticmethod
    def _hits(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in records if _MESSAGE in str(r.get("msg", ""))]

    @pytest.mark.tripwire
    def test_loopback_plus_enabled_sources_warns_and_names_them(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        """THE LIVE SHAPE: the operator's own configuration on 2026-09-11.

        Uses the repo's `capture_logs` fixture, which attaches a handler to the
        `stackowl` logger DIRECTLY, rather than `caplog`. That distinction is
        recorded and was paid for: `configure_logging` sets `propagate = False`, so a
        caplog-based assertion passes alone and fails in any session that configured
        logging first.
        """
        recv = self._receiver("127.0.0.1", {"mygithub": True, "acme": True})

        recv._warn_if_configured_but_unreachable()  # noqa: SLF001

        hits = self._hits(capture_logs)
        assert hits, (
            "a receiver bound to loopback with enabled sources said nothing — which "
            "is the 871-boot silence this guard exists to end"
        )
        assert hits[0].get("level") == "WARNING", (
            "production runs at INFO; a configuration that cannot work is not "
            "routine news"
        )
        fields = hits[0].get("fields") or {}
        assert fields.get("unreachable_sources") == ["acme", "mygithub"], (
            "the warning must NAME the sources; a count cannot be acted on at 2am"
        )
        assert "remedy" in fields, (
            "a warning about a configuration must say what to do about it"
        )

    @pytest.mark.tripwire
    def test_a_reachable_bind_says_nothing(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        """The control that stops this becoming noise on every correct deployment."""
        recv = self._receiver("0.0.0.0", {"mygithub": True})

        recv._warn_if_configured_but_unreachable()  # noqa: SLF001

        assert not self._hits(capture_logs)

    @pytest.mark.tripwire
    def test_loopback_with_NO_enabled_source_says_nothing(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        """A loopback bind is the SAFE DEFAULT — `bind_address` defaults to it "for
        safety" in its own field description. Warning about it with nothing
        configured would fire on every fresh install, which is how a warning gets
        filtered instead of read."""
        recv = self._receiver("127.0.0.1", {"mygithub": False})

        recv._warn_if_configured_but_unreachable()  # noqa: SLF001

        assert not self._hits(capture_logs)

    @pytest.mark.tripwire
    def test_the_bind_is_not_changed(self) -> None:
        """SCOPE, pinned. Exposing the port is the operator's security decision, and
        a later pass must not quietly take it because the warning is annoying."""
        import inspect
        import textwrap

        from stackowl.webhooks import receiver as mod

        src = textwrap.dedent(
            inspect.getsource(mod.WebhookReceiver._warn_if_configured_but_unreachable)
        )
        assert "bind_address =" not in src and "= bind" not in src.replace(
            "bind = ", ""
        ), "the warning writes to the bind address — it must only report"
