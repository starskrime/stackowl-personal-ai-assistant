"""ParliamentOrchestrator — multi-owl debate coordinator with parallel fan-out."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

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
    from stackowl.owls.concurrency import ConcurrencyGovernor


class ParliamentOrchestrator:
    """Runs multi-owl Parliament sessions with parallel fan-out via asyncio.gather.

    Depends only on the OrchestratorBackend interface (ARCH-113). Enforces
    per-owl/session timeouts, a token budget, convergence early-termination,
    and mid-session interjection via a PER-INSTANCE active-session dict keyed by
    session_id (F075 — a single process-wide slot let concurrent sessions clobber
    each other).
    """

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
        delegation_governor: ConcurrencyGovernor | None = None,
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
        # E8-S0 — per-owl fan-out shares the SAME governor as A2ADelegator.
        self._round_runner = RoundRunner(
            backend=backend,
            per_owl_timeout_s=per_owl_timeout_s,
            token_budget=token_budget,
            delegation_governor=delegation_governor,
        )
        # F075 — active sessions keyed by session_id (was a single process-wide
        # ClassVar slot that concurrent run() calls clobbered). The lock makes the
        # read-mutate-write of an entry atomic. Eager init avoids a lazy
        # check-then-set TOCTOU; asyncio.Lock() binds to the running loop on first
        # await, which is fine here (constructed before any session runs).
        self._active_sessions: dict[str, ParliamentSession] = {}
        self._active_session_lock = asyncio.Lock()

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
        lock = self._active_session_lock
        async with lock:
            self._active_sessions[session.session_id] = session
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
                # Fail THIS session (its latest active snapshot), never a neighbor's.
                current = self._active_sessions.get(session.session_id, session)
                final = current.fail()
            await self._store.update_final(final)
        finally:
            async with lock:
                # Pop ONLY this session's entry — a blanket reset would null a
                # concurrent session's slot (the "A's finally nulls B" bug).
                self._active_sessions.pop(session.session_id, None)

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

    async def inject_interjection(
        self, message: str, session_id: str | None = None
    ) -> bool:
        """Push ``message`` into a live session. Returns True if accepted.

        Routing (F075, single-or-refuse — never silently pick one):
        * ``session_id`` given → route to that session; unknown → refuse + warn.
        * ``session_id`` None + exactly ONE live session → route to it (the natural
          "the active debate", the ``/parliament push`` call site's intent).
        * ``session_id`` None + MULTIPLE live → refuse LOUDLY (return False + warn);
          a silent pick would misroute a push into the wrong debate.
        """
        log.parliament.debug(
            "[parliament] orchestrator.inject_interjection: entry",
            extra={"_fields": {"msg_len": len(message), "session_id": session_id}},
        )
        lock = self._active_session_lock
        async with lock:
            target_id = self._resolve_interjection_target_locked(session_id)
            if target_id is None:
                return False
            current = self._active_sessions[target_id]
            updated = current.add_interjection(message)
            self._active_sessions[target_id] = updated
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

    def _resolve_interjection_target_locked(self, session_id: str | None) -> str | None:
        """Resolve which live session an interjection targets (caller holds the lock).

        Returns the target session_id, or None to REFUSE (logged). Never silently
        picks among multiple live sessions (no-fake-success).
        """
        if session_id is not None:
            if session_id not in self._active_sessions:
                log.parliament.warning(
                    "[parliament] orchestrator.inject_interjection: unknown session — refusing",
                    extra={"_fields": {"session_id": session_id}},
                )
                return None
            return session_id
        live = list(self._active_sessions.keys())
        if not live:
            log.parliament.debug(
                "[parliament] orchestrator.inject_interjection: no active session",
            )
            return None
        if len(live) > 1:
            log.parliament.warning(
                "[parliament] orchestrator.inject_interjection: ambiguous — "
                "multiple debates active, refusing unscoped push",
                extra={"_fields": {"active_count": len(live)}},
            )
            return None
        return live[0]

    async def _run_session(self, session: ParliamentSession) -> ParliamentSession:
        """Core debate loop — runs rounds until convergence or max_rounds."""
        log.parliament.debug(
            "[parliament] orchestrator._run_session: entry",
            extra={"_fields": {"session_id": session.session_id}},
        )
        lock = self._active_session_lock
        sid = session.session_id
        converged = False
        for round_number in range(1, self._max_rounds + 1):
            async with lock:
                # Read THIS session's latest snapshot (picks up any interjection
                # added concurrently); fall back to the in-scope param on a
                # completion-race key miss so it never NPEs.
                current = self._active_sessions.get(sid, session)
            prompts = self._build_round_prompts(current, round_number)
            round_ = await self._round_runner.run_round(current, round_number, prompts)
            async with lock:
                # Re-read under the lock and append the round to the LIVE snapshot,
                # not the pre-round local: an interjection added DURING the round
                # (concurrent inject_interjection mutated the dict entry) would
                # otherwise be clobbered by a stale write-back (a lost-update race).
                live = self._active_sessions.get(sid, current)
                current = live.add_round(round_)
                self._active_sessions[sid] = current
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
            self._active_sessions[sid] = final
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
            # PARL-3 (F081) — a synthesis failure is a DEGRADED terminal, not a
            # clean 'completed'. Mark completed_no_synthesis so the channel tells
            # the user the debate ran but no conclusion formed (no fake success).
            log.parliament.error(
                "[parliament] orchestrator._finalize_session: synthesis failed — "
                "marking completed_no_synthesis (degraded)",
                exc_info=exc,
                extra={"_fields": {"session_id": session.session_id}},
            )
            return session.complete_no_synthesis()

        # F-58 — a parse-failed synthesis is a fallback (raw text dressed as a
        # verdict), NOT a real conclusion. Treat it like a synthesis failure:
        # mark the session degraded (completed_no_synthesis) and SKIP pellet
        # staging so fabricated claims never enter durable memory. The fallback
        # text still rides the returned SynthesisResult for display upstream.
        if not getattr(synthesis_result, "parse_ok", True):
            log.parliament.warning(
                "[parliament] orchestrator._finalize_session: synthesis parse "
                "failed — marking completed_no_synthesis (degraded), skipping "
                "pellet staging of fabricated claims",
                extra={"_fields": {"session_id": session.session_id}},
            )
            return session.complete_no_synthesis()

        final = session.complete(synthesis=synthesis_result.synthesis_text)
        if self._pellet_gen is not None:
            try:
                await self._pellet_gen.from_parliament(final, synthesis_result)
            except Exception as exc:
                # PARL-4 (F082) — flag the failure on the session so health /
                # observability can surface a RUN of pellet-staging failures.
                # The synthesis is already stored; only the pellet side-channel
                # failed, so the session still reports 'completed'.
                final = final.model_copy(update={"pellet_staged": False})
                log.parliament.warning(
                    "[parliament] orchestrator._finalize_session: "
                    "pellet generation failed — synthesis already stored, "
                    "session flagged pellet_staged=False",
                    exc_info=exc,
                    extra={"_fields": {
                        "session_id": final.session_id,
                        "pellet_staged": False,
                    }},
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
