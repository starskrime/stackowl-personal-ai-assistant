"""MoA layer-1 fan-out + layer-2 synthesis (B2 split from the tool).

Holds the self-healing ensemble logic so ``mixture_of_agents.py`` stays under the
B2 line cap: fan a question across a roster of providers (per-proposer timeout +
``return_exceptions=True``), filter failures BEFORE synthesis (each ERROR-logged,
none hidden), then collapse survivors via
:meth:`ParliamentSynthesizer.synthesize_positions`. Returns a plain structured
record dict (status + provenance + degraded flag + failed list); never raises.

E8-S0cost — per-proposer cost is recorded by the PROVIDER itself inside
``provider.complete`` (the single recording site), so this module records nothing;
recording here too would DOUBLE-COUNT each proposer call.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from stackowl.infra.observability import log
from stackowl.parliament.synthesizer import ParliamentSynthesizer
from stackowl.providers.base import Message

if TYPE_CHECKING:
    from stackowl.providers.base import ModelProvider
    from stackowl.providers.registry import ProviderRegistry

_PER_PROPOSER_TIMEOUT_S = 30.0


class ProposerOutcome(BaseModel):
    """One proposer's layer-1 outcome (a position, or a structured failure)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_name: str
    position: str | None = None
    error: str | None = None


async def run_ensemble(
    *,
    registry: ProviderRegistry,
    roster: list[ModelProvider],
    question: str,
) -> dict[str, object]:
    """Fan out over ``roster``, filter failures, synthesize survivors.

    Returns a structured record dict with ``status`` one of ``ok`` /
    ``all_proposers_failed`` / ``synthesis_failed``. Never raises. Per-proposer cost
    is recorded by the provider itself (E8-S0cost single recording site).
    """
    log.tool.debug(
        "mixture_of_agents.run_ensemble: fanning out",
        extra={"_fields": {"roster": len(roster)}},
    )
    outcomes = await asyncio.gather(
        *[_propose(p, question) for p in roster],
        return_exceptions=True,
    )

    positions: list[str] = []
    failed: list[str] = []
    for provider, outcome in zip(roster, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            # _propose is contracted not to raise — belt-and-braces (B5).
            log.tool.error(
                "mixture_of_agents.run_ensemble: proposer raised unexpectedly",
                exc_info=outcome,
                extra={"_fields": {"provider": provider.name}},
            )
            failed.append(provider.name)
            continue
        if outcome.position is not None:
            positions.append(outcome.position)
        else:
            failed.append(outcome.provider_name)

    if not positions:
        log.tool.error(
            "mixture_of_agents.run_ensemble: all proposers failed",
            extra={"_fields": {"attempted": len(roster), "failed": failed}},
        )
        return {
            "status": "all_proposers_failed",
            "attempted": len(roster),
            "failed": failed,
            "detail": "every consulted model failed; answer the question directly or retry later.",
        }

    return await _synthesize(registry, question, positions, failed, len(roster))


async def _synthesize(
    registry: ProviderRegistry,
    question: str,
    positions: list[str],
    failed: list[str],
    attempted: int,
) -> dict[str, object]:
    """Layer-2 synthesis over surviving positions + provenance record."""
    synthesizer = ParliamentSynthesizer(registry)
    try:
        result = await synthesizer.synthesize_positions(question, positions)
    except Exception as exc:  # B5 — surface a synth-provider failure (no fake verdict).
        log.tool.error(
            "mixture_of_agents._synthesize: synthesis raised — structured error",
            exc_info=exc,
            extra={"_fields": {"positions": len(positions), "failed": failed}},
        )
        return {
            "status": "synthesis_failed",
            "consulted": len(positions),
            "failed": failed,
            "detail": str(exc),
        }

    degraded = len(failed) > 0 or len(positions) < 2
    provenance = f"consulted {len(positions)} model(s)" + (
        f"; {len(failed)} failed ({', '.join(failed)})" if failed else ""
    )
    return {
        "status": "ok",
        "answer": result.synthesis_text,
        "consensus": result.consensus,
        "recommendation": result.recommendation,
        "disagreements": [d.model_dump() for d in result.disagreements],
        "ensemble_size": len(positions),
        "consulted": len(positions),
        "attempted": attempted,
        "degraded_ensemble": degraded,
        "failed": failed,
        "provenance": provenance,
    }


async def _propose(
    provider: ModelProvider,
    question: str,
) -> ProposerOutcome:
    """Ask one provider for its position under a per-proposer timeout.

    Self-healing: a timeout or any provider error becomes a structured failure
    outcome (ERROR-logged), never an exception bubbling into synthesis. The call's
    cost is recorded by the provider itself (E8-S0cost single recording site).
    """
    messages = [Message(role="user", content=question)]
    try:
        completion = await asyncio.wait_for(
            provider.complete(messages, model=""),
            timeout=_PER_PROPOSER_TIMEOUT_S,
        )
    except TimeoutError:
        log.tool.error(
            "mixture_of_agents._propose: proposer timed out — filtered",
            extra={"_fields": {"provider": provider.name, "timeout_s": _PER_PROPOSER_TIMEOUT_S}},
        )
        return ProposerOutcome(provider_name=provider.name, error="timeout")
    except Exception as exc:
        log.tool.error(
            "mixture_of_agents._propose: proposer failed — filtered",
            exc_info=exc,
            extra={"_fields": {"provider": provider.name}},
        )
        return ProposerOutcome(provider_name=provider.name, error=str(exc))

    text = completion.content.strip()
    if not text:
        log.tool.warning(
            "mixture_of_agents._propose: empty position — filtered",
            extra={"_fields": {"provider": provider.name}},
        )
        return ProposerOutcome(provider_name=provider.name, error="empty")
    return ProposerOutcome(provider_name=provider.name, position=text)
