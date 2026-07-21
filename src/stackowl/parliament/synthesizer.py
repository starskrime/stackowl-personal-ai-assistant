"""ParliamentSynthesizer — collapses a multi-round debate into a verdict.

Produces a :class:`SynthesisResult` containing:

* consensus / recommendation / disagreements parsed from the LLM output,
* an epistemic confidence score (base = mean pairwise cosine similarity
  with a truncation penalty applied), and
* a formatted ``synthesis_text`` ready for display, with a roll-call
  header, optional low-confidence warning, and a ``◆`` terminator.

The synthesizer depends only on :class:`ProviderRegistry` (for the powerful
provider tier) and an optional :class:`ConvergenceDetector` (for mean
similarity). All live I/O is guarded by :class:`TestModeGuard`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.parliament.convergence import ConvergenceDetector
from stackowl.parliament.positions_synthesis import (
    complete_synthesis_with_retry,
    synthesize_positions,
)
from stackowl.parliament.synthesis_models import SynthesisResult
from stackowl.parliament.synthesis_parser import SynthesisParser
from stackowl.providers.base import Message

if TYPE_CHECKING:
    from stackowl.parliament.models import ParliamentSession
    from stackowl.providers.registry import ProviderRegistry


_DIAMOND = "◆"
_LOW_CONFIDENCE_THRESHOLD = 0.6
_NO_EMBEDDER_BASE_CONFIDENCE = 0.7
_TRUNCATION_PENALTY = 0.2
_TRUNCATION_RATIO_TRIGGER = 0.5

_SYSTEM_PROMPT = (
    "You are a synthesis engine for a multi-agent debate (\"Parliament\"). "
    "Your job is to collapse the transcript into a structured verdict. "
    "Respond using exactly these structural markers, all uppercase, one per "
    "line: CONSENSUS:, RECOMMENDATION:, DISAGREEMENT:. "
    "After CONSENSUS: write one or two sentences describing what the "
    "participants agree on. After RECOMMENDATION: write the recommended "
    "next action. For each unresolved disagreement, add one line in this "
    "exact shape: DISAGREEMENT: <claim> | <owl_name>: <position> | "
    "<owl_name>: <position>. If there are no unresolved disagreements, "
    "omit the DISAGREEMENT: lines entirely. Always terminate the entire "
    "response with the single character ◆ on its own line. "
    "Reply in the same language the participants used."
)


class ParliamentSynthesizer:
    """Synthesises a completed :class:`ParliamentSession` into a verdict."""

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        convergence_detector: ConvergenceDetector | None = None,
    ) -> None:
        self._providers = provider_registry
        self._convergence = convergence_detector or ConvergenceDetector()
        self._parser = SynthesisParser()

    async def synthesize(self, session: ParliamentSession) -> SynthesisResult:
        """Run synthesis on ``session`` and return a :class:`SynthesisResult`.

        Calls :meth:`TestModeGuard.assert_not_test_mode` first so this method
        never performs live I/O in unit-test runs unless the guard is
        explicitly disabled by the test fixture.
        """
        TestModeGuard.assert_not_test_mode("parliament.synthesize")
        log.parliament.debug(
            "[parliament] synthesizer.synthesize: entry",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "rounds": len(session.rounds),
                    "owl_count": len(session.owl_names),
                }
            },
        )
        t0 = time.monotonic()
        mean_sim = await self._compute_mean_similarity(session)
        log.parliament.debug(
            "[parliament] synthesizer.synthesize: similarity computed",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "mean_similarity": mean_sim,
                }
            },
        )

        messages = self._build_synthesis_prompt(session)
        # F125 — most-capable available substitute (not config-order first), and
        # SURFACE the degrade so a weak-model synthesis is never shown as a clean
        # powerful verdict.
        provider, model, degraded_from = self._providers.resolve_capable_or_degrade_and_model(
            "powerful"
        )
        log.parliament.debug(
            "[parliament] synthesizer.synthesize: provider selected",
            extra={
                "_fields": {
                    "provider_name": provider.name,
                    "tier": "powerful",
                    "model": model,
                    "tier_degraded": degraded_from is not None,
                    "session_id": session.session_id,
                }
            },
        )
        if degraded_from is not None:
            log.parliament.warning(
                "[parliament] synthesizer.synthesize: no 'powerful' provider — "
                "synthesizing on a less-capable substitute (DEGRADED)",
                extra={"_fields": {
                    "provider_name": provider.name, "degraded_from": degraded_from,
                    "session_id": session.session_id,
                }},
            )

        try:
            # F-57 — re-prompt the synthesis provider ONCE (stricter) if the first
            # completion is unparseable, before accepting a degraded parse; a one-off
            # bad generation is recovered while a persistent failure stays
            # parse_ok=False (the S2 degrade + pellet-skip gates still fire). Provider
            # failures still propagate to the handler below.
            raw_text, parsed = await complete_synthesis_with_retry(
                provider=provider,
                parser=self._parser,
                messages=messages,
                correlation_id=session.session_id,
                model=model,
            )
        except Exception as exc:
            # No-hidden-errors: a synthesis-provider failure must NOT be masked as a
            # clean confidence-scored verdict (a placeholder dressed up as a real
            # synthesis). Surface it so the orchestrator marks the session finished
            # WITHOUT a synthesis and SKIPS pellet generation — the user is told the
            # parliament could not synthesize, never shown a fabricated consensus.
            log.parliament.error(
                "[parliament] synthesizer.synthesize: provider call failed — surfacing",
                exc_info=exc,
                extra={
                    "_fields": {
                        "session_id": session.session_id,
                        "provider_name": provider.name,
                    }
                },
            )
            raise

        confidence = self._compute_confidence(session, mean_sim)
        synthesis_text = self._format_synthesis_text(raw_text, session, confidence)
        if degraded_from is not None:
            synthesis_text = (
                "_(Note: no powerful synthesis model was available — this was "
                "synthesized by a less-capable substitute.)_\n\n" + synthesis_text
            )

        result = SynthesisResult(
            consensus=parsed.consensus,
            disagreements=parsed.disagreements,
            recommendation=parsed.recommendation,
            confidence=confidence,
            synthesis_text=synthesis_text,
            mean_similarity=mean_sim,
            # F-58 — carry the parser's trust flag through so a fallback (raw text
            # dressed as a verdict) is never staged as durable knowledge.
            parse_ok=parsed.parse_ok,
        )
        log.parliament.info(
            "[parliament] synthesizer.synthesize: exit",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "confidence": confidence,
                    "mean_similarity": mean_sim,
                    "disagreements": len(result.disagreements),
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )
        return result

    async def synthesize_positions(
        self,
        question: str,
        positions: list[str],
    ) -> SynthesisResult:
        """Synthesize a verdict from raw independent positions (no session).

        Positions-in / verdict-out entry point used by the ``mixture_of_agents``
        tool (E8-S2). Reuses the same synthesis ``_SYSTEM_PROMPT`` + structural
        parser as :meth:`synthesize` (via :mod:`positions_synthesis`) but takes
        already-collected proposer answers rather than a
        :class:`ParliamentSession` — so MoA never fabricates a fake session.
        Single-round; degrades gracefully on a synth-provider failure; never
        raises out of the synthesis itself.
        """
        TestModeGuard.assert_not_test_mode("parliament.synthesize_positions")
        return await synthesize_positions(
            providers=self._providers,
            parser=self._parser,
            system_prompt=_SYSTEM_PROMPT,
            question=question,
            positions=positions,
        )

    async def _compute_mean_similarity(self, session: ParliamentSession) -> float:
        """Return mean pairwise similarity for the last round's responses."""
        if not session.rounds:
            return 0.0
        last_round = session.rounds[-1]
        # PARL-1 (F078) — never embed error/timeout sentinels as if they were
        # owl positions; mean similarity is over genuine responses only.
        responses = list(last_round.genuine_responses().values())
        if len(responses) < 2:
            return 0.0
        try:
            return await self._convergence.mean_similarity(responses)
        except Exception as exc:
            log.parliament.warning(
                "[parliament] synthesizer._compute_mean_similarity: failed",
                exc_info=exc,
                extra={"_fields": {"session_id": session.session_id}},
            )
            return 0.0

    def _build_synthesis_prompt(self, session: ParliamentSession) -> list[Message]:
        """Format the transcript into a system + user message pair."""
        transcript_lines: list[str] = [
            f"Topic: {session.topic}",
            f"Participants: {', '.join(session.owl_names)}",
            "",
        ]
        for rnd in session.rounds:
            transcript_lines.append(f"--- Round {rnd.round_number} ---")
            # PARL-1 (F078) — exclude error/timeout sentinels from the synthesis
            # transcript so '[error: …]' / '[timed out …]' markers are never
            # presented to the synthesis model as a participant's actual position.
            for owl_name, response in rnd.genuine_responses().items():
                transcript_lines.append(f"[{owl_name}]: {response}")
            transcript_lines.append("")
        if session.interjections:
            transcript_lines.append("--- User Interjections ---")
            for interjection in session.interjections:
                transcript_lines.append(f"[user]: {interjection}")
        user_text = "\n".join(transcript_lines)
        return [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_text),
        ]

    def _compute_confidence(
        self,
        session: ParliamentSession,
        mean_similarity: float,
    ) -> float:
        """Combine similarity + truncation penalty into a [0, 1] score."""
        base = _NO_EMBEDDER_BASE_CONFIDENCE if mean_similarity == 0.0 else mean_similarity

        truncated_count = sum(
            1
            for rnd in session.rounds
            for v in rnd.truncated.values()
            if v
        )
        total_responses = sum(len(rnd.responses) for rnd in session.rounds)
        if total_responses > 0:
            ratio = truncated_count / total_responses
            if ratio > _TRUNCATION_RATIO_TRIGGER:
                log.parliament.warning(
                    "[parliament] synthesizer: confidence penalty — "
                    "more than half of owl responses were truncated",
                    extra={
                        "_fields": {
                            "session_id": session.session_id,
                            "truncated_count": truncated_count,
                            "total_responses": total_responses,
                            "ratio": ratio,
                        }
                    },
                )
                base -= _TRUNCATION_PENALTY

        if base < 0.0:
            return 0.0
        if base > 1.0:
            return 1.0
        return base

    def _format_synthesis_text(
        self,
        raw_synthesis: str,
        session: ParliamentSession,
        confidence: float,
    ) -> str:
        """Wrap the raw synthesis with header, warning, and ``◆`` terminator."""
        confidence_pct = int(confidence * 100)
        rollcall = " · ".join(session.owl_names)
        prefix = (
            f"[confidence: {confidence_pct}%]\n"
            f"Parliament: {rollcall}\n\n"
        )
        warning = ""
        if confidence < _LOW_CONFIDENCE_THRESHOLD:
            warning = (
                "⚠ Low confidence synthesis — consider verifying "
                "key claims independently\n"
            )
        body = raw_synthesis.split(_DIAMOND)[0].rstrip()
        return f"{prefix}{warning}{body}\n{_DIAMOND}"
