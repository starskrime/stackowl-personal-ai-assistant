"""ParliamentOrchestrator — multi-owl debate coordinator with parallel fan-out."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, ClassVar

from stackowl.config.test_mode import TestModeGuard
from stackowl.events.bus import EventBus
from stackowl.infra.observability import log
from stackowl.parliament.convergence import ConvergenceDetector
from stackowl.parliament.cross_examination import CrossExaminationPromptBuilder
from stackowl.parliament.models import ParliamentSession
from stackowl.parliament.pellet_generator import KnowledgePelletGenerator
from stackowl.parliament.round_runner import RoundRunner
from stackowl.parliament.session_store import SessionStore
from stackowl.parliament.synthesizer import ParliamentSynthesizer
from stackowl.pipeline.backends.base import OrchestratorBackend

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from stackowl.memory.bridge import MemoryBridge


class ParliamentOrchestrator:
    """Runs multi-owl Parliament sessions with parallel fan-out via asyncio.gather.

    Depends only on the OrchestratorBackend interface (ARCH-113). Enforces
    per-owl/session timeouts, a token budget, convergence early-termination,
    and mid-session interjection via a class-level active-session ref.
    """

    _active_session: ClassVar[ParliamentSession | None] = None
    _active_session_lock: ClassVar[asyncio.Lock | None] = None

    def __init__(
        self,
        backend: OrchestratorBackend,
        session_store: SessionStore,
        max_rounds: int = 3,
        event_bus: EventBus | None = None,
        convergence_detector: ConvergenceDetector | None = None,
        cross_examination_builder: CrossExaminationPromptBuilder | None = None,
        session_timeout_s: float = 90.0,
        per_owl_timeout_s: float = 30.0,
        token_budget: int = 20_000,
        synthesizer: ParliamentSynthesizer | None = None,
        pellet_generator: KnowledgePelletGenerator | None = None,
        memory_bridge: MemoryBridge | None = None,
    ) -> None:
        self._backend = backend
        self._store = session_store
        self._max_rounds = max_rounds
        self._event_bus = event_bus
        self._convergence = convergence_detector or ConvergenceDetector()
        self._cross_examination = cross_examination_builder or CrossExaminationPromptBuilder()
        self._session_timeout_s = session_timeout_s
        self._synthesizer = synthesizer
        # Auto-wire pellet generator with the provided memory bridge when the
        # caller didn't supply one — canonical path post-Story 6.7.
        if pellet_generator is None and memory_bridge is not None:
            pellet_generator = KnowledgePelletGenerator(memory_bridge=memory_bridge)
        self._pellet_gen = pellet_generator
        self._round_runner = RoundRunner(
            backend=backend,
            per_owl_timeout_s=per_owl_timeout_s,
            token_budget=token_budget,
        )

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._active_session_lock is None:
            cls._active_session_lock = asyncio.Lock()
        return cls._active_session_lock

    async def run(
        self,
        topic: str,
        owl_names: list[str],
        session_id: str | None = None,
    ) -> ParliamentSession:
        """Run a full Parliament session under the session timeout."""
        TestModeGuard.assert_not_test_mode("parliament.run")
        log.parliament.debug(
            "[parliament] orchestrator.run: entry",
            extra={
                "_fields": {
                    "topic_len": len(topic),
                    "owl_count": len(owl_names),
                    "max_rounds": self._max_rounds,
                }
            },
        )
        t0 = time.monotonic()
        session = ParliamentSession(
            session_id=session_id or str(uuid.uuid4()),
            topic=topic,
            owl_names=owl_names,
        )
        await self._store.create(session)
        lock = self._get_lock()
        async with lock:
            ParliamentOrchestrator._active_session = session
        try:
            final = await asyncio.wait_for(
                self._run_session(session),
                timeout=self._session_timeout_s,
            )
        except TimeoutError:
            elapsed = time.monotonic() - t0
            log.parliament.warning(
                "[parliament] orchestrator.run: session timeout",
                extra={
                    "_fields": {
                        "session_id": session.session_id,
                        "elapsed_s": elapsed,
                        "timeout_s": self._session_timeout_s,
                    }
                },
            )
            async with lock:
                current = ParliamentOrchestrator._active_session or session
                final = current.fail()
            await self._store.update_final(final)
        finally:
            async with lock:
                ParliamentOrchestrator._active_session = None

        if self._event_bus is not None:
            try:
                self._event_bus.emit("parliament.completed", final.session_id)
            except Exception as exc:
                log.parliament.warning(
                    "[parliament] orchestrator.run: event emit failed",
                    exc_info=exc,
                    extra={"_fields": {"session_id": final.session_id}},
                )
        log.parliament.info(
            "[parliament] orchestrator.run: exit",
            extra={
                "_fields": {
                    "session_id": final.session_id,
                    "status": final.status,
                    "rounds": len(final.rounds),
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )
        return final

    async def inject_interjection(self, message: str) -> bool:
        """Push ``message`` into the active session. Returns True if accepted."""
        log.parliament.debug(
            "[parliament] orchestrator.inject_interjection: entry",
            extra={"_fields": {"msg_len": len(message)}},
        )
        lock = self._get_lock()
        async with lock:
            current = ParliamentOrchestrator._active_session
            if current is None:
                log.parliament.debug(
                    "[parliament] orchestrator.inject_interjection: no active session",
                )
                return False
            updated = current.add_interjection(message)
            ParliamentOrchestrator._active_session = updated
        log.parliament.info(
            "[parliament] orchestrator.inject_interjection: queued",
            extra={
                "_fields": {
                    "session_id": updated.session_id,
                    "total_interjections": len(updated.interjections),
                }
            },
        )
        return True

    async def _run_session(self, session: ParliamentSession) -> ParliamentSession:
        """Core debate loop — runs rounds until convergence or max_rounds."""
        log.parliament.debug(
            "[parliament] orchestrator._run_session: entry",
            extra={"_fields": {"session_id": session.session_id}},
        )
        lock = self._get_lock()
        converged = False
        for round_number in range(1, self._max_rounds + 1):
            async with lock:
                current = ParliamentOrchestrator._active_session or session
            prompts = self._build_round_prompts(current, round_number)
            round_ = await self._round_runner.run_round(current, round_number, prompts)
            current = current.add_round(round_)
            async with lock:
                ParliamentOrchestrator._active_session = current
            await self._store.update_rounds(current)
            session = current

            if await self._convergence.check(round_):
                log.parliament.info(
                    "[parliament] orchestrator: convergence detected — terminating",
                    extra={
                        "_fields": {
                            "session_id": session.session_id,
                            "round_number": round_number,
                        }
                    },
                )
                converged = True
                break
        if not converged:
            log.parliament.info(
                "[parliament] orchestrator: max_rounds reached without convergence — "
                "proceeding to synthesis",
                extra={
                    "_fields": {
                        "session_id": session.session_id,
                        "max_rounds": self._max_rounds,
                    }
                },
            )
        final = await self._finalize_session(session)
        await self._store.update_final(final)
        async with lock:
            ParliamentOrchestrator._active_session = final
        log.parliament.debug(
            "[parliament] orchestrator._run_session: exit",
            extra={"_fields": {"session_id": final.session_id, "rounds": len(final.rounds)}},
        )
        return final

    async def _finalize_session(
        self,
        session: ParliamentSession,
    ) -> ParliamentSession:
        """Run synthesis + pellet staging (if wired) and mark session completed."""
        log.parliament.debug(
            "[parliament] orchestrator._finalize_session: entry",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "has_synthesizer": self._synthesizer is not None,
                    "has_pellet_gen": self._pellet_gen is not None,
                }
            },
        )
        if self._synthesizer is None:
            return session.complete()

        try:
            synthesis_result = await self._synthesizer.synthesize(session)
        except Exception as exc:
            log.parliament.warning(
                "[parliament] orchestrator._finalize_session: synthesis failed",
                exc_info=exc,
                extra={"_fields": {"session_id": session.session_id}},
            )
            return session.complete()

        final = session.complete(synthesis=synthesis_result.synthesis_text)
        if self._pellet_gen is not None:
            try:
                await self._pellet_gen.from_parliament(final, synthesis_result)
            except Exception as exc:
                log.parliament.warning(
                    "[parliament] orchestrator._finalize_session: "
                    "pellet generation failed — synthesis already stored",
                    exc_info=exc,
                    extra={"_fields": {"session_id": final.session_id}},
                )
        log.parliament.debug(
            "[parliament] orchestrator._finalize_session: exit",
            extra={
                "_fields": {
                    "session_id": final.session_id,
                    "synthesis_len": len(synthesis_result.synthesis_text),
                }
            },
        )
        return final

    def _build_round_prompts(
        self,
        session: ParliamentSession,
        round_number: int,
    ) -> dict[str, str]:
        if round_number == 1:
            return {owl_name: session.topic for owl_name in session.owl_names}
        return {
            owl_name: self._cross_examination.build(
                topic=session.topic,
                owl_name=owl_name,
                prior_rounds=session.rounds,
                interjections=session.interjections,
            )
            for owl_name in session.owl_names
        }
