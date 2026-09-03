"""A failure to determine is not a determination, and must not be cached as one.

``host_locality`` classifies a provider's ``base_url`` as self-hosted or cloud.
It resolves the hostname, and its own docstring records what that fix was for::

    base_url  = http://llm-gateway.dev.nera.gov:4000/v1
    resolves  = 172.30.104.100          (RFC1918 — a private host)
    classified= CLOUD                    (hostname is not an IP literal)
    result    = every call billed at the unknown-cloud fallback rate
                ~$2,328 of imaginary spend in 10 days, 82,016 rows

The resolution path fails safe to cloud, correctly, and says why: "A DNS failure
is not evidence of locality". Then it stores that non-evidence in the same cache
as a determination::

    except Exception as exc:
        log.engine.warning("[host_locality] could not resolve host ...")
        _RESOLVED[host] = False      # <- the failure, for the process lifetime
        return False

MEASURED 2026-09-03. That exact host is NXDOMAIN — the VPN tunnel is up and the
corporate resolvers have no ``nera.gov`` zone at all — and the warning appears 17
times in a two-hour window. So every running process has now cached
``llm-gateway.dev.nera.gov -> cloud``.

THE COST IS THE ONE THIS MODULE EXISTS TO PREVENT. The cache is checked BEFORE
the lookup is attempted, so the entry can never be corrected by a later success.
The moment DNS returns, the process keeps billing at the unknown-cloud fallback
rate until somebody restarts it — silently, because the warning only fires on the
first lookup per host per process. A transient outage becomes a permanent
misclassification, and the misclassification invents money.

THE ASYMMETRY IS THE POINT, and it is why the fix is not "cache for less time".
A POSITIVE answer is a fact about the network that does not change while the
process runs — caching it for the process lifetime is right and stays. A NEGATIVE
answer is the absence of an answer. One cache was holding both, which is one
field carrying two meanings.

WHAT THIS DOES NOT CHANGE. Failing safe to cloud is still correct: for the egress
consumer (``vision/selector``) guessing "local" would send an image off-network
while telling the user it stayed, so an unresolvable host must still read as
cloud on that call. It just stops being the answer forever.
"""

from __future__ import annotations

import socket

import pytest

from stackowl.infra.net import host_locality

PRIVATE_HOST = "llm-gateway.dev.nera.gov"
URL = f"http://{PRIVATE_HOST}:4000/v1"


@pytest.fixture(autouse=True)
def _clear_cache():
    host_locality._RESOLVED.clear()
    yield
    host_locality._RESOLVED.clear()


def _addrinfo(ip: str) -> list:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


def test_a_failed_lookup_does_not_poison_the_process(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE DEFECT. The live shape: DNS is down, then comes back. A cached failure
    made the second answer unreachable, and the consequence is the $2,328 of
    imaginary spend this module was fixed to prevent."""
    calls = {"n": 0}

    def _flaky(host: str, *_a: object, **_k: object) -> list:
        calls["n"] += 1
        if calls["n"] == 1:
            raise socket.gaierror("[Errno -2] Name or service not known")
        return _addrinfo("172.30.104.100")

    monkeypatch.setattr(socket, "getaddrinfo", _flaky)

    assert host_locality.is_local_url(URL) is False, "an unresolvable host must read as cloud"
    assert host_locality.is_local_url(URL) is True, (
        "DNS came back and the classification did not — a transient failure was "
        "cached as though it were an answer"
    )


def test_a_determined_answer_is_still_cached(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE HALF THAT MUST NOT CHANGE. A resolved host is a fact about the network
    that does not move while the process runs; re-resolving it on every call
    would put a blocking lookup on the pricing and egress paths."""
    calls = {"n": 0}

    def _once(host: str, *_a: object, **_k: object) -> list:
        calls["n"] += 1
        return _addrinfo("172.30.104.100")

    monkeypatch.setattr(socket, "getaddrinfo", _once)

    assert host_locality.is_local_url(URL) is True
    assert host_locality.is_local_url(URL) is True
    assert calls["n"] == 1, f"a settled answer was re-resolved {calls['n']} times"


def test_a_cloud_answer_is_cached_too(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """"Cloud" reached by RESOLVING is a determination, not a failure — a public
    address is an answer. It must cache like any other."""
    calls = {"n": 0}

    def _public(host: str, *_a: object, **_k: object) -> list:
        calls["n"] += 1
        return _addrinfo("93.184.216.34")

    monkeypatch.setattr(socket, "getaddrinfo", _public)

    assert host_locality.is_local_url(URL) is False
    assert host_locality.is_local_url(URL) is False
    assert calls["n"] == 1, "a resolved public address is an answer and must cache"


def test_the_failure_still_reads_as_cloud_on_the_call_that_failed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Failing SAFE is not what changed. For the egress consumer, guessing
    "local" would send an image off-network while telling the user it stayed, so
    an unresolvable host must still answer cloud right now — it just stops being
    the answer forever."""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *_a, **_k: (_ for _ in ()).throw(socket.gaierror("boom")),
    )
    assert host_locality.is_local_url(URL) is False
    assert host_locality.is_local_url(URL) is False


def test_an_ip_literal_never_needs_dns(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The syntactic path predates resolution and must stay free of it — a
    private literal was always classified local, and this change does not widen
    or narrow what "local" means."""
    def _boom(*_a: object, **_k: object) -> list:
        raise AssertionError("an IP literal must not trigger a DNS lookup")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert host_locality.is_local_url("http://172.30.104.100:4000/v1") is True
    assert host_locality.is_local_url("http://localhost:4000/v1") is True
    assert host_locality.is_local_url("http://93.184.216.34/v1") is False
