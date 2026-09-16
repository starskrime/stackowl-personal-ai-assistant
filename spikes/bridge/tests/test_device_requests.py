"""Single-pending second-device approval state machine (AD-16, FR69)."""

from __future__ import annotations

import time

import pytest
from bridge_spike import device_requests as device_requests_module


def test_create_returns_a_name_and_a_code() -> None:
    store = device_requests_module.DeviceRequestStore()
    request = store.create("Bakir's iPhone")
    assert request.device_name == "Bakir's iPhone"
    assert len(request.code) == device_requests_module.CODE_LENGTH
    assert request.approved is False


def test_a_second_concurrent_request_is_refused_while_one_is_pending() -> None:
    store = device_requests_module.DeviceRequestStore()
    store.create("first-device")
    with pytest.raises(device_requests_module.DeviceRequestError):
        store.create("second-device")


def test_a_new_request_can_be_created_once_the_pending_one_is_approved() -> None:
    store = device_requests_module.DeviceRequestStore()
    first = store.create("first-device")
    store.approve(first.request_id, first.code)
    # No longer pending -- a new request is allowed.
    second = store.create("second-device")
    assert second.device_name == "second-device"


def test_create_rejects_an_empty_or_blank_device_name() -> None:
    store = device_requests_module.DeviceRequestStore()
    with pytest.raises(device_requests_module.DeviceRequestError):
        store.create("")
    with pytest.raises(device_requests_module.DeviceRequestError):
        store.create("   ")


def test_approve_succeeds_for_the_pending_requests_own_id_and_code() -> None:
    store = device_requests_module.DeviceRequestStore()
    request = store.create("second-device")
    approved = store.approve(request.request_id, request.code)
    assert approved.approved is True
    assert store.get_pending() is None


def test_approve_refuses_an_unknown_request_id() -> None:
    store = device_requests_module.DeviceRequestStore()
    request = store.create("second-device")
    with pytest.raises(device_requests_module.DeviceRequestError):
        store.approve("not-a-real-id", request.code)


def test_approve_refuses_a_mismatched_code_for_the_right_request_id() -> None:
    """The AC's own scenario: "Mismatched/expired code rejected" -- a
    correct request id with the WRONG code must still be refused."""
    store = device_requests_module.DeviceRequestStore()
    request = store.create("second-device")
    with pytest.raises(device_requests_module.DeviceRequestError):
        store.approve(request.request_id, "WRONGCD")
    # The request is still pending -- a mismatch does not consume it.
    assert store.get_pending() is request


def test_approve_refuses_when_nothing_is_pending() -> None:
    store = device_requests_module.DeviceRequestStore()
    with pytest.raises(device_requests_module.DeviceRequestError):
        store.approve("anything", "ANYCODE")


def test_get_finds_both_a_pending_and_an_approved_request_by_id() -> None:
    store = device_requests_module.DeviceRequestStore()
    request = store.create("second-device")
    assert store.get(request.request_id) is request
    store.approve(request.request_id, request.code)
    assert store.get(request.request_id) is request


def test_get_returns_none_for_an_unknown_id() -> None:
    store = device_requests_module.DeviceRequestStore()
    assert store.get("nope") is None


def test_a_stale_pending_request_expires_and_frees_the_slot(monkeypatch) -> None:
    store = device_requests_module.DeviceRequestStore(ttl_seconds=0.01)
    store.create("first-device")
    time.sleep(0.05)
    # The stale request no longer blocks a new one, and is no longer "pending".
    assert store.get_pending() is None
    second = store.create("second-device")
    assert second.device_name == "second-device"


def test_approving_an_expired_request_is_refused() -> None:
    store = device_requests_module.DeviceRequestStore(ttl_seconds=0.01)
    request = store.create("first-device")
    time.sleep(0.05)
    with pytest.raises(device_requests_module.DeviceRequestError):
        store.approve(request.request_id, request.code)


def test_a_stale_approved_request_expires_and_is_no_longer_gettable() -> None:
    """Regression: an APPROVED entry must expire too, per the store's own
    docstring ("approved requests are kept... until their own TTL") -- only
    the pending slot used to be checked for staleness."""
    store = device_requests_module.DeviceRequestStore(ttl_seconds=0.05)
    request = store.create("second-device")
    store.approve(request.request_id, request.code)
    assert store.get(request.request_id) is request  # still fresh right after approval
    time.sleep(0.1)
    assert store.get(request.request_id) is None
