"""A URL the user is told to VISIT is not a source the agent claims to have read.

BAKIR, 2026-08-18, after seeing the evidence: do both — the loopback fix and this.

WHAT THE GATE IS FOR. It stops the agent presenting a URL as EVIDENCE for a claim
it never checked: "according to https://example.com/report, revenue doubled", with
no fetch behind it. That is a lie about a source and the gate should keep blocking
it.

WHAT IT WAS ALSO CATCHING. Eleven of Bakir's OAuth turns were floored on::

    https://accounts.google.com/o/oauth2/v2/auth
    http://localhost

Neither is offered as evidence. They are the endpoint he must open in a browser and
the redirect URI he must configure — the ANSWER, not a citation supporting it. A
setup instruction cannot be written without naming where to go, so the gate made
every correct answer to that question unsendable.

THE DISTINCTION DRAWN HERE is a well-known interactive endpoint whose purpose is to
be VISITED — sign-in, consent, console, developer settings. It is deliberately a
narrow, named set rather than a heuristic: "does this url look instructional" is
exactly the kind of fuzzy rule that quietly stops a security gate working. Anything
outside the set is still checked exactly as before.
"""

from __future__ import annotations

from stackowl.pipeline.delivery_gate import _is_uncitable_url


class TestInteractiveEndpointsAreNotCitations:
    def test_the_google_oauth_endpoint(self) -> None:
        """The url that floored eleven of Bakir's turns."""
        assert _is_uncitable_url("https://accounts.google.com/o/oauth2/v2/auth") is True

    def test_the_google_cloud_console(self) -> None:
        assert _is_uncitable_url(
            "https://console.cloud.google.com/apis/credentials"
        ) is True

    def test_a_loopback_redirect_still_passes(self) -> None:
        """The half fixed earlier stays fixed."""
        assert _is_uncitable_url("http://localhost:8080/callback") is True


class TestTheGateStillDoesItsJob:
    def test_an_ordinary_article_is_still_checked(self) -> None:
        """The case the gate exists for. If this ever returns True the guardrail is
        gone and nothing else in the suite would notice."""
        assert _is_uncitable_url("https://example.com/2026/revenue-report") is False

    def test_a_google_SEARCH_result_page_is_still_checked(self) -> None:
        """google.com is not blanket-exempt. A search result cited as a source is
        exactly the fabrication this blocks — only the interactive endpoints are
        exempted, not the domain."""
        assert _is_uncitable_url("https://www.google.com/search?q=revenue") is False

    def test_a_lookalike_domain_is_not_exempted(self) -> None:
        """Matching on a substring would let accounts.google.com.evil.test through.
        The host must match exactly or be a real subdomain."""
        assert _is_uncitable_url("https://accounts.google.com.evil.test/o/oauth2") is False

    def test_a_news_article_about_oauth_is_still_checked(self) -> None:
        """The topic is irrelevant — only the endpoint identity matters."""
        assert _is_uncitable_url("https://news.example.com/how-oauth-works") is False
