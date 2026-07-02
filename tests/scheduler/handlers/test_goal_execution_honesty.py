"""TS10 — scheduled-job honesty + safety (ADR-T5, "the empty scheduled cycle").

The #1 risk: a scheduled owl pokes the user every 2h with "AI news". When a
cycle's ``web_search`` returns NOTHING, a weak model under pressure to deliver
FABRICATES — the original incident, now automated 12×/day with no human to catch
it. The honesty/grounding floor MUST hold INSIDE the scheduled job.

Wiring (verified by reading + asserted by ``test_both_backends_run_grounding_gate``
below): ``GoalExecutionHandler`` runs the goal through ``self._backend.run``; BOTH
real backends (``AsyncioBackend`` / ``LangGraphBackend``) run
``surface_overclaim_gate`` then ``surface_grounding_gate`` in their deliver-
surfacing band BEFORE the answer leaves the pipeline, and the handler delivers
``final_state.responses`` (the POST-gate answer). So the gate that floors a
fabricated interactive answer floors the scheduled poke identically.

These tests assert on the DELIVERED content (what ``deliver_for_job`` received),
never prose classification:

  (a) empty cycle — ``web_search`` returned no URLs, the model fabricated a poke
      with a fabricated URL → the delivered poke carries NO fabricated URL; it is
      the honest grounding floor.
  (b) real cycle — ``web_search`` returned a real URL the answer cites → the
      delivered poke carries that source URL (a grounded poke is NOT floored).
  (d) quiet-hours reuse — a recurring poke routes at "normal" urgency (router
      coalesces in quiet hours); a one-shot user goal stays "critical".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stackowl.pipeline.delivery_gate import (
    _FLOOR_TEXT,
    surface_grounding_gate,
    surface_overclaim_gate,
)
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from tests._story_7_2_helpers import RecordingDb, disable_guard, make_job
from tests.scheduler.handlers.test_goal_execution_delivery import FakeJobDeliverer

pytestmark = pytest.mark.asyncio

_FAB_URL = "https://openai.example/gpt56-launch"
_REAL_URL = "https://realsource.example/ai-weekly"


def _web_search(*urls: str) -> ToolCall:
    """A ``web_search`` tool record whose result envelope carries ``urls`` (none → empty cycle)."""
    payload = {
        "success": True,
        "data": {"web": [{"title": "t", "url": u, "description": "d", "position": i}
                         for i, u in enumerate(urls, 1)]},
    }
    return ToolCall(
        tool_name="web_search", args={"query": "AI news"},
        result=json.dumps(payload), error=None, duration_ms=1.0,
    )


class DeliverBandBackend:
    """Backend double that runs the REAL deliver-surfacing band.

    It reproduces, byte-for-byte, what ``AsyncioBackend.run`` /
    ``LangGraphBackend`` do before deliver: seed the draft + retrieval ledger for
    the cycle under test, then run the SAME ``surface_overclaim_gate`` →
    ``surface_grounding_gate`` functions the production backends import and call.
    The handler delivers whatever responses this returns — so this proves the
    floor reaches the scheduled poke.
    """

    def __init__(self, *, draft: str, search_urls: tuple[str, ...]) -> None:
        self._draft = draft
        self._search_urls = search_urls
        self.calls: list[PipelineState] = []

    async def run(self, state: PipelineState) -> PipelineState:
        self.calls.append(state)
        draft = ResponseChunk(
            content=self._draft, is_final=False, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )
        seeded = state.evolve(
            responses=(draft,),
            tool_calls=(_web_search(*self._search_urls),),
        )
        # The production deliver-surfacing band (asyncio_backend.py / langgraph).
        seeded = await surface_overclaim_gate(seeded)
        seeded = await surface_grounding_gate(seeded)
        return seeded

    async def shutdown(self) -> None:
        return None


def _delivered_message(deliverer: FakeJobDeliverer) -> str:
    assert len(deliverer.calls) == 1
    return str(deliverer.calls[0]["message"])


def _poke_job(**overrides: Any) -> Any:
    overrides.setdefault("params", {"goal": "Poke me with the latest AI news"})
    return make_job(
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
        **overrides,
    )


# (a) THE empty-cycle proof — search returned nothing, model fabricated a URL.
async def test_empty_cycle_floors_no_fabricated_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard(monkeypatch)
    backend = DeliverBandBackend(
        draft=(
            "Big AI news today: GPT-5.6 just launched, read the full "
            f"announcement here {_FAB_URL} — huge update for everyone."
        ),
        search_urls=(),  # the empty cycle: web_search came back with nothing
    )
    deliverer = FakeJobDeliverer(rollup="delivered")
    handler = GoalExecutionHandler(
        backend=backend, db=RecordingDb(), job_deliverer=deliverer,  # type: ignore[arg-type]
    )

    await handler.execute(_poke_job())

    delivered = _delivered_message(deliverer)
    # The fabricated URL NEVER reaches the user.
    assert _FAB_URL not in delivered
    assert "gpt56" not in delivered.lower()
    # It floored to the honest "I didn't actually retrieve this" — never invented.
    assert delivered == _FLOOR_TEXT


# (b) real cycle — a grounded poke carrying its fetched source is delivered intact.
async def test_real_cycle_delivers_sourced_poke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard(monkeypatch)
    backend = DeliverBandBackend(
        draft=(
            "Here's what's genuinely new in AI this week, with the source: "
            f"{_REAL_URL} — worth a read."
        ),
        search_urls=(_REAL_URL,),  # web_search actually returned this URL
    )
    deliverer = FakeJobDeliverer(rollup="delivered")
    handler = GoalExecutionHandler(
        backend=backend, db=RecordingDb(), job_deliverer=deliverer,  # type: ignore[arg-type]
    )

    await handler.execute(_poke_job())

    delivered = _delivered_message(deliverer)
    assert _REAL_URL in delivered  # the grounded source survives — poke is sent
    assert delivered != _FLOOR_TEXT


# (d) quiet-hours reuse — recurring poke → "normal" (router coalesces in quiet hours).
async def test_recurring_poke_routes_normal_for_quiet_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard(monkeypatch)
    backend = DeliverBandBackend(draft="all good, here's the news.", search_urls=())
    deliverer = FakeJobDeliverer(rollup="delivered")
    handler = GoalExecutionHandler(
        backend=backend, db=RecordingDb(), job_deliverer=deliverer,  # type: ignore[arg-type]
    )

    await handler.execute(_poke_job())  # recurring (no run_once)
    assert deliverer.calls[0]["urgency"] == "normal"


async def test_one_shot_goal_stays_critical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disable_guard(monkeypatch)
    backend = DeliverBandBackend(draft="one-off answer.", search_urls=())
    deliverer = FakeJobDeliverer(rollup="delivered")
    handler = GoalExecutionHandler(
        backend=backend, db=RecordingDb(), job_deliverer=deliverer,  # type: ignore[arg-type]
    )

    await handler.execute(_poke_job(params={"goal": "do it once", "run_once": True}))
    assert deliverer.calls[0]["urgency"] == "critical"


# Wiring tripwire — the proactive path is only honest while BOTH real backends keep
# the grounding gate in their deliver-surfacing band. If a refactor drops it from
# either backend, the scheduled poke could fabricate again — fail loudly here.
#
# FR-11/FR-12 — both backends now route their post-execute surfacing through the
# ONE shared pipeline.backends.shared.run_delivery_gate() seam instead of calling
# the gates inline, so the tripwire has two halves: (a) each backend still routes
# through that shared seam, (b) the seam itself still runs both gates. Either half
# failing means the same "a refactor silently dropped a gate" scenario this test
# exists to catch.
async def test_both_backends_run_grounding_gate() -> None:
    backends_dir = (
        Path(__file__).resolve().parents[3]
        / "src" / "stackowl" / "pipeline" / "backends"
    )
    for name in ("asyncio_backend.py", "langgraph_backend.py"):
        src = (backends_dir / name).read_text(encoding="utf-8")
        assert "run_delivery_gate(" in src, f"{name} no longer routes through the shared delivery gate"

    shared_src = (backends_dir / "shared.py").read_text(encoding="utf-8")
    assert "surface_grounding_gate(" in shared_src, "shared delivery gate no longer runs grounding gate"
    assert "surface_overclaim_gate(" in shared_src, "shared delivery gate no longer runs overclaim gate"
