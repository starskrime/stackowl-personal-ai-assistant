"""Tests for ConsentScope.DENY_SESSION — session-wide deny policy.

DENY_SESSION must:
  (i)  deny the current call AND suppress further prompts for that tool in
       the same session (no second prompt);
  (ii) be scoped per session_id — a different session still prompts;
  (iii) work even for always-ask/excluded tools (deny is purely restrictive);
  (iv) not interfere with SESSION approve, which still records allow-batch and
       lets a different tool prompt normally.
"""

from __future__ import annotations

from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope


class _CountingPrompter:
    """Returns a scripted sequence of scopes; counts every prompt call."""

    def __init__(self, *scopes: ConsentScope) -> None:
        self._queue: list[ConsentScope] = list(scopes)
        self.call_count = 0
        self.requests: list[ConsentRequest] = []

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.call_count += 1
        self.requests.append(req)
        if self._queue:
            return self._queue.pop(0)
        # Default to DENY if the script runs out (should not happen in well-formed tests)
        return ConsentScope.DENY


# ---------------------------------------------------------------------------
# (i) DENY_SESSION denies current call and suppresses subsequent prompts
# ---------------------------------------------------------------------------


async def test_deny_session_denies_current_call() -> None:
    """DENY_SESSION must return False for the triggering request."""
    prompter = _CountingPrompter(ConsentScope.DENY_SESSION)
    policy = ConsentPolicy(prompter=prompter)
    result = await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    assert result is False


async def test_deny_session_suppresses_subsequent_prompts_same_tool() -> None:
    """After a DENY_SESSION the policy must not call the prompter again for
    the same tool+session — it short-circuits to deny silently."""
    prompter = _CountingPrompter(ConsentScope.DENY_SESSION)
    policy = ConsentPolicy(prompter=prompter)

    first = await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    assert first is False
    assert prompter.call_count == 1  # prompted once

    second = await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    assert second is False
    assert prompter.call_count == 1  # NOT prompted again — short-circuit applied


async def test_deny_session_third_call_also_silent() -> None:
    """The short-circuit persists for the whole session (more than 2 calls)."""
    prompter = _CountingPrompter(ConsentScope.DENY_SESSION)
    policy = ConsentPolicy(prompter=prompter)

    await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    result = await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    assert result is False
    assert prompter.call_count == 1  # only the first call reached the prompter


# ---------------------------------------------------------------------------
# (ii) DENY_SESSION is scoped per session_id
# ---------------------------------------------------------------------------


async def test_deny_session_does_not_affect_different_session() -> None:
    """A session-deny on s1 must NOT suppress prompts on s2."""
    prompter = _CountingPrompter(ConsentScope.DENY_SESSION, ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter)

    result_s1 = await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    assert result_s1 is False
    assert prompter.call_count == 1

    # s2 is a clean slate — must prompt normally
    result_s2 = await policy.request(tool_name="tool_x", channel="cli", session_id="s2")
    assert result_s2 is True   # ONCE grant
    assert prompter.call_count == 2  # second prompt for s2


# ---------------------------------------------------------------------------
# (iii) DENY_SESSION works for always-ask/excluded tools
# ---------------------------------------------------------------------------


async def test_deny_session_works_for_always_ask_tool() -> None:
    """Deny is purely restrictive — safe to record even for excluded tools."""
    prompter = _CountingPrompter(ConsentScope.DENY_SESSION)
    policy = ConsentPolicy(
        prompter=prompter,
        always_ask_tools=frozenset({"execute_code"}),
    )

    first = await policy.request(tool_name="execute_code", channel="cli", session_id="s1")
    assert first is False
    assert prompter.call_count == 1

    second = await policy.request(tool_name="execute_code", channel="cli", session_id="s1")
    assert second is False
    # Excluded tools normally always re-prompt, but DENY_SESSION short-circuits BEFORE
    # the always-ask check, so the prompter must NOT be called a second time.
    assert prompter.call_count == 1


# ---------------------------------------------------------------------------
# (iv) SESSION approve is unaffected — different tool still prompts normally
# ---------------------------------------------------------------------------


async def test_session_approve_still_records_batch_grant() -> None:
    """A SESSION approve is unaffected by the new DENY_SESSION path.

    After one SESSION approve, the same tool in the same session must NOT
    prompt again (session batch active).  A DIFFERENT tool must still prompt.
    """
    prompter = _CountingPrompter(ConsentScope.SESSION, ConsentScope.ONCE)
    policy = ConsentPolicy(prompter=prompter)

    result_first = await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    assert result_first is True
    assert prompter.call_count == 1

    # Same tool — session batch should skip the prompt
    result_second = await policy.request(tool_name="tool_x", channel="cli", session_id="s1")
    assert result_second is True
    assert prompter.call_count == 1  # no new prompt

    # Different tool — must still prompt
    result_other = await policy.request(tool_name="tool_y", channel="cli", session_id="s1")
    assert result_other is True
    assert prompter.call_count == 2  # prompted for tool_y
