"""Control-plane test isolation: the secret store, and which state the dashboard is in.

TWO THINGS EVERY TEST HERE NEEDS SINCE Q29.

1. THE OPERATOR'S REAL KEYRING IS OUT OF REACH (BH8). The dashboard password, its
   setup code and the bearer token are all read from and written to the secret
   store, and a working OS keyring would put a test's records in the developer's
   real keyring, outside the per-test ``STACKOWL_HOME``. The keyring is made ABSENT
   (``NoKeyringError`` — no backend at all), so every record takes the 0600-file
   branch under the isolated home. A test about a locked keyring patches it again.

2. A DASHBOARD THAT HAS A PASSWORD, BY DEFAULT. An install with no stored password
   is in setup mode and every data route answers 403, so the files that test the
   DATA start from a ready install. `test_the_dashboard_has_a_login.py` tests the
   GATE and overrides ``dashboard_password`` to start from nothing stored.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

#: The password the default fixture stores.
OWNER_PASSWORD = "an-owner-chosen-password"  # noqa: S105 — test fixture value


@pytest.fixture(autouse=True)
def _no_os_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring
    import keyring.errors

    def _absent(*_a: Any, **_k: Any) -> None:
        raise keyring.errors.NoKeyringError("no OS keyring in this test")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, _absent)


def _store_password(password: str = OWNER_PASSWORD) -> str:
    """Store *password* as a READY record, at a test-only scrypt cost.

    Production parameters take ~66 ms per derivation on this box; a record is
    parsed with the parameters it carries, so n=2 keeps a hundred tests fast while
    exercising the same parse-and-compare path.
    """
    from stackowl.config.secret_writer import store_located_secret
    from stackowl.control_plane.password import HASH_SERVICE, _StoredHash

    salt = b"q29-test-salt-16"
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2, r=1, p=1, dklen=32)
    store_located_secret(HASH_SERVICE, _StoredHash(2, 1, 1, salt, digest).serialise())
    return password


@pytest.fixture
def set_dashboard_password() -> Callable[..., str]:
    """Store a dashboard password on demand; returns the password stored."""
    return _store_password


@pytest.fixture(autouse=True)
def dashboard_password(_no_os_keyring: None) -> str | None:
    """READY by default — see the module docstring."""
    return _store_password()


@pytest.fixture(autouse=True)
async def _settle_setup_code_tasks(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """A refusal in setup mode issues a code in a BACKGROUND task — finish it HERE.

    The task's store write runs in a thread. A test that ended before that write
    landed would have it resolve ``STACKOWL_HOME`` after the root conftest put it
    back — the operator's real home — and nothing would fail. This fixture is set
    up after the isolated home and therefore torn down before it is restored, so
    every such task finishes inside that home, for every file in this directory
    rather than only the one that knew to wait.
    """
    from stackowl.control_plane.server import ControlPlaneServer

    built: list[ControlPlaneServer] = []
    real_init = ControlPlaneServer.__init__

    def _tracking_init(self: ControlPlaneServer, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        built.append(self)

    # THROUGH THE SERVER'S OWN REGISTRY, not a match on the coroutine's name: a
    # rename would have made a name match settle nothing, silently.
    monkeypatch.setattr(ControlPlaneServer, "__init__", _tracking_init)
    yield
    for srv in built:
        await srv._settle_background()  # noqa: SLF001
        assert not srv._background, "a setup-code task outlived its test"  # noqa: SLF001
