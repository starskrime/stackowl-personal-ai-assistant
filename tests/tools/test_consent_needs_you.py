"""Story 3.3 (AD-28) -- ``ConsentPolicy.request()`` opens a durable
``approval`` needs_you item and binds its OWN in-memory wait as the
``waiter_kind="turn"`` waiter the moment it starts waiting on its prompter,
then settles it EXCLUSIVELY through ``needs_you.resolve()`` -- acting on
``resolve()``'s own returned outcome, never the raw local prompter answer.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal import needs_you
from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope

pytestmark = pytest.mark.asyncio


class _FixedPrompter:
    def __init__(self, scope: ConsentScope = ConsentScope.ONCE) -> None:
        self._scope = scope
        self.asked = False

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.asked = True
        return self._scope


class _RaisingPrompter:
    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        raise RuntimeError("simulated prompter failure")


class TestCleanWinRoutesThroughTheResolver:
    async def test_allowed_decision_opens_and_resolves_the_item(
        self, tmp_db: DbPool,
    ) -> None:
        policy = ConsentPolicy(prompter=_FixedPrompter(ConsentScope.ONCE), db_pool=tmp_db)

        allowed = await policy.request(
            tool_name="shell", channel="telegram", session_key="s1",
        )

        assert allowed is True
        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE kind = 'approval'",
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["resolved_cursor"] is not None
        assert row["answer"] == "once"
        assert row["resolved_by"] == "consent_decision:telegram"

        resolved_events = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'needs_you.resolved'",
        )
        assert len(resolved_events) == 1

    async def test_denied_decision_still_resolves_the_item(self, tmp_db: DbPool) -> None:
        policy = ConsentPolicy(prompter=_FixedPrompter(ConsentScope.DENY), db_pool=tmp_db)

        allowed = await policy.request(
            tool_name="shell", channel="cli", session_key="s2",
        )

        assert allowed is False
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE kind = 'approval'")
        assert len(rows) == 1
        assert rows[0]["answer"] == "deny"


class TestNoDbPoolIsByteIdentical:
    async def test_no_db_pool_never_touches_needs_you(self) -> None:
        policy = ConsentPolicy(prompter=_FixedPrompter(ConsentScope.ONCE))

        allowed = await policy.request(
            tool_name="shell", channel="cli", session_key="s3",
        )

        assert allowed is True


class TestPrompterRaisingSettlesTheItem:
    """Review pass 1 Group A -- the prompt_error branch must settle the
    bound item, not leave it open until its own HUMAN_DECISION_TIMEOUT_SECONDS
    expiry."""

    async def test_a_raising_prompter_still_leaves_the_item_settled_not_open(
        self, tmp_db: DbPool,
    ) -> None:
        policy = ConsentPolicy(prompter=_RaisingPrompter(), db_pool=tmp_db)

        allowed = await policy.request(
            tool_name="shell", channel="cli", session_key="s4",
        )

        assert allowed is False
        rows = await tmp_db.fetch_all("SELECT * FROM needs_you WHERE kind = 'approval'")
        assert len(rows) == 1
        row = rows[0]
        assert row["resolved_cursor"] is not None, "must settle, never left open"
        assert row["answer"] is None
        assert row["resolved_by"] == "system:consent_prompt_error"


class TestConcurrentDistinctRequestsEachGetTheirOwnAnswer:
    """Review pass 1 Group C -- the regression test for the cross-request
    -contamination bug: two concurrent ConsentPolicy.request() calls with the
    SAME session_key for DIFFERENT tools must each resolve with ITS OWN
    answer, never swap."""

    async def test_two_concurrent_requests_never_swap_answers(
        self, tmp_db: DbPool,
    ) -> None:
        policy_allow = ConsentPolicy(
            prompter=_FixedPrompter(ConsentScope.ONCE), db_pool=tmp_db,
        )
        policy_deny = ConsentPolicy(
            prompter=_FixedPrompter(ConsentScope.DENY), db_pool=tmp_db,
        )

        allowed_a, allowed_b = await asyncio.gather(
            policy_allow.request(tool_name="tool-a", channel="cli", session_key="shared"),
            policy_deny.request(tool_name="tool-b", channel="cli", session_key="shared"),
        )

        assert allowed_a is True, "tool-a's own ONCE answer must never become tool-b's DENY"
        assert allowed_b is False, "tool-b's own DENY answer must never become tool-a's ONCE"

        rows = await tmp_db.fetch_all("SELECT dedupe_key, answer FROM needs_you WHERE kind = 'approval'")
        assert len(rows) == 2, "two distinct decisions must never collapse onto one item"


class TestMalformedResolutionAnswerFailsClosed:
    """Verification-gap: a resolution.answer that is not a valid ConsentScope
    must fail closed rather than raise into the caller (a regression here
    would silently flip fail-closed to fail-open)."""

    async def test_a_malformed_answer_from_resolve_denies(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import stackowl.journal.needs_you as needs_you_module

        real_resolve = needs_you_module.resolve

        async def _garbled_resolve(conn, **kwargs):
            result = await real_resolve(conn, **kwargs)
            return result.model_copy(update={"answer": "not-a-real-scope"})

        monkeypatch.setattr(needs_you_module, "resolve", _garbled_resolve)

        policy = ConsentPolicy(prompter=_FixedPrompter(ConsentScope.ONCE), db_pool=tmp_db)

        allowed = await policy.request(
            tool_name="shell", channel="cli", session_key="s5",
        )

        assert allowed is False, "a malformed resolution.answer must fail closed"


class TestVersionDigestCheckIsReal:
    """Story 3.6 -- the version/digest CHECK ``needs_you.resolve()`` has had
    since Story 3.2 is finally called with real values from
    ``ConsentPolicy.request()``. A prompter that observes ``req.item_id``
    (Story 3.6's own new field) and manually advances that item's version --
    simulating a concurrent resolve landing between prompt-build and the
    tap -- must cause ``resolve()`` to refuse; ``ConsentPolicy.request()``
    must fail closed (``not_approved``), never silently grant."""

    async def test_stale_version_refuses_and_fails_closed(
        self, tmp_db: DbPool,
    ) -> None:
        class _StaleVersionPrompter:
            def __init__(self) -> None:
                self.req: ConsentRequest | None = None

            async def prompt(self, req: ConsentRequest) -> ConsentScope:
                self.req = req
                assert req.item_id is not None, (
                    "Story 3.6: item_id must be threaded onto the request"
                )
                await tmp_db.execute(
                    "UPDATE needs_you SET version = version + 1 WHERE id = ?",
                    (req.item_id,),
                )
                return ConsentScope.ONCE

        prompter = _StaleVersionPrompter()
        policy = ConsentPolicy(prompter=prompter, db_pool=tmp_db)

        allowed = await policy.request(
            tool_name="shell", channel="cli", session_key="s8",
        )

        assert allowed is False, "a stale version must refuse, never silently grant"
        assert prompter.req is not None and prompter.req.item_id is not None
        rows = await tmp_db.fetch_all(
            "SELECT * FROM needs_you WHERE id = ?", (prompter.req.item_id,),
        )
        assert rows[0]["resolved_cursor"] is None, (
            "a refused item stays open, re-shown at its current true version"
        )


class TestResolveOutcomeWinsOverRawPrompterAnswer:
    """Boundaries: resolve()'s own returned outcome -- never the raw local
    prompter answer -- is what the turn acts on. Simulated by settling the
    item as EXPIRED (as a concurrent boot-sweep would) from inside the
    prompter's own call, between open+bind and resolve(), while the prompter
    itself still answers ONCE."""

    async def test_an_item_expired_before_resolve_overrides_the_prompters_once(
        self, tmp_db: DbPool,
    ) -> None:
        class _ExpiringPrompter:
            async def prompt(self, req: ConsentRequest) -> ConsentScope:
                rows = await tmp_db.fetch_all(
                    "SELECT id FROM needs_you WHERE kind = 'approval' "
                    "AND resolved_cursor IS NULL",
                )
                assert len(rows) == 1, "the item must already be open+bound"
                await needs_you.settle_or_abandon(
                    tmp_db, item_id=rows[0]["id"], resolved_by="system:test_race",
                )
                return ConsentScope.ONCE

        policy = ConsentPolicy(prompter=_ExpiringPrompter(), db_pool=tmp_db)

        allowed = await policy.request(
            tool_name="shell", channel="cli", session_key="s7",
        )

        assert allowed is False, (
            "resolve()'s own 'expired' outcome must win over the prompter's "
            "raw ONCE answer"
        )
