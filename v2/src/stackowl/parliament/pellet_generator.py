"""KnowledgePelletGenerator — stages Parliament synthesis facts to memory.

After a Parliament session completes, the consensus statement and each
unresolved disagreement point are staged through a memory bridge so the
platform's memory subsystem (Epic 6) can persist them as long-lived
knowledge pellets.

Two bridge shapes are supported:

* The Epic-5 minimal :class:`MemoryBridge` ABC defined in this module,
  whose ``stage(content, source_type, source_ref)`` signature predates
  the structured :class:`~stackowl.memory.models.StagedFact`.
* The Epic-6 :class:`stackowl.memory.bridge.MemoryBridge`, whose
  ``stage(fact: StagedFact)`` signature is now the canonical contract.

Detection is by ``isinstance`` against the Epic-6 ABC — Epic-5 tests
that subclass the local stub continue to work unchanged.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log
from stackowl.memory.bridge import MemoryBridge as RealMemoryBridge
from stackowl.memory.models import StagedFact

if TYPE_CHECKING:
    from stackowl.parliament.models import ParliamentSession
    from stackowl.parliament.synthesis_models import SynthesisResult


class MemoryBridge(ABC):
    """Minimal staging interface for facts produced outside the memory subsystem.

    Concrete implementation lands in Epic 6 (``SqliteMemoryBridge``).
    """

    @abstractmethod
    async def stage(
        self,
        fact_content: str,
        source_type: str,
        source_ref: str,
    ) -> None:
        """Stage a single fact for later consolidation by the memory subsystem."""
        ...


class NullMemoryBridge(MemoryBridge):
    """No-op bridge that logs each staged fact.

    Used until Epic 6 wires the real ``SqliteMemoryBridge``. Keeps the
    orchestrator path exercised end-to-end without requiring memory infra.
    """

    async def stage(
        self,
        fact_content: str,
        source_type: str,
        source_ref: str,
    ) -> None:
        log.engine.info(
            "[parliament] pellet_generator: null bridge — fact not persisted "
            "(Epic 6 pending)",
            extra={
                "_fields": {
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "content_len": len(fact_content),
                }
            },
        )


class KnowledgePelletGenerator:
    """Converts a synthesised Parliament session into staged knowledge facts."""

    def __init__(self, memory_bridge: Any | None = None) -> None:
        # Accepts either the Epic-5 minimal :class:`MemoryBridge` stub or the
        # Epic-6 :class:`stackowl.memory.bridge.MemoryBridge`. Defaults to the
        # local NullMemoryBridge stub when nothing is provided.
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
                }
            },
        )
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
        uses_real_bridge = isinstance(self._bridge, RealMemoryBridge)
        log.engine.debug(
            "[parliament] pellet_generator.from_parliament: bridge dispatch",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "uses_real_bridge": uses_real_bridge,
                }
            },
        )
        for claim in claims:
            try:
                if uses_real_bridge:
                    fact = StagedFact(
                        fact_id=str(uuid.uuid4()),
                        content=claim,
                        source_type="parliament",
                        source_ref=f"parliament:{session.session_id}",
                        confidence=0.7,
                        reinforcement_count=0,
                    )
                    await self._bridge.stage(fact)
                else:
                    await self._bridge.stage(
                        claim,
                        "parliament",
                        session.session_id,
                    )
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
