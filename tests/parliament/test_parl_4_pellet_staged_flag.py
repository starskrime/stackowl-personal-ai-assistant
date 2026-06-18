"""PARL-4 (F082) — repeated pellet-staging failures are observable.

A pellet-generation failure must NOT be a silent warning: the session carries
``pellet_staged=False`` so health/observability can surface repeated failures.
The synthesis is still stored (the verdict survives; only the pellet side-channel
failed), and the session still reports a clean ``completed`` status.
"""

from __future__ import annotations

import pytest

from stackowl.parliament.models import ParliamentRound, ParliamentSession
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.session_store import SessionStore


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


class _Synth:
    async def synthesize(self, session: ParliamentSession) -> object:
        class _R:
            synthesis_text = "verdict text ◆"

        return _R()


class _OkPellet:
    async def from_parliament(self, session: object, result: object) -> None:
        return None


class _FailingPellet:
    async def from_parliament(self, session: object, result: object) -> None:
        raise RuntimeError("pellet store unavailable")


def _session() -> ParliamentSession:
    return ParliamentSession(
        topic="t",
        owl_names=["a", "b"],
        rounds=[
            ParliamentRound(
                round_number=1,
                responses={"a": "x", "b": "y"},
                truncated={"a": False, "b": False},
            )
        ],
    )


def test_default_pellet_staged_is_true() -> None:
    assert ParliamentSession(topic="t", owl_names=["a"]).pellet_staged is True


@pytest.mark.asyncio
async def test_pellet_failure_flags_session_but_keeps_synthesis() -> None:
    store = _MemStore()
    orch = ParliamentOrchestrator(
        backend=object(),  # type: ignore[arg-type]
        session_store=store,
        synthesizer=_Synth(),  # type: ignore[arg-type]
        pellet_generator=_FailingPellet(),  # type: ignore[arg-type]
    )
    final = await orch._finalize_session(_session())  # noqa: SLF001
    assert final.status == "completed"
    assert final.synthesis == "verdict text ◆"  # synthesis still stored
    assert final.pellet_staged is False  # observable failure flag


@pytest.mark.asyncio
async def test_pellet_success_leaves_flag_true() -> None:
    store = _MemStore()
    orch = ParliamentOrchestrator(
        backend=object(),  # type: ignore[arg-type]
        session_store=store,
        synthesizer=_Synth(),  # type: ignore[arg-type]
        pellet_generator=_OkPellet(),  # type: ignore[arg-type]
    )
    final = await orch._finalize_session(_session())  # noqa: SLF001
    assert final.status == "completed"
    assert final.pellet_staged is True
