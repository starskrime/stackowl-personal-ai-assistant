"""A loopback URL is not a fabricated source.

MEASURED 2026-08-18, after instrumenting the grounding gate to record WHICH urls it
objects to. Eleven consecutive turns of Bakir's Gmail/OAuth setup were floored, and
the flagged urls were::

    ["http://localhost"]
    ["https://accounts.google.com/o/oauth2/v2/auth", "http://localhost"]

``http://localhost`` is an OAuth **redirect URI** — a configuration value the answer
must state for the setup to work. It is not a source, and it could not be one:
nobody can read localhost as evidence for a claim, so treating it as a fabricated
citation is a category error rather than a judgement call.

WHY ONLY LOOPBACK, when accounts.google.com was flagged too. That one IS a real
public URL, and whether an instruction ("go here to authorize") counts as a citation
is a genuine product judgement about a security guardrail — so it is Bakir's call,
raised with the evidence rather than decided here. This change fixes only the part
that is unarguable.

It reuses ``infra.net.host_locality.is_local_url``, the oracle the SSRF guard
already uses. A second opinion about what "local" means is how the two drift.
"""

from __future__ import annotations

from stackowl.pipeline.delivery_gate import _is_uncitable_url


class TestALoopbackUrlCannotBeCited:
    def test_localhost_is_not_a_citation(self) -> None:
        """The exact url that floored eleven turns."""
        assert _is_uncitable_url("http://localhost") is True

    def test_localhost_with_a_port_and_path(self) -> None:
        """An OAuth redirect is usually a full callback address."""
        assert _is_uncitable_url("http://localhost:8080/oauth2callback") is True

    def test_the_loopback_ip_too(self) -> None:
        assert _is_uncitable_url("http://127.0.0.1:9000/") is True


class TestARealSourceIsStillACitation:
    def test_a_public_url_remains_checkable(self) -> None:
        """The gate must keep doing its job. This is the case it exists for — an
        answer citing a page it never fetched."""
        assert _is_uncitable_url("https://example.com/article") is False

    def test_a_google_SEARCH_url_is_still_checked(self) -> None:
        """SUPERSEDED 2026-08-18, and deliberately rewritten rather than deleted.

        This test used to assert accounts.google.com was NOT exempt — a guard I put
        in because widening a security gate was Bakir's decision, not mine. He made
        it ("do both"), so the interactive-endpoint exemption now covers it and the
        old assertion is obsolete.

        What replaces it is the property that still matters: the exemption is
        HOST-scoped to interactive endpoints, not a blanket pass for google.com. A
        search result cited as a source is exactly the fabrication the gate exists
        to block, and it must still be caught.
        """
        assert _is_uncitable_url("https://www.google.com/search?q=oauth") is False


class TestItNeverBreaksTheGate:
    def test_malformed_input_does_not_raise(self) -> None:
        """This runs on every answer containing a url. An exception here would turn
        a grounding check into a lost turn."""
        for bad in ("", "not a url", "http://", "://x", "ht tp://x"):
            assert _is_uncitable_url(bad) in (True, False)
