"""Story 6.7 (part B) — Parliament → Memory wiring + integration marker."""

from __future__ import annotations

from pathlib import Path

from stackowl.parliament.models import ParliamentSession
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.pellet_generator import (
    KnowledgePelletGenerator,
    NullMemoryBridge,
)
from stackowl.parliament.session_store import SessionStore
from stackowl.parliament.synthesis_models import (
    DisagreementPoint,
    SynthesisResult,
)

from tests._story_6_7_helpers import (  # noqa: F401 — fixture re-exports
    FakeBridge,
    db,
    no_test_mode_guard,
)


# ---------------------------------------------------------------------------
# Parliament memory integration
# ---------------------------------------------------------------------------


async def test_pellet_generator_with_real_bridge_calls_stage() -> None:
    """T14 — KnowledgePelletGenerator + real MemoryBridge calls bridge.stage(StagedFact)."""
    bridge = FakeBridge()
    gen = KnowledgePelletGenerator(memory_bridge=bridge)
    session = ParliamentSession(
        topic="topic", owl_names=["a", "b"], session_id="sess-real"
    )
    synthesis = SynthesisResult(
        consensus="we agree X",
        disagreements=[DisagreementPoint(claim="dispute", positions={"a": "1"})],
        recommendation="ship",
        confidence=0.8,
        synthesis_text="full",
    )
    await gen.from_parliament(session, synthesis)
    assert len(bridge.staged) == 2
    staged = bridge.staged[0]
    assert staged.source_type == "parliament"
    assert staged.source_ref == "parliament:sess-real"
    assert staged.confidence == 0.7
    assert staged.reinforcement_count == 0
    contents = {s.content for s in bridge.staged}
    assert {"we agree X", "dispute"} <= contents


async def test_pellet_generator_with_none_bridge_uses_null_bridge() -> None:
    """T15 — KnowledgePelletGenerator(memory_bridge=None) does not raise."""
    gen = KnowledgePelletGenerator(memory_bridge=None)
    session = ParliamentSession(
        topic="topic", owl_names=["a"], session_id="sess-null"
    )
    synthesis = SynthesisResult(
        consensus="x",
        disagreements=[],
        recommendation="r",
        confidence=0.5,
        synthesis_text="t",
    )
    # No exception expected — NullMemoryBridge swallows the stage call
    await gen.from_parliament(session, synthesis)
    assert isinstance(gen._bridge, NullMemoryBridge)


async def test_parliament_orchestrator_passes_bridge_to_pellet_generator(
    db: object,
) -> None:
    """T16 — ParliamentOrchestrator(memory_bridge=...) wires KnowledgePelletGenerator."""
    bridge = FakeBridge()

    class _StubBackend:
        async def run(self, state: object) -> object:  # pragma: no cover
            raise NotImplementedError

    orch = ParliamentOrchestrator(
        backend=_StubBackend(),  # type: ignore[arg-type]
        session_store=SessionStore(db=db),  # type: ignore[arg-type]
        memory_bridge=bridge,
    )
    # The orchestrator must have constructed a pellet generator wired to bridge
    assert orch._pellet_gen is not None
    # Confirm dispatch uses the bridge by calling from_parliament directly
    session = ParliamentSession(
        topic="topic", owl_names=["a"], session_id="sess-orch"
    )
    synthesis = SynthesisResult(
        consensus="C",
        disagreements=[],
        recommendation="r",
        confidence=0.7,
        synthesis_text="t",
    )
    await orch._pellet_gen.from_parliament(session, synthesis)
    assert len(bridge.staged) == 1


# ---------------------------------------------------------------------------
# Conftest marker (T20)
# ---------------------------------------------------------------------------


def test_integration_marker_registered_in_pyproject() -> None:
    """T20 — pyproject.toml registers the 'integration' marker for Story 6.7 E2E."""
    pp = Path(__file__).parent.parent / "pyproject.toml"
    text = pp.read_text(encoding="utf-8")
    assert "integration" in text, (
        "Add 'integration' marker to [tool.pytest.ini_options].markers"
    )
