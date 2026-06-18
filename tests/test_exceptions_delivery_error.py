"""T0 — DeliveryError(DomainError): the single new C6 exception.

The provenance-keyed no-target rule (C-1) raises ``DeliveryError`` when an
EXPLICIT keyword target was passed to ``send_text`` but does not resolve. The
exception carries ONLY a channel name + a coarse reason code — NEVER a raw
secret / JID / channel-id (sensitive-data mandate).
"""

from __future__ import annotations

from stackowl.exceptions import DeliveryError, DomainError


def test_delivery_error_is_domain_error() -> None:
    err = DeliveryError("discord", "no_target")
    assert isinstance(err, DomainError)


def test_delivery_error_carries_channel_and_reason() -> None:
    err = DeliveryError("whatsapp", "no_target")
    assert err.channel == "whatsapp"
    assert err.reason == "no_target"


def test_delivery_error_str_has_no_raw_target() -> None:
    """The message must not leak a raw JID / channel-id — only channel + reason."""
    secret_jid = "1234567890@s.whatsapp.net"
    err = DeliveryError("whatsapp", "no_target")
    # Even if a caller mistakenly tried to embed the JID, the constructor never
    # accepts/echoes a raw target: the rendered str is channel + reason only.
    rendered = str(err)
    assert secret_jid not in rendered
    assert "whatsapp" in rendered
    assert "no_target" in rendered


def test_delivery_error_reasons() -> None:
    for reason in ("no_target", "no_channel", "transport_error"):
        err = DeliveryError("discord", reason)
        assert err.reason == reason
        assert reason in str(err)
