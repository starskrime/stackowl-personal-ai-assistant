"""S2 F-58 / F-59 — a parse-failed synthesis is degraded, never pelletized.

F-58: ``SynthesisParser`` previously returned a fallback ``SynthesisResult`` that
was *type-identical* to a real verdict (consensus = raw text first 200 chars,
recommendation = "See synthesis above"). The orchestrator then marked the session
``completed`` and staged the fabricated body as durable knowledge. The fix carries
an explicit ``parse_ok`` flag on ``SynthesisResult`` so the fallback is marked
not-trustworthy, the orchestrator marks the session ``completed_no_synthesis``, and
pelletization is skipped.

F-59: ``KnowledgePelletGenerator.from_parliament`` must independently gate staging
on ``parse_ok`` AND a confidence floor, so the truncated fallback body is never
persisted as a confidence=0.7 trust="self" durable fact.
"""

from __future__ import annotations

import pytest

from stackowl.parliament.models import ParliamentRound, ParliamentSession
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.pellet_generator import KnowledgePelletGenerator
from stackowl.parliament.session_store import SessionStore
from stackowl.parliament.synthesis_models import SynthesisResult
from stackowl.parliament.synthesis_parser import SynthesisParser
from tests._story_6_7_helpers import (  # noqa: F401 — fixture re-exports
    FakeBridge,
    db,
    no_test_mode_guard,
)


class _MemStore(SessionStore):
    def __init__(self) -> None:
        self.final: ParliamentSession | None = None

    async def create(self, session: ParliamentSession) -> None:
        pass

    async def update_rounds(self, session: ParliamentSession) -> None:
        pass

    async def update_final(self, session: ParliamentSession) -> None:
        self.final = session

    async def get(self, session_id: str) -> ParliamentSession | None:
        return self.final

    async def list_recent(self, limit: int = 10) -> list[ParliamentSession]:
        return [self.final] if self.final else []


def _session() -> ParliamentSession:
    return ParliamentSession(
        topic="t",
        owl_names=["a", "b"],
        session_id="sess-degraded",
        rounds=[
            ParliamentRound(
                round_number=1,
                responses={"a": "x", "b": "y"},
                truncated={"a": False, "b": False},
            )
        ],
    )


# ---------------------------------------------------------------------------
# F-58 — SynthesisResult carries an explicit parse_ok flag
# ---------------------------------------------------------------------------


def test_synthesis_result_parse_ok_defaults_true() -> None:
    result = SynthesisResult(
        consensus="c",
        disagreements=[],
        recommendation="r",
        confidence=0.8,
        synthesis_text="t",
    )
    assert result.parse_ok is True


def test_parser_success_sets_parse_ok_true() -> None:
    parsed = SynthesisParser().parse(
        "CONSENSUS: we agree\nRECOMMENDATION: ship\n◆", "sid"
    )
    assert parsed.parse_ok is True
    assert parsed.consensus == "we agree"


def test_parser_fallback_sets_parse_ok_false() -> None:
    # No CONSENSUS:/RECOMMENDATION: markers -> the parser falls back. The fallback
    # must be marked not-trustworthy while still keeping the text for display.
    parsed = SynthesisParser().parse("just some unstructured model rambling", "sid")
    assert parsed.parse_ok is False
    assert parsed.synthesis_text  # fallback text kept for display


# ---------------------------------------------------------------------------
# F-58 — orchestrator marks degraded + skips pellets on a parse failure
# ---------------------------------------------------------------------------


class _DegradedSynth:
    async def synthesize(self, session: ParliamentSession) -> SynthesisResult:
        return SynthesisResult(
            consensus="truncated raw fallback body",
            disagreements=[],
            recommendation="See synthesis above",
            confidence=0.7,
            synthesis_text="raw fallback ◆",
            parse_ok=False,
        )


class _SpyPellet:
    def __init__(self) -> None:
        self.called = False

    async def from_parliament(self, session: object, result: object) -> None:
        self.called = True


@pytest.mark.asyncio
async def test_orchestrator_degraded_parse_marks_no_synthesis_and_skips_pellets() -> None:
    store = _MemStore()
    spy = _SpyPellet()
    orch = ParliamentOrchestrator(
        backend=object(),  # type: ignore[arg-type]
        session_store=store,
        synthesizer=_DegradedSynth(),  # type: ignore[arg-type]
        pellet_generator=spy,  # type: ignore[arg-type]
    )
    final = await orch._finalize_session(_session())  # noqa: SLF001
    assert final.status == "completed_no_synthesis"
    assert final.synthesis is None
    assert spy.called is False  # fabricated claims never staged


# ---------------------------------------------------------------------------
# F-59 — pellet generator independently gates on parse_ok + confidence floor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pellet_generator_skips_when_parse_failed() -> None:
    bridge = FakeBridge()
    gen = KnowledgePelletGenerator(memory_bridge=bridge)
    synthesis = SynthesisResult(
        consensus="truncated raw fallback body[:200]",
        disagreements=[],
        recommendation="See synthesis above",
        confidence=0.7,
        synthesis_text="raw ◆",
        parse_ok=False,
    )
    await gen.from_parliament(_session(), synthesis)
    assert bridge.staged == []  # nothing fabricated persisted


@pytest.mark.asyncio
async def test_pellet_generator_skips_when_below_confidence_floor() -> None:
    bridge = FakeBridge()
    gen = KnowledgePelletGenerator(memory_bridge=bridge)
    synthesis = SynthesisResult(
        consensus="low confidence consensus",
        disagreements=[],
        recommendation="ship",
        confidence=0.2,
        synthesis_text="body ◆",
        parse_ok=True,
    )
    await gen.from_parliament(_session(), synthesis)
    assert bridge.staged == []


@pytest.mark.asyncio
async def test_pellet_generator_stages_trustworthy_synthesis() -> None:
    bridge = FakeBridge()
    gen = KnowledgePelletGenerator(memory_bridge=bridge)
    synthesis = SynthesisResult(
        consensus="we agree X",
        disagreements=[],
        recommendation="ship",
        confidence=0.8,
        synthesis_text="body ◆",
        parse_ok=True,
    )
    await gen.from_parliament(_session(), synthesis)
    assert len(bridge.staged) == 1
    assert bridge.staged[0].content == "we agree X"
