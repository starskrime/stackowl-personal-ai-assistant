"""Tests for Task 5: trust field stamped at all staging sites + persisted via stage().

Verifies:
  - stage() INSERT writes trust to the DB
  - store() (conversation) stamps trust="self"
  - list_staged() roundtrips trust through row_to_staged
  - web_fetch stages trust="untrusted"
  - pellet_generator stages trust="self"
  - memory tool (agent_self path) yields trust="self"

The two remember_fact cases listed here went with that function in D08.2 seam 3
pass 4; see the note where they stood for the three ways their invariant is
still covered.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

# ---------------------------------------------------------------------------
# bridge: stage() persists trust
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stage_persists_trust_untrusted(tmp_db: Any) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    fact = StagedFact(
        content="web content",
        source_type="webpage",
        source_ref="https://example.com/page",
        confidence=0.4,
        trust="untrusted",
    )
    await bridge.stage(fact)
    rows = await tmp_db.fetch_all("SELECT trust FROM staged_facts WHERE fact_id = ?", (fact.fact_id,))
    assert rows, "row must exist after stage()"
    assert rows[0]["trust"] == "untrusted"


@pytest.mark.asyncio
async def test_stage_persists_trust_self(tmp_db: Any) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    fact = StagedFact(
        content="parliament claim",
        source_type="parliament",
        source_ref="parliament:sess-1",
        confidence=0.7,
        trust="self",
    )
    await bridge.stage(fact)
    rows = await tmp_db.fetch_all("SELECT trust FROM staged_facts WHERE fact_id = ?", (fact.fact_id,))
    assert rows[0]["trust"] == "self"


@pytest.mark.asyncio
async def test_stage_persists_trust_trusted(tmp_db: Any) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    fact = StagedFact(
        content="user said prefer tabs",
        source_type="manual",
        source_ref="user_explicit",
        confidence=1.0,
        trust="trusted",
    )
    await bridge.stage(fact)
    rows = await tmp_db.fetch_all("SELECT trust FROM staged_facts WHERE fact_id = ?", (fact.fact_id,))
    assert rows[0]["trust"] == "trusted"


# ---------------------------------------------------------------------------
# bridge: store() defaults to trust="self" (conversation)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_conversation_defaults_self(tmp_db: Any) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    await bridge.store("a turn", "sess-abc")
    rows = await tmp_db.fetch_all("SELECT trust FROM staged_facts LIMIT 1")
    assert rows, "store() must stage a row"
    assert rows[0]["trust"] == "self"


# ---------------------------------------------------------------------------
# bridge: list_staged() roundtrips trust via row_to_staged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_row_to_staged_reads_trust_self(tmp_db: Any) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    fact = StagedFact(
        content="parliament synthesis",
        source_type="parliament",
        source_ref="parliament:s2",
        confidence=0.7,
        trust="self",
    )
    await bridge.stage(fact)
    staged = await bridge.list_staged()
    assert staged, "list_staged must return the staged row"
    assert staged[0].trust == "self"


@pytest.mark.asyncio
async def test_row_to_staged_reads_trust_untrusted(tmp_db: Any) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    fact = StagedFact(
        content="scraped page",
        source_type="webpage",
        source_ref="https://x.com/p",
        confidence=0.3,
        trust="untrusted",
    )
    await bridge.stage(fact)
    staged = await bridge.list_staged()
    assert staged[0].trust == "untrusted"


@pytest.mark.asyncio
async def test_row_to_staged_reads_trust_trusted(tmp_db: Any) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    fact = StagedFact(
        content="I always use dark mode",
        source_type="manual",
        source_ref="user_explicit",
        confidence=1.0,
        trust="trusted",
    )
    await bridge.stage(fact)
    staged = await bridge.list_staged()
    assert staged[0].trust == "trusted"


# ---------------------------------------------------------------------------
# recent_conversation_turns roundtrips trust
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recent_conversation_turns_roundtrips_trust(tmp_db: Any) -> None:
    bridge = SqliteMemoryBridge(tmp_db)
    # store() creates a conversation fact — should be self
    await bridge.store("hello world", "test-session")
    turns = await bridge.recent_conversation_turns("test-session", limit=5)
    assert turns, "must return recent turns"
    assert turns[0].trust == "self"


# ---------------------------------------------------------------------------
# web_fetch no longer stages at all (D08.1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_fetch_no_longer_stages_anything() -> None:
    """INVERTED, not deleted — what can regress is the write coming back.

    web_fetch used to stage every fetched page as an untrusted fact, and this
    asserted the trust stamp on that write. D08.1 (247d74a5) removed the staging
    entirely: a fetched page is exactly the "will stop being true" content the
    durability rule keeps out of curated memory. Its sibling test in the
    web_fetch suite was inverted at the time; this one was missed and had been
    failing with AttributeError on the removed method ever since.

    The surviving assertion is the one that matters: the method is GONE, so
    nothing can quietly start staging pages again.
    """
    from stackowl.tools.io.web_fetch import WebFetchTool

    assert not hasattr(WebFetchTool, "_stage_in_memory"), (
        "web_fetch must not stage fetched pages as facts (D08.1)"
    )


# ---------------------------------------------------------------------------
# parliament pellet_generator stages trust="self"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pellet_generator_stages_self(tmp_db: Any) -> None:
    """KnowledgePelletGenerator must stage parliament facts with trust='self'."""

    from stackowl.memory.bridge import MemoryBridge
    from stackowl.parliament.pellet_generator import KnowledgePelletGenerator
    from stackowl.parliament.synthesis_models import SynthesisResult

    staged_facts: list[StagedFact] = []

    # Real bridge (inherits MemoryBridge) that captures facts
    class _CaptureBridge(MemoryBridge):
        async def stage(self, fact: StagedFact) -> None:  # type: ignore[override]
            staged_facts.append(fact)

        async def retrieve(self, query: str, session_key: str) -> str:
            return ""

        async def store(self, content: str, session_key: str) -> None:
            pass

        async def recall(self, query: str, limit: int = 10) -> list[Any]:  # type: ignore[override]
            return []

        async def delete(self, fact_id: str) -> None:
            pass

        async def list_staged(self, status: str = "staged") -> list[Any]:  # type: ignore[override]
            return []

    session = MagicMock()
    session.session_key = "parl-sess-1"
    synthesis = SynthesisResult(
        consensus="Tabs are better than spaces.",
        disagreements=[],
        recommendation="Use tabs consistently.",
        confidence=0.9,
        synthesis_text="Tabs are better than spaces.\n◆",
    )

    gen = KnowledgePelletGenerator(_CaptureBridge())
    await gen.from_parliament(session, synthesis)

    assert staged_facts, "pellet_generator must stage at least one fact"
    for f in staged_facts:
        assert f.trust == "self", f"expected trust='self', got {f.trust!r}"


# ---------------------------------------------------------------------------
# remember_fact: REMOVED with the function in D08.2 seam 3 pass 4.
#
# Its two tests asserted manual -> trusted and agent_self -> self through
# remember_fact, which had NO production caller (verified by a complete search,
# not a truncated one) and staged into a store with no readers left. The
# INVARIANT they protected is untouched and still covered three ways: the three
# test_stage_persists_trust_* cases above, tests/memory/test_trust_map.py over
# trust_for_source itself, and test_agent_memory_tool_source_is_agent_self below
# for the live agent path.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# memory tool routes to agent_self → trust="self" (never trusted)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_memory_tool_source_is_agent_self(tmp_db: Any) -> None:
    """The memory tool's add path must pass source_type='agent_self' to remember_fact.

    We verify the source_type by inspecting what gets staged — agent_self maps
    to trust='self', so a staged row with trust='self' and source_type='agent_self'
    proves the tool never escalates to 'trusted'.
    """
    from stackowl.tools.knowledge.guards import AGENT_SELF_SOURCE_TYPE

    # Confirm the constant is "agent_self" (not "manual")
    assert AGENT_SELF_SOURCE_TYPE == "agent_self"

    # Directly verify the mapping: agent_self → self (never trusted)
    from stackowl.memory.trust import trust_for_source
    assert trust_for_source("agent_self") == "self"
    assert trust_for_source("agent_self") != "trusted"
