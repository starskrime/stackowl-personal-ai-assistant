"""RoundRunner — single-round execution: fan-out across owls and aggregation."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

from stackowl.exceptions import OwlConcurrencyError, OwlTimeoutError, OwlTokenLimitError
from stackowl.infra.observability import log
from stackowl.owls.concurrency import GovernorSaturatedError
from stackowl.owls.delegation_limits import GOVERNOR_ACQUIRE_TIMEOUT_SECONDS
from stackowl.parliament.models import ParliamentRound, ParliamentSession
from stackowl.parliament.token_estimate import estimate_tokens
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover — typing-only import
    from stackowl.owls.concurrency import ConcurrencyGovernor

_TRUNCATION_CHARS = 500


class RoundRunner:
    """Executes a single Parliament round across all owls in parallel.

    Encapsulates the per-owl backend invocation, the asyncio.gather fan-out,
    timeout handling, and token-budget enforcement so the orchestrator stays
    focused on session lifecycle.
    """

    def __init__(
        self,
        backend: OrchestratorBackend,
        per_owl_timeout_s: float,
        token_budget: int,
        delegation_governor: ConcurrencyGovernor | None = None,
        acquire_timeout_s: float = GOVERNOR_ACQUIRE_TIMEOUT_SECONDS,
    ) -> None:
        self._backend = backend
        self._per_owl_timeout_s = per_owl_timeout_s
        self._token_budget = token_budget
        # E8-S0 — shared in-flight budget; the same instance A2ADelegator holds.
        self._governor = delegation_governor
        # PARL-5 (F087) — bound the governor-slot ACQUIRE separately from the
        # per-owl RUN budget so a saturated host (never got a slot) is reported
        # distinctly from a slow run (got a slot, ran too long).
        self._acquire_timeout_s = acquire_timeout_s

    async def run_round(
        self,
        session: ParliamentSession,
        round_number: int,
        prompts: dict[str, str],
    ) -> ParliamentRound:
        """Run one round and return the aggregated ParliamentRound."""
        log.parliament.debug(
            "[parliament] round_runner.run_round: entry",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "round_number": round_number,
                    "owl_count": len(prompts),
                }
            },
        )
        t0 = time.monotonic()
        coros = [
            self._run_owl(session, owl_name, prompts[owl_name], round_number)
            for owl_name in session.owl_names
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        responses: dict[str, str] = {}
        truncated: dict[str, bool] = {}
        # PARL-1 (F078) — token-aware budget over prior GENUINE responses (the
        # estimator skips sentinels). Error markers added below contribute 0 real
        # tokens, so a failed owl can never silently inflate or shrink the budget.
        cumulative_tokens = session.cumulative_token_estimate()
        for owl_name, result in zip(session.owl_names, results, strict=True):
            if isinstance(result, BaseException):
                log.parliament.warning(
                    "[parliament] round_runner.run_round: owl raised",
                    exc_info=result,
                    extra={"_fields": {"owl_name": owl_name}},
                )
                responses[owl_name] = f"[error: {type(result).__name__}]"
                truncated[owl_name] = True
                continue
            _name, text, was_truncated = result
            cumulative_tokens += estimate_tokens(text)
            if cumulative_tokens >= self._token_budget:
                log.parliament.warning(
                    "[parliament] round_runner: token budget exceeded — truncating",
                    extra={
                        "_fields": {
                            "owl_name": owl_name,
                            "budget": self._token_budget,
                            "estimated_tokens": cumulative_tokens,
                        }
                    },
                )
                text = text[:_TRUNCATION_CHARS]
                was_truncated = True
            responses[owl_name] = text
            truncated[owl_name] = was_truncated

        duration_ms = (time.monotonic() - t0) * 1000.0
        round_ = ParliamentRound(
            round_number=round_number,
            responses=responses,
            truncated=truncated,
            duration_ms=duration_ms,
        )
        log.parliament.debug(
            "[parliament] round_runner.run_round: exit",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "round_number": round_number,
                    "duration_ms": duration_ms,
                }
            },
        )
        return round_

    async def _run_under_governor(self, state: PipelineState) -> PipelineState:
        """Run one owl's pipeline under the shared concurrency budget.

        Acquires a governor slot (released in ``finally`` via the slot context
        manager) so a parliament fan-out cannot exceed the host-wide in-flight
        cap shared with delegation. Ungated when no governor is wired.

        PARL-5 (F087): the slot is acquired with a BOUNDED acquire timeout; if
        the host is saturated the acquire raises :class:`GovernorSaturatedError`
        (never the per-owl run timeout), so the caller reports 'queued out'. The
        RUN itself is bounded separately by the caller's ``per_owl_timeout_s``.
        """
        if self._governor is None:
            return await self._run_backend_bounded(state)
        async with self._governor.slot(timeout=self._acquire_timeout_s):
            return await self._run_backend_bounded(state)

    async def _run_backend_bounded(self, state: PipelineState) -> PipelineState:
        """Run the backend bounded by the per-owl RUN budget only."""
        return await asyncio.wait_for(
            self._backend.run(state),
            timeout=self._per_owl_timeout_s,
        )

    async def _run_owl(
        self,
        session: ParliamentSession,
        owl_name: str,
        prompt: str,
        round_number: int,
    ) -> tuple[str, str, bool]:
        """Run a single owl via the backend; return (name, text, truncated)."""
        log.parliament.debug(
            "[parliament] round_runner._run_owl: entry",
            extra={
                "_fields": {
                    "session_id": session.session_id,
                    "owl_name": owl_name,
                    "round_number": round_number,
                    "prompt_len": len(prompt),
                }
            },
        )
        state = PipelineState(
            trace_id=str(uuid.uuid4()),
            session_id=session.session_id,
            input_text=prompt,
            channel="parliament",
            owl_name=owl_name,
            pipeline_step="parliament_round",
            # Internal owl-to-owl debate round — no user is answering mid-round,
            # so a clarify call must default-deny rather than park the debate.
            interactive=False,
        )
        t0 = time.monotonic()
        try:
            # The per-owl RUN budget is enforced INSIDE _run_under_governor (around
            # backend.run only); the bounded slot ACQUIRE is enforced by the
            # governor and raises GovernorSaturatedError, handled distinctly below.
            final_state = await self._run_under_governor(state)
        except GovernorSaturatedError as exc:
            # PARL-5 (F087) — the host was saturated and this owl NEVER got a
            # slot. Report 'queued out' (distinct from '[timed out]', which means
            # it ran but was too slow) so the operator can tell the two apart.
            log.parliament.warning(
                "[parliament] round_runner._run_owl: queued out — host saturated",
                exc_info=exc,
                extra={
                    "_fields": {
                        "owl_name": owl_name,
                        "acquire_timeout_s": self._acquire_timeout_s,
                    }
                },
            )
            return (
                owl_name,
                f"[queued out — host saturated, no slot within "
                f"{self._acquire_timeout_s:.0f}s]",
                True,
            )
        except TimeoutError:
            log.parliament.warning(
                "[parliament] round_runner._run_owl: timeout",
                extra={
                    "_fields": {
                        "owl_name": owl_name,
                        "timeout_s": self._per_owl_timeout_s,
                    }
                },
            )
            return (
                owl_name,
                f"[timed out after {self._per_owl_timeout_s:.0f}s]",
                True,
            )
        except (OwlTimeoutError, OwlTokenLimitError, OwlConcurrencyError) as exc:
            log.parliament.warning(
                "[parliament] round_runner._run_owl: owl error",
                exc_info=exc,
                extra={"_fields": {"owl_name": owl_name}},
            )
            return owl_name, f"[error: {type(exc).__name__}]", True
        except Exception as exc:
            log.parliament.warning(
                "[parliament] round_runner._run_owl: backend failed",
                exc_info=exc,
                extra={"_fields": {"owl_name": owl_name}},
            )
            return owl_name, f"[backend error: {type(exc).__name__}]", True

        text = "".join(chunk.content for chunk in final_state.responses)
        log.parliament.debug(
            "[parliament] round_runner._run_owl: exit",
            extra={
                "_fields": {
                    "owl_name": owl_name,
                    "response_len": len(text),
                    "duration_ms": (time.monotonic() - t0) * 1000.0,
                }
            },
        )
        return owl_name, text, False
