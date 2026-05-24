"""SynthesisParser — parses structural markers from LLM synthesis output.

The LLM is instructed to respond with the uppercase markers ``CONSENSUS:``,
``RECOMMENDATION:``, and ``DISAGREEMENT:`` terminated by ``◆``. This module
extracts those fields and gracefully degrades when the output is malformed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.parliament.synthesis_models import (
    DisagreementPoint,
    SynthesisResult,
)

if TYPE_CHECKING:
    from stackowl.parliament.models import ParliamentSession


_DIAMOND = "◆"


class SynthesisParser:
    """Parses an LLM synthesis response into a :class:`SynthesisResult`."""

    def parse(self, raw: str, session: ParliamentSession) -> SynthesisResult:
        """Parse structural markers; on failure return a graceful fallback."""
        log.parliament.debug(
            "[parliament] synthesis_parser.parse: entry",
            extra={"_fields": {"session_id": session.session_id, "raw_len": len(raw)}},
        )
        try:
            body = raw.split(_DIAMOND)[0].strip()
            consensus = ""
            recommendation = ""
            disagreements: list[DisagreementPoint] = []
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("CONSENSUS:"):
                    consensus = stripped[len("CONSENSUS:"):].strip()
                elif stripped.startswith("RECOMMENDATION:"):
                    recommendation = stripped[len("RECOMMENDATION:"):].strip()
                elif stripped.startswith("DISAGREEMENT:"):
                    point = self._parse_disagreement_line(
                        stripped[len("DISAGREEMENT:"):].strip()
                    )
                    if point is not None:
                        disagreements.append(point)
            if not consensus and not recommendation:
                raise ValueError("no CONSENSUS: or RECOMMENDATION: markers found")
            log.parliament.debug(
                "[parliament] synthesis_parser.parse: exit — parsed",
                extra={
                    "_fields": {
                        "session_id": session.session_id,
                        "disagreements": len(disagreements),
                    }
                },
            )
            return SynthesisResult(
                consensus=consensus or body[:200],
                disagreements=disagreements,
                recommendation=recommendation or "See synthesis above",
                confidence=0.0,  # placeholder — overwritten by caller
                synthesis_text=body,
                mean_similarity=0.0,
            )
        except Exception as exc:
            log.parliament.warning(
                "[parliament] synthesis_parser.parse: parse failure — "
                "falling back to raw text",
                exc_info=exc,
                extra={
                    "_fields": {
                        "session_id": session.session_id,
                        "raw_len": len(raw),
                    }
                },
            )
            fallback_body = raw.strip()
            return SynthesisResult(
                consensus=fallback_body[:200],
                disagreements=[],
                recommendation="See synthesis above",
                confidence=0.0,
                synthesis_text=fallback_body,
                mean_similarity=0.0,
            )

    def _parse_disagreement_line(self, line: str) -> DisagreementPoint | None:
        """Parse ``<claim> | owl: pos | owl: pos`` into a DisagreementPoint."""
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if not parts:
            return None
        claim = parts[0]
        positions: dict[str, str] = {}
        for part in parts[1:]:
            if ":" not in part:
                continue
            name, _, position = part.partition(":")
            name = name.strip()
            position = position.strip()
            if name and position:
                positions[name] = position
        if not positions:
            return None
        return DisagreementPoint(claim=claim, positions=positions)
