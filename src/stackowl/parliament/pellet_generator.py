"""KnowledgePelletGenerator — stages Parliament synthesis facts to memory.

After a Parliament session completes, the consensus statement and each
unresolved disagreement point are staged through a memory bridge so the
platform's memory subsystem (Epic 6) can persist them as long-lived
knowledge pellets.

The only supported bridge contract is the Epic-6
:class:`stackowl.memory.bridge.MemoryBridge`'s ``stage(fact: StagedFact)``
shape (the legacy Epic-5 ``stage(content, source_type, source_ref)`` stub
has been removed — nothing outside this module's own tests depended on it).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log
from stackowl.memory.models import StagedFact

if TYPE_CHECKING:
    from stackowl.parliament.models import ParliamentSession
    from stackowl.parliament.synthesis_models import SynthesisResult

# F-59 — durable-knowledge floor. A synthesis below this confidence is too weak
# to persist as a long-lived fact (e.g. a mostly-truncated debate). This is a
# SECONDARY guard; the PRIMARY gate is ``SynthesisResult.parse_ok`` — a parse
# failure must never be staged regardless of its (caller-assigned) confidence.
_MIN_PELLET_CONFIDENCE = 0.5


class NullMemoryBridge:
    """No-op bridge that logs each staged fact.

    Default when no bridge is wired (e.g. ``ParliamentOrchestrator`` built
    without a ``memory_bridge``). Duck-types the Epic-6
    ``stage(fact: StagedFact)`` contract — no ABC needed since
    :class:`KnowledgePelletGenerator` no longer branches on bridge type.
    """

    async def stage(self, fact: StagedFact) -> None:
        log.engine.info(
            "[parliament] pellet_generator: null bridge — fact not persisted "
            "(no memory_bridge wired)",
            extra={
                "_fields": {
                    "source_type": fact.source_type,
                    "source_ref": fact.source_ref,
                    "content_len": len(fact.content),
                }
            },
        )


class KnowledgePelletGenerator:
    """Converts a synthesised Parliament session into staged knowledge facts."""

    def __init__(self, memory_bridge: Any | None = None) -> None:
        # Any object exposing `stage(fact: StagedFact) -> None` (typically the
        # Epic-6 `stackowl.memory.bridge.MemoryBridge`). Defaults to the local
        # NullMemoryBridge stub when nothing is provided.
        self._bridge: Any = memory_bridge or NullMemoryBridge()

    async def from_parliament(
        self,
        session: ParliamentSession,
        synthesis: SynthesisResult,
    ) -> None:
        """Stage the consensus + each disagreement claim through the bridge.

        Bridge failures are logged at WARNING and do not halt processing of
        the remaining claims — Parliament must never fail because a memory
        write failed.
        """
        log.engine.debug(
            "[parliament] pellet_generator.from_parliament: entry",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "disagreements": len(synthesis.disagreements),
                    "has_consensus": bool(synthesis.consensus),
                    "parse_ok": getattr(synthesis, "parse_ok", True),
                    "confidence": getattr(synthesis, "confidence", None),
                }
            },
        )
        # F-59 — NEVER stage a fallback/low-confidence synthesis as durable
        # knowledge. ``parse_ok=False`` means the consensus is raw text the parser
        # could not structure (a truncated body dressed as a verdict); staging it
        # would pollute long-term memory with a confidence=0.7 trust="self" fact.
        # A confidence below the floor is too weak to persist even when parsed.
        parse_ok = getattr(synthesis, "parse_ok", True)
        confidence = getattr(synthesis, "confidence", 1.0)
        if not parse_ok or confidence < _MIN_PELLET_CONFIDENCE:
            log.engine.warning(
                "[parliament] pellet_generator.from_parliament: skipping — "
                "synthesis not trustworthy enough to persist as durable knowledge",
                extra={
                    "_fields": {
                        "session_id": session.session_id,
                        "parse_ok": parse_ok,
                        "confidence": confidence,
                        "min_confidence": _MIN_PELLET_CONFIDENCE,
                    }
                },
            )
            return
        claims: list[str] = []
        if synthesis.consensus:
            claims.append(synthesis.consensus)
        claims.extend(d.claim for d in synthesis.disagreements if d.claim)
        log.engine.debug(
            "[parliament] pellet_generator.from_parliament: claims collected",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "claim_count": len(claims),
                }
            },
        )

        staged = 0
        for claim in claims:
            try:
                fact = StagedFact(
                    fact_id=str(uuid.uuid4()),
                    content=claim,
                    source_type="parliament",
                    source_ref=f"parliament:{session.session_id}",
                    confidence=0.7,
                    reinforcement_count=0,
                    trust="self",
                )
                await self._bridge.stage(fact)
                staged += 1
            except Exception as exc:
                log.engine.warning(
                    "[parliament] pellet_generator.from_parliament: "
                    "bridge.stage failed — continuing with remaining claims",
                    exc_info=exc,
                    extra={
                        "_fields": {
                            "session_id": session.session_id,
                            "claim_len": len(claim),
                        }
                    },
                )

        log.engine.info(
            "[parliament] pellet_generator.from_parliament: exit",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "claims_total": len(claims),
                    "claims_staged": staged,
                }
            },
        )
