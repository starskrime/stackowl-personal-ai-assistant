"""A DNS failure must not be reported to the model as a blocked URL.

FROM BAKIR'S STALLED TURN, 2026-08-24, trace 0e568f1ad39a4da485aa5552ac279f3b.
`web_fetch` told the agent, three times:

    URL blocked by egress policy: host 'www.hcpdirectory.cigna.com' did not resolve

Both halves of that sentence are true and together they are misleading. The guard
refused because DNS returned nothing — which is correct, you cannot validate an
address you cannot resolve — but the agent was told it had hit an EGRESS POLICY.
Those have opposite remedies: a policy refusal means stop asking, a name that does
not resolve means the hostname is wrong, try another.

MEASURED: `www.hcpdirectory.cigna.com` does not resolve; `hcpdirectory.cigna.com`
resolves perfectly well. Only the `www.` subdomain is absent. The agent, reading a
policy refusal, had no reason to vary the hostname — so it went back to the
browser, burned its remaining steps on a recycling runtime, and the turn hit its
step cap with no answer.

The same wording sent the person debugging it to the wrong hypothesis too: I went
looking for an over-strict egress rule on a legitimate healthcare directory. A
message that misnames its own cause costs its reader the same detour every time.

These tests drive the REAL SsrfGuard against a hostname that genuinely does not
resolve, rather than asserting on a hand-written reason string — the guard's
wording is what is under test, so a fixture that supplied it would test nothing.
"""

from __future__ import annotations

import pytest

from stackowl.tools.io.web_fetch import WebFetchTool

#: Reserved by RFC 2606 precisely so it can never resolve.
_UNRESOLVABLE = "https://nonexistent-host-for-tests.invalid/page"
#: RFC 5737 documentation address — resolves as a literal, refused by POLICY.
_BLOCKED_BY_POLICY = "http://127.0.0.1:8080/admin"


@pytest.mark.asyncio
async def test_a_dns_failure_says_the_hostname_does_not_exist() -> None:
    result = await WebFetchTool().execute(url=_UNRESOLVABLE)
    assert result.success is False
    err = (result.error or "").lower()
    assert "does not exist" in err or "dns" in err, err


@pytest.mark.asyncio
async def test_a_dns_failure_does_NOT_claim_the_url_was_blocked() -> None:
    """The specific confusion. "blocked by egress policy" tells the agent to stop
    trying; the truth is that a different hostname would work."""
    result = await WebFetchTool().execute(url=_UNRESOLVABLE)
    err = (result.error or "").lower()
    assert "blocked by egress policy" not in err, err


@pytest.mark.asyncio
async def test_it_tells_the_agent_what_to_DO_next() -> None:
    """An error that only names a failure leaves the agent to guess, and this one
    guessed by retrying the same dead host until the budget ran out."""
    result = await WebFetchTool().execute(url=_UNRESOLVABLE)
    err = (result.error or "").lower()
    assert "www." in err, "the www-prefix hint is the one that would have worked here"


@pytest.mark.asyncio
async def test_a_REAL_policy_refusal_still_says_blocked() -> None:
    """The guard's actual job is untouched. A loopback target is an SSRF attempt,
    not a typo, and must still read as a refusal."""
    result = await WebFetchTool().execute(url=_BLOCKED_BY_POLICY)
    assert result.success is False
    err = (result.error or "").lower()
    assert "blocked" in err, err
    assert "does not exist" not in err, err


@pytest.mark.asyncio
async def test_neither_case_ever_raises() -> None:
    for url in (_UNRESOLVABLE, _BLOCKED_BY_POLICY, "not-a-url", ""):
        result = await WebFetchTool().execute(url=url)
        assert result.success is False
