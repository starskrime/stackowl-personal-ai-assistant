"""Unit coverage for bridge_spike.push: VAPID keypair/public-key shape, the
NFR29 endpoint validator (https-only, loopback/private/link-local refused --
the SSRF guard AD-19 calls for), the subscription store, the metadata-only
payload builder, and send_push's real VAPID+encryption against a fake
subscription (never a real relay -- see test_push_stub.py for the local
stand-in this is proven against end-to-end).
"""

from __future__ import annotations

import base64
import json
import socket

import pytest
from bridge_spike import push as push_module
from cryptography.hazmat.primitives.asymmetric import ec


def test_generate_vapid_keypair_is_p256() -> None:
    key = push_module.generate_vapid_keypair()
    assert isinstance(key.curve, ec.SECP256R1)


def test_vapid_public_key_b64url_is_an_uncompressed_p256_point() -> None:
    key = push_module.generate_vapid_keypair()
    encoded = push_module.vapid_public_key_b64url(key)
    padded = encoded + "=" * (-len(encoded) % 4)
    raw = base64.urlsafe_b64decode(padded)
    assert len(raw) == 65
    assert raw[0] == 0x04  # uncompressed point marker


# -- validate_endpoint (NFR29) -------------------------------------------


def test_validate_endpoint_rejects_non_https() -> None:
    with pytest.raises(push_module.EndpointRefused, match="https"):
        push_module.validate_endpoint("http://push.example.com/abc")


def test_validate_endpoint_rejects_an_ip_literal_loopback() -> None:
    with pytest.raises(push_module.EndpointRefused, match="disallowed address"):
        push_module.validate_endpoint("https://127.0.0.1/abc")


def test_validate_endpoint_rejects_an_ipv6_loopback_literal() -> None:
    with pytest.raises(push_module.EndpointRefused, match="disallowed address"):
        push_module.validate_endpoint("https://[::1]/abc")


def test_validate_endpoint_rejects_a_private_ip_literal() -> None:
    with pytest.raises(push_module.EndpointRefused, match="disallowed address"):
        push_module.validate_endpoint("https://10.1.2.3/abc")


def test_validate_endpoint_rejects_a_link_local_ip_literal() -> None:
    with pytest.raises(push_module.EndpointRefused, match="disallowed address"):
        push_module.validate_endpoint("https://169.254.1.1/abc")


def test_validate_endpoint_accepts_a_public_ip_literal() -> None:
    push_module.validate_endpoint("https://8.8.8.8/abc")  # does not raise


def test_validate_endpoint_rejects_a_hostname_resolving_to_loopback() -> None:
    def fake_resolve(hostname: str) -> list[str]:
        assert hostname == "push.internal.example"
        return ["127.0.0.1"]

    with pytest.raises(push_module.EndpointRefused, match="disallowed address"):
        push_module.validate_endpoint("https://push.internal.example/abc", resolve=fake_resolve)


def test_validate_endpoint_rejects_when_any_resolved_address_is_private() -> None:
    """Multi-A DNS rotation: a hostname that resolves to BOTH a public and a
    private address must still be refused -- checking only the first result
    would let an attacker hide a private target behind a public decoy."""

    def fake_resolve(hostname: str) -> list[str]:
        return ["8.8.8.8", "10.0.0.5"]

    with pytest.raises(push_module.EndpointRefused, match="disallowed address"):
        push_module.validate_endpoint("https://push.example.com/abc", resolve=fake_resolve)


def test_validate_endpoint_accepts_a_hostname_resolving_only_to_public_addresses() -> None:
    def fake_resolve(hostname: str) -> list[str]:
        return ["8.8.8.8", "1.1.1.1"]

    push_module.validate_endpoint("https://push.example.com/abc", resolve=fake_resolve)  # does not raise


def test_validate_endpoint_refuses_when_resolution_fails() -> None:
    def fake_resolve(hostname: str) -> list[str]:
        raise push_module.EndpointRefused("simulated resolution failure")

    with pytest.raises(push_module.EndpointRefused):
        push_module.validate_endpoint("https://does-not-resolve.example/abc", resolve=fake_resolve)


# -- _default_resolve (the resolver the real /api/push/subscribe route uses
# -- for a hostname endpoint) -- exercised directly via socket.getaddrinfo,
# -- not via the resolve= override the tests above substitute.


def test_default_resolve_converts_a_real_socket_error_into_endpoint_refused(monkeypatch) -> None:
    def fake_getaddrinfo(host, port):
        raise socket.gaierror("simulated DNS failure")

    monkeypatch.setattr(push_module.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(push_module.EndpointRefused):
        push_module.validate_endpoint("https://does-not-resolve.example/abc")


def test_default_resolve_strips_an_ipv6_zone_suffix_and_still_refuses_loopback(monkeypatch) -> None:
    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1%lo0", 0, 0, 0))]

    monkeypatch.setattr(push_module.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(push_module.EndpointRefused, match="disallowed address"):
        push_module.validate_endpoint("https://push.example.com/abc")


# -- PushSubscriptionStore -------------------------------------------------


def test_subscription_store_add_and_list_for_device() -> None:
    store = push_module.PushSubscriptionStore()
    store.add("device-1", "https://push.example.com/a", "p256dh-a", "auth-a")
    store.add("device-1", "https://push.example.com/b", "p256dh-b", "auth-b")
    store.add("device-2", "https://push.example.com/c", "p256dh-c", "auth-c")

    subs = store.for_device("device-1")
    assert {s.endpoint for s in subs} == {"https://push.example.com/a", "https://push.example.com/b"}
    assert len(store.all()) == 3


def test_subscription_store_remove_is_scoped_to_device_and_endpoint() -> None:
    store = push_module.PushSubscriptionStore()
    store.add("device-1", "https://push.example.com/a", "p256dh-a", "auth-a")

    assert store.remove("device-2", "https://push.example.com/a") is False  # wrong device
    assert store.remove("device-1", "https://push.example.com/a") is True
    assert store.remove("device-1", "https://push.example.com/a") is False  # already gone


# -- build_metadata (FR26: never full content) -----------------------------


def test_build_metadata_carries_only_the_four_allowed_fields() -> None:
    metadata = push_module.build_metadata(item_id="item-1", kind="approval", intensity=0.7, rendering="Approve?")
    assert metadata == {"id": "item-1", "kind": "approval", "intensity": 0.7, "rendering": "Approve?"}
    assert set(metadata.keys()) == {"id", "kind", "intensity", "rendering"}


# -- send_push (real VAPID + aes128gcm, against a fake subscription) -------


def test_send_push_encrypts_and_signs_and_posts_only_metadata(monkeypatch) -> None:
    """Proves send_push's OWN contract without needing a live server: swap
    pywebpush.webpush for a spy that records what it was called with, and
    assert this module never hands it anything beyond the metadata dict --
    the real end-to-end encryption round trip (against push_stub.py, a real
    HTTPS server) is covered by test_push_stub.py instead."""
    calls = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(push_module, "webpush", fake_webpush)

    vapid_key = push_module.generate_vapid_keypair()
    subscription = push_module.PushSubscription(
        device_id="device-1", endpoint="https://push.example.com/abc", p256dh="p256dh", auth="auth"
    )
    metadata = push_module.build_metadata(item_id="item-1", kind="alert", intensity=1, rendering="Something happened")

    push_module.send_push(vapid_key, subscription, metadata)

    assert len(calls) == 1
    call = calls[0]
    assert call["subscription_info"] == {
        "endpoint": "https://push.example.com/abc",
        "keys": {"p256dh": "p256dh", "auth": "auth"},
    }
    assert json.loads(call["data"]) == metadata
    assert call["vapid_claims"]["sub"] == push_module.VAPID_SUB_CLAIM
