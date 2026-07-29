"""D01.1 slice 2 — persistence for the per-session system prompt.

The prompt is PERSISTED rather than held in memory. Hermes keeps an in-memory LRU
of live agent objects because their gateway restarts rarely; StackOwl's core
``os.execv``s itself on every code change, so an in-memory cache would be
discarded continuously — during exactly the development in which stable
measurements matter most.

The row is keyed ``(session_key, owl_name)`` and STAMPED with the incarnation it
was built for. That stamp is what makes invariant I6 self-enforcing: when a
rollover mints a new ``session_id`` the stored prompt no longer matches, so the
next turn cold-builds. No invalidation job, no listener, no way to forget — the
same trick D01.7 used for ``summary_enqueued_for``, where storing the incarnation
rather than a timestamp is what made the question answerable from the row.

No ``owner_id`` here, deliberately: the parent ``sessions`` table has none either.
D01.7 scopes a lane by ``identity_key``, and a prompt table that invented a
different scoping model would be the more confusing choice.
"""

from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.sessions.prompt_store import SessionPromptStore

LANE = "owl:secretary:telegram:dm:72055773"
RUN = "20260727_040000_abcd1234"
NEXT_RUN = "20260728_040000_ffff9999"


async def test_a_saved_prompt_comes_back(tmp_db: DbPool) -> None:
    store = SessionPromptStore(tmp_db)

    await store.save(session_key=LANE, owl_name="secretary", session_id=RUN,
                     prompt_text="you are a helpful owl", model_window=32768)
    got = await store.load(session_key=LANE, owl_name="secretary", session_id=RUN)

    assert got is not None
    assert got.prompt_text == "you are a helpful owl"
    assert got.model_window == 32768
    assert got.session_id == RUN


async def test_an_unknown_lane_has_no_prompt(tmp_db: DbPool) -> None:
    store = SessionPromptStore(tmp_db)

    assert await store.load(session_key=LANE, owl_name="secretary", session_id=RUN) is None


async def test_a_new_incarnation_does_not_inherit_the_old_prompt(tmp_db: DbPool) -> None:
    """Invariant I6, and the reason the incarnation is STAMPED on the row.

    A rollover keeps the lane and mints a new session_id. The stored prompt was
    built for the previous conversation, so it must not be served to the new one
    — otherwise a session boundary would change nothing about the prompt, which
    is the whole point of having a boundary.
    """
    store = SessionPromptStore(tmp_db)
    await store.save(session_key=LANE, owl_name="secretary", session_id=RUN,
                     prompt_text="built for yesterday", model_window=None)

    assert await store.load(session_key=LANE, owl_name="secretary",
                            session_id=NEXT_RUN) is None
    # ...and the old incarnation can still read its own, so this is a MISMATCH
    # rule rather than a destructive one.
    assert await store.load(session_key=LANE, owl_name="secretary",
                            session_id=RUN) is not None


async def test_two_owls_on_one_lane_keep_separate_prompts(tmp_db: DbPool) -> None:
    """Invariant I6's other half, and not hypothetical: the staged RCA runs three
    owls (gatherer, hypothesis, verifier) against ONE incident session_key, which
    the live logs show as three different persona_len values on the same lane."""
    store = SessionPromptStore(tmp_db)

    await store.save(session_key=LANE, owl_name="secretary", session_id=RUN,
                     prompt_text="secretary prompt", model_window=None)
    await store.save(session_key=LANE, owl_name="scout", session_id=RUN,
                     prompt_text="scout prompt", model_window=None)

    sec = await store.load(session_key=LANE, owl_name="secretary", session_id=RUN)
    scout = await store.load(session_key=LANE, owl_name="scout", session_id=RUN)
    assert sec is not None and scout is not None
    assert sec.prompt_text == "secretary prompt"
    assert scout.prompt_text == "scout prompt"


async def test_rebuilding_replaces_rather_than_accumulates(tmp_db: DbPool) -> None:
    """One row per (lane, owl). A cold rebuild after a boundary must not leave the
    previous incarnation's row behind to be picked up later."""
    store = SessionPromptStore(tmp_db)

    await store.save(session_key=LANE, owl_name="secretary", session_id=RUN,
                     prompt_text="first", model_window=None)
    await store.save(session_key=LANE, owl_name="secretary", session_id=NEXT_RUN,
                     prompt_text="second", model_window=None)

    rows = await tmp_db.fetch_all(
        "SELECT COUNT(*) n FROM session_prompts WHERE session_key = ? AND owl_name = ?",
        (LANE, "secretary"),
    )
    assert rows[0]["n"] == 1
    got = await store.load(session_key=LANE, owl_name="secretary", session_id=NEXT_RUN)
    assert got is not None
    assert got.prompt_text == "second"


async def test_the_stored_hash_identifies_the_stored_text(tmp_db: DbPool) -> None:
    """The hash is what invariant I1 is measured with, so it must be derived from
    the text rather than supplied alongside it and trusted."""
    from stackowl.infra.prompt_metrics import digest

    store = SessionPromptStore(tmp_db)
    await store.save(session_key=LANE, owl_name="secretary", session_id=RUN,
                     prompt_text="you are a helpful owl", model_window=None)

    got = await store.load(session_key=LANE, owl_name="secretary", session_id=RUN)
    assert got is not None
    assert got.prompt_hash == digest("you are a helpful owl")


async def test_an_empty_prompt_is_not_persisted(tmp_db: DbPool) -> None:
    """A cold build that produced nothing is a FAILURE to freeze, not a frozen
    empty prompt. Persisting it would pin the failure for the whole session."""
    store = SessionPromptStore(tmp_db)

    await store.save(session_key=LANE, owl_name="secretary", session_id=RUN,
                     prompt_text="", model_window=None)

    assert await store.load(session_key=LANE, owl_name="secretary", session_id=RUN) is None


# ---------------------------------------------------------------------------
# D01.4 — invalidation, the seam D01.1 never built.
#
# Until this existed, NOTHING in the tree could clear a frozen prompt: the store
# only ever INSERTed/UPSERTed, and the sole release was a rollover minting a new
# session_id. So an owl edit was invisible to the conversation you made it in —
# for up to twelve hours, since D01.7 rolls daily at 04:00.
# ---------------------------------------------------------------------------

OTHER_LANE = "owl:secretary:cli:dm:1"


async def _freeze(store: SessionPromptStore, lane: str, owl: str) -> None:
    await store.save(
        session_key=lane, owl_name=owl, session_id=RUN,
        prompt_text=f"prompt for {owl} on {lane}", model_window=None,
    )


async def test_invalidating_an_owl_clears_it_on_every_lane(tmp_db: DbPool) -> None:
    """You edited the OWL, not one conversation.

    Anything narrower lets telegram-secretary and cli-secretary silently disagree
    about who they are, which is the harder bug to diagnose.
    """
    store = SessionPromptStore(tmp_db)
    await _freeze(store, LANE, "secretary")
    await _freeze(store, OTHER_LANE, "secretary")

    cleared = await store.invalidate_owl(owl_name="secretary", cause="owl_edit")

    assert cleared == 2
    assert await store.load(session_key=LANE, owl_name="secretary", session_id=RUN) is None
    assert await store.load(session_key=OTHER_LANE, owl_name="secretary", session_id=RUN) is None


async def test_invalidating_an_owl_leaves_other_owls_alone(tmp_db: DbPool) -> None:
    """A lane can run several owls — the staged RCA drives three against one."""
    store = SessionPromptStore(tmp_db)
    await _freeze(store, LANE, "secretary")
    await _freeze(store, LANE, "researcher")

    await store.invalidate_owl(owl_name="secretary", cause="owl_edit")

    assert await store.load(session_key=LANE, owl_name="secretary", session_id=RUN) is None
    survivor = await store.load(session_key=LANE, owl_name="researcher", session_id=RUN)
    assert survivor is not None, "editing one owl must not clear another's prompt"


async def test_invalidate_all_clears_every_owl_and_lane(tmp_db: DbPool) -> None:
    """The skills catalogue and capabilities block are machine-wide facts, so
    every prompt containing them genuinely IS stale after an install."""
    store = SessionPromptStore(tmp_db)
    await _freeze(store, LANE, "secretary")
    await _freeze(store, OTHER_LANE, "researcher")

    cleared = await store.invalidate_all(cause="skill_install")

    assert cleared == 2
    assert await store.load(session_key=LANE, owl_name="secretary", session_id=RUN) is None
    assert await store.load(session_key=OTHER_LANE, owl_name="researcher", session_id=RUN) is None


async def test_invalidating_nothing_is_not_an_error(tmp_db: DbPool) -> None:
    """I4 — background lanes never froze a prompt (DEBT-27: empty session_id), so
    a delete clears 0 rows and costs nothing. It starts mattering automatically
    once DEBT-27 lands, with no change here."""
    store = SessionPromptStore(tmp_db)
    assert await store.invalidate_owl(owl_name="nobody", cause="owl_edit") == 0
    assert await store.invalidate_all(cause="skill_install") == 0


async def test_invalidation_is_idempotent(tmp_db: DbPool) -> None:
    store = SessionPromptStore(tmp_db)
    await _freeze(store, LANE, "secretary")
    assert await store.invalidate_owl(owl_name="secretary", cause="owl_edit") == 1
    assert await store.invalidate_owl(owl_name="secretary", cause="owl_edit") == 0


async def test_a_rebuild_after_invalidation_is_stored_again(tmp_db: DbPool) -> None:
    """Invalidation reuses the existing miss path — the row simply reappears."""
    store = SessionPromptStore(tmp_db)
    await _freeze(store, LANE, "secretary")
    await store.invalidate_owl(owl_name="secretary", cause="owl_edit")

    await store.save(
        session_key=LANE, owl_name="secretary", session_id=RUN,
        prompt_text="the REBUILT prompt", model_window=None,
    )
    found = await store.load(session_key=LANE, owl_name="secretary", session_id=RUN)
    assert found is not None
    assert found.prompt_text == "the REBUILT prompt"


async def test_a_delete_failure_never_raises(tmp_db: DbPool) -> None:
    """I3 — the edit persisted; losing the cache clear must not fail the turn."""
    await tmp_db.execute("DROP TABLE session_prompts")
    store = SessionPromptStore(tmp_db)
    assert await store.invalidate_owl(owl_name="secretary", cause="owl_edit") == 0
    assert await store.invalidate_all(cause="skill_install") == 0


async def test_invalidating_records_the_explanation_for_the_audit(tmp_db: DbPool) -> None:
    """D01.4 — the store itself notes WHY, so D01.2's audit does not warn about a
    change the user asked for.

    Tested here rather than only at the audit's own unit level because this is
    the seam that carries it: if invalidate_owl stopped noting, the audit would
    warn on every deliberate edit and nothing in cache_audit's own tests would
    notice.
    """
    from stackowl.infra.prompt_invalidation import (
        reset_expected_changes,
        take_expected_change,
    )

    reset_expected_changes()
    store = SessionPromptStore(tmp_db)
    await _freeze(store, LANE, "secretary")

    await store.invalidate_owl(owl_name="secretary", cause="owl_edit")

    assert take_expected_change("secretary") == "owl_edit"
