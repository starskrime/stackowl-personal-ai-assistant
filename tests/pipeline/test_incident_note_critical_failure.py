"""ADR-6 Task 7 — guaranteed delivery, live-turn path.

When a CRITICAL step fails with no usable response, ``surface_critical_failure``
already guarantees a non-empty apology (Phase 2 #2). Task 7 enriches that SAME
apology with a one-line incident summary when a verified background-incident
RCA verdict exists for the SAME failure_class this turn's critical step just
hit — read off ``services.incident_verdict_lookup`` (the EXISTING StepServices
parameter the surfacer already takes), never a new gate/cascade member.
"""

from __future__ import annotations

import pytest

from stackowl.learning.failure_outcome_miner import RcaVerdict
from stackowl.pipeline.delivery_gate import surface_critical_failure
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState, StepError
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ProviderRegistry

pytestmark = pytest.mark.asyncio


class _ApologyProvider(ModelProvider):
    """Healthy apology-cascade provider (mirrors test_phase2_critical_failure.py)."""

    def __init__(self, reply: str = "Sorry, that could not be completed.") -> None:
        self._name = "apology"
        self._reply = reply

    @property
    def name(self) -> str:
        return self._name

    @property
    def protocol(self) -> str:  # type: ignore[override]
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        return CompletionResult(
            content=self._reply, input_tokens=5, output_tokens=5,
            model="apology-model", provider_name=self._name, duration_ms=1.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # type: ignore[override]
        yield self._reply

    async def complete_with_tools(self, *a, **k) -> tuple[str, list]:  # pragma: no cover
        return self._reply, []


def _state() -> PipelineState:
    return PipelineState(
        trace_id="trace-incident-note",
        session_id="sess-incident-note",
        input_text="do the thing",
        channel="cli",
        owl_name="secretary",
        pipeline_step="execute",
        step_errors=(StepError(step="execute", exc_type="ToolTimeoutError", message="boom"),),
    )


def _services(lookup=None) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("apology", _ApologyProvider(), tier="fast")
    return StepServices(provider_registry=preg, incident_verdict_lookup=lookup)


def _verdict(*, verified: bool = True) -> RcaVerdict:
    return RcaVerdict(
        capability_class="web_fetch", failure_class="ToolTimeoutError",
        skill_name="fix_web_fetch_timeout", description="d", when_to_use="w",
        root_cause="connection pool exhaustion under load", fix_pattern="fx",
        verified=verified,
    )


async def test_no_lookup_wired_is_byte_identical() -> None:
    result = await surface_critical_failure(_state(), _services(lookup=None))
    text = "".join(c.content for c in result.responses)
    assert "known, already-investigated" not in text
    assert "Sorry, that could not be completed." in text


async def test_matching_verified_verdict_enriches_the_apology() -> None:
    verdict = _verdict(verified=True)
    lookup = lambda fc: verdict if fc == "ToolTimeoutError" else None  # noqa: E731
    result = await surface_critical_failure(_state(), _services(lookup=lookup))
    text = "".join(c.content for c in result.responses)
    assert "Sorry, that could not be completed." in text  # existing cascade untouched
    assert "known, already-investigated" in text
    assert "connection pool exhaustion under load" in text


async def test_non_matching_failure_class_no_enrichment() -> None:
    verdict = _verdict(verified=True)
    lookup = lambda fc: verdict if fc == "SomeOtherError" else None  # noqa: E731
    result = await surface_critical_failure(_state(), _services(lookup=lookup))
    text = "".join(c.content for c in result.responses)
    assert "known, already-investigated" not in text


async def test_unverified_verdict_no_enrichment() -> None:
    verdict = _verdict(verified=False)
    lookup = lambda fc: verdict  # noqa: E731
    result = await surface_critical_failure(_state(), _services(lookup=lookup))
    text = "".join(c.content for c in result.responses)
    assert "known, already-investigated" not in text


async def test_lookup_raising_degrades_to_no_note_not_a_crash() -> None:
    def _boom(fc: str) -> RcaVerdict | None:
        raise RuntimeError("lookup exploded")

    result = await surface_critical_failure(_state(), _services(lookup=_boom))
    text = "".join(c.content for c in result.responses)
    assert "Sorry, that could not be completed." in text
    assert "known, already-investigated" not in text
