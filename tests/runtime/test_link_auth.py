"""Spec 2.4 -- ``runtime.link_auth`` truth table.

Pure functions (``generate_link_secret``/``verify_link_secret``/
``authorize_peer``) plus ``peer_pid`` over a REAL ``socket.socketpair`` (both
ends are this test process, so the OS-verified PID is ``os.getpid()``) and its
two fail-closed paths: no ``SO_PEERCRED`` on this platform, and a raised
``getsockopt``.
"""

from __future__ import annotations

import os
import socket

from stackowl.runtime import link_auth


def test_generate_link_secret_returns_a_nonempty_string() -> None:
    secret = link_auth.generate_link_secret()
    assert isinstance(secret, str)
    assert len(secret) > 16


def test_generate_link_secret_is_different_each_call() -> None:
    """Not a fixed constant -- a real per-boot secret."""
    assert link_auth.generate_link_secret() != link_auth.generate_link_secret()


def test_read_link_secret_from_env_present(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(link_auth.ENV_LINK_SECRET, "abc123")
    assert link_auth.read_link_secret_from_env() == "abc123"


def test_read_link_secret_from_env_absent(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv(link_auth.ENV_LINK_SECRET, raising=False)
    assert link_auth.read_link_secret_from_env() is None


def test_verify_link_secret_matching() -> None:
    assert link_auth.verify_link_secret(expected="s3cr3t", presented="s3cr3t") is True


def test_verify_link_secret_wrong() -> None:
    assert link_auth.verify_link_secret(expected="s3cr3t", presented="nope") is False


def test_verify_link_secret_missing() -> None:
    """``presented=None`` (a pre-2.4 Hello, or a core that sent nothing) always fails."""
    assert link_auth.verify_link_secret(expected="s3cr3t", presented=None) is False


def test_authorize_peer_matching_pids() -> None:
    assert link_auth.authorize_peer(observed_pid=123, supervised_pid=123) is True


def test_authorize_peer_mismatched_pids() -> None:
    assert link_auth.authorize_peer(observed_pid=123, supervised_pid=456) is False


def test_authorize_peer_observed_unknown() -> None:
    """An unverifiable peer credential (SO_PEERCRED unavailable/raised) fails CLOSED."""
    assert link_auth.authorize_peer(observed_pid=None, supervised_pid=123) is False


def test_authorize_peer_supervised_unknown() -> None:
    """No core spawned yet (or holder empty) fails CLOSED, never treated as a match."""
    assert link_auth.authorize_peer(observed_pid=123, supervised_pid=None) is False


def test_authorize_peer_both_unknown() -> None:
    assert link_auth.authorize_peer(observed_pid=None, supervised_pid=None) is False


# --- peer_pid ---------------------------------------------------------------


def test_peer_pid_over_a_real_socketpair_returns_this_process_pid() -> None:
    """Both ends of a socketpair are THIS test process -- SO_PEERCRED must
    report `os.getpid()` exactly, over a real (not mocked) kernel call."""
    if not hasattr(socket, "SO_PEERCRED"):
        import pytest

        pytest.skip("SO_PEERCRED not available on this platform")
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert link_auth.peer_pid(a) == os.getpid()
        assert link_auth.peer_pid(b) == os.getpid()
    finally:
        a.close()
        b.close()


def test_peer_pid_none_socket_returns_none() -> None:
    assert link_auth.peer_pid(None) is None


def test_peer_pid_returns_none_when_so_peercred_unavailable(monkeypatch) -> None:  # noqa: ANN001
    """Fail-closed on a platform lacking SO_PEERCRED (e.g. macOS) -- never guesses."""
    monkeypatch.delattr(socket, "SO_PEERCRED", raising=False)
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert link_auth.peer_pid(a) is None
    finally:
        a.close()
        b.close()


def test_peer_pid_returns_none_when_getsockopt_raises(monkeypatch) -> None:  # noqa: ANN001
    """Fail-closed when the OS call itself raises -- never propagates, never guesses.

    ``socket.socket`` instances refuse arbitrary attribute assignment (the
    underlying C socket's methods are read-only), so the raise is patched at
    the CLASS level instead of the instance.
    """
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        def _raising_getsockopt(self: socket.socket, *_args: object, **_kwargs: object) -> bytes:
            raise OSError("simulated getsockopt failure")

        monkeypatch.setattr(socket.socket, "getsockopt", _raising_getsockopt)
        assert link_auth.peer_pid(a) is None
    finally:
        a.close()
        b.close()
