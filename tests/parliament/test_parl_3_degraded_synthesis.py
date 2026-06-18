"""PARL-3 (F081) — a failed synthesis surfaces as degraded, not a clean 'completed'.

When the synthesizer raises, the session must terminate in the distinct
``completed_no_synthesis`` state (NOT ``completed``), so the channel can honestly
tell the user the debate ran but produced no conclusion — never a silent
synthesis=None dressed up as a clean completion.
"""

from __future__ import annotations

import pytest

from stackowl.parliament.models import ParliamentRound, ParliamentSession
from stackowl.parliament.orchestrator import ParliamentOrchestrator
from stackowl.parliament.session_store import SessionStore


class _MemStore(SessionStore):
    def __init__(self) -> None:
        self.final: ParliamentSession | None = None

    async def create(self, session: ParliamentSession) -> None:  # noqa: D102
        pass

    async def update_rounds(self, session: ParliamentSession) -> None:  # noqa: D102
        pass

    async def update_final(self, session: ParliamentSession) -> None:  # noqa: D102
        self.final = session

    async def get(self, session_id: str) -> ParliamentSession | None:  # noqa: D102
        return self.final

    async def list_recent(self, limit: int = 10) -> list[ParliamentSession]:  # noqa: D102
        return [self.final] if self.final else []


class _RaisingSynthesizer:
    async def synthesize(self, session: ParliamentSession) -> object:
        raise RuntimeError("synthesis provider exploded")


class _NoopBackend:
    async def run(self, state: object) -> object:  # pragma: no cover - unused here
        return state


def test_session_has_degraded_transition() -> None:
    s = ParliamentSession(topic="t", owl_names=["a", "b"])
    degraded = s.complete_no_synthesis()
    assert degraded.status == "completed_no_synthesis"
    assert degraded.synthesis is None
    assert degraded.completed_at is not None


@pytest.mark.asyncio
async def test_synthesis_failure_marks_degraded_not_completed() -> None:
    store = _MemStore()
    orch = ParliamentOrchestrator(
        backend=_NoopBackend(),  # type: ignore[arg-type]
        session_store=store,
        synthesizer=_RaisingSynthesizer(),  # type: ignore[arg-type]
    )
    session = ParliamentSession(
        topic="should we ship?",
        owl_names=["scout", "sage"],
        rounds=[
            ParliamentRound(
                round_number=1,
                responses={"scout": "yes ship", "sage": "no wait"},
                truncated={"scout": False, "sage": False},
            )
        ],
    )
    final = await orch._finalize_session(session)  # noqa: SLF001 — exercising the path
    assert final.status == "completed_no_synthesis"
    assert final.synthesis is None
