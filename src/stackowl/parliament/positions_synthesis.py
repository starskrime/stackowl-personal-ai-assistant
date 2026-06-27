"""Positions-in / verdict-out synthesis (B2 split from ParliamentSynthesizer).

The ``mixture_of_agents`` tool (E8-S2) collects independent proposer answers and
needs them collapsed into one structured verdict — the same job the Parliament
:class:`~stackowl.parliament.synthesizer.ParliamentSynthesizer` does, but starting
from raw positions rather than a :class:`ParliamentSession`. This module holds the
pure prompt-builder + the async ``synthesize_positions`` routine so the synthesizer
class stays under the B2 line cap while still owning the single entry point
(``ParliamentSynthesizer.synthesize_positions`` delegates here). It reuses the
synthesizer's ``_SYSTEM_PROMPT`` and :class:`SynthesisParser`, never fabricating a
fake session, and degrades gracefully on a synth-provider failure (never raises).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.parliament.synthesis_models import SynthesisResult
from stackowl.providers.base import Message

if TYPE_CHECKING:
    from stackowl.parliament.synthesis_parser import SynthesisParser
    from stackowl.providers.base import ModelProvider
    from stackowl.providers.registry import ProviderRegistry

_DIAMOND = "◆"
_BASE_CONFIDENCE = 0.7

# F-57 — corrective nudge sent on the single re-prompt when the first synthesis
# completion was unparseable. Restates the structural-marker contract tersely so a
# one-off bad generation can be recovered before the result is accepted.
_STRICT_RETRY_INSTRUCTION = (
    "Your previous reply could not be parsed. Reply AGAIN using ONLY the exact "
    "uppercase structural markers, each on its own line: CONSENSUS:, "
    "RECOMMENDATION:, and (optionally) DISAGREEMENT:. The very first line must "
    "begin with 'CONSENSUS:'. Terminate the whole response with ◆ on its own "
    "line. Output nothing outside these markers."
)


async def complete_synthesis_with_retry(
    *,
    provider: ModelProvider,
    parser: SynthesisParser,
    messages: list[Message],
    correlation_id: str,
) -> tuple[str, SynthesisResult]:
    """Complete a synthesis and parse it; re-prompt ONCE if the parse degrades.

    F-57 — the synthesis ``provider.complete`` call was single-shot: a malformed or
    empty completion was handed straight to :class:`SynthesisParser`, whose fallback
    (S2 ``parse_ok=False``) then dressed raw text as a verdict, so a transient bad
    generation was never recovered. This re-prompts the SAME provider once, stricter
    (``_STRICT_RETRY_INSTRUCTION``), before accepting a degraded parse. The retry is
    bounded to exactly one extra call. A persistently-unparseable synthesis is
    returned with ``parse_ok=False`` so the existing S2 gates (orchestrator marks the
    session degraded; pellet generator skips staging) still fire — this only adds the
    bounded retry, it never weakens those gates. Provider exceptions propagate to the
    caller (surfaced, never masked).
    """
    completion = await provider.complete(messages, model="")
    raw_text = completion.content
    parsed = parser.parse(raw_text, correlation_id)
    if parsed.parse_ok:
        return raw_text, parsed

    log.parliament.debug(
        "[parliament] synthesis: unparseable completion — re-prompting once (stricter)",
        extra={"_fields": {"correlation_id": correlation_id, "raw_len": len(raw_text)}},
    )
    retry_messages = [*messages, Message(role="user", content=_STRICT_RETRY_INSTRUCTION)]
    retry_completion = await provider.complete(retry_messages, model="")
    retry_text = retry_completion.content
    retry_parsed = parser.parse(retry_text, correlation_id)
    if retry_parsed.parse_ok:
        log.parliament.debug(
            "[parliament] synthesis: re-prompt recovered a parseable verdict",
            extra={"_fields": {"correlation_id": correlation_id}},
        )
        return retry_text, retry_parsed

    log.parliament.warning(
        "[parliament] synthesis: still unparseable after one re-prompt — DEGRADED",
        extra={"_fields": {"correlation_id": correlation_id, "raw_len": len(retry_text)}},
    )
    return retry_text, retry_parsed


def build_positions_prompt(
    system_prompt: str,
    question: str,
    positions: list[str],
    labels: list[str],
) -> list[Message]:
    """Format raw positions into a system + user message pair (MoA).

    Mirrors the Parliament single-round transcript shape so the shared synthesis
    system prompt + parser apply, without any session object.
    """
    transcript_lines: list[str] = [
        f"Topic: {question}",
        f"Participants: {', '.join(labels)}",
        "",
        "--- Round 1 ---",
    ]
    for label, position in zip(labels, positions, strict=True):
        transcript_lines.append(f"[{label}]: {position}")
    user_text = "\n".join(transcript_lines)
    return [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_text),
    ]


async def synthesize_positions(
    *,
    providers: ProviderRegistry,
    parser: SynthesisParser,
    system_prompt: str,
    question: str,
    positions: list[str],
) -> SynthesisResult:
    """Synthesize a verdict from raw independent positions (no session).

    Reuses ``system_prompt`` + ``parser`` (the same the Parliament synthesizer
    uses). Single-round (no convergence/similarity). A synth-provider failure
    degrades to placeholder text → parser fallback; this routine never raises.
    """
    log.parliament.debug(
        "[parliament] synthesize_positions: entry",
        extra={"_fields": {"question_len": len(question), "positions": len(positions)}},
    )
    t0 = time.monotonic()
    labels = [f"agent_{i + 1}" for i in range(len(positions))]
    messages = build_positions_prompt(system_prompt, question, positions, labels)
    # F125 — prefer the most-capable AVAILABLE substitute (not config-order first)
    # and SURFACE the degrade so the user is never shown a fake "powerful" consensus
    # silently synthesized by a weak model.
    provider, degraded_from = providers.resolve_capable_or_degrade("powerful")
    log.parliament.debug(
        "[parliament] synthesize_positions: provider selected",
        extra={"_fields": {
            "provider_name": provider.name, "tier": "powerful",
            "tier_degraded": degraded_from is not None,
        }},
    )
    if degraded_from is not None:
        log.parliament.warning(
            "[parliament] synthesize_positions: no 'powerful' provider — synthesizing "
            "on a less-capable substitute (DEGRADED)",
            extra={"_fields": {"provider_name": provider.name, "degraded_from": degraded_from}},
        )

    try:
        # F-57 — re-prompt once (stricter) if the first completion is unparseable,
        # before accepting a degraded parse; provider failures still propagate.
        raw_text, parsed = await complete_synthesis_with_retry(
            provider=provider,
            parser=parser,
            messages=messages,
            correlation_id="moa",
        )
    except Exception as exc:
        # No-hidden-errors: a synthesis-provider failure must NOT be masked as a
        # clean verdict (a placeholder dressed as a synthesized answer). Surface it
        # so the caller (MoA) classifies the result as synthesis_failed and the user
        # is told the aggregator was unavailable — never shown a fake consensus.
        log.parliament.error(
            "[parliament] synthesize_positions: provider call failed — surfacing",
            exc_info=exc,
            extra={"_fields": {"provider_name": provider.name}},
        )
        raise

    rollcall = " · ".join(labels)
    body = raw_text.split(_DIAMOND)[0].rstrip()
    degrade_notice = (
        "_(Note: no powerful synthesis model was available — this was aggregated "
        "by a less-capable substitute.)_\n\n"
        if degraded_from is not None
        else ""
    )
    synthesis_text = f"Mixture-of-Agents: {rollcall}\n\n{degrade_notice}{body}\n{_DIAMOND}"
    result = SynthesisResult(
        consensus=parsed.consensus,
        disagreements=parsed.disagreements,
        recommendation=parsed.recommendation,
        confidence=_BASE_CONFIDENCE,
        synthesis_text=synthesis_text,
        mean_similarity=0.0,
        # F-58 — carry the parser's trust flag through so a fallback aggregation
        # (raw text dressed as a verdict) is never staged as durable knowledge.
        parse_ok=parsed.parse_ok,
    )
    log.parliament.info(
        "[parliament] synthesize_positions: exit",
        extra={
            "_fields": {
                "positions": len(positions),
                "disagreements": len(result.disagreements),
                "duration_ms": (time.monotonic() - t0) * 1000.0,
            }
        },
    )
    return result
