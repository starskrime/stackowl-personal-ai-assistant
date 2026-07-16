"""ShadowValidator — pre-commit replay-and-score gate for proposed DNA deltas (FR-8/FR-9).

Standalone, side-effect-free core. Given a batch of proposed DNA deltas, this
replays a held-out sample of the owl's own recent, real, trustworthy interactions
against the PROPOSED dna — using a scratch (throwaway) :class:`OwlRegistry` and a
scratch :class:`StepServices`, never the live global registry/services — and scores
each replay with the SAME critic prompt builder + :func:`is_trustworthy_success`
oracle the rest of the platform already uses.

Wired into the promotion flow as of Story 2.6: ``EvolutionCoordinator``'s
``_checkpoint_validate_and_promote`` (owls/evolution.py) composes this class
between checkpoint and persist for the nightly batch. Story 3.2's
``evolve_now`` is the second production caller — it will call that same
method, not construct/call this class independently (AD-3).

ISOLATION (load-bearing): ``validate()`` never mutates the live ``OwlRegistry``
(no ``apply_dna_overlay``/``registry.replace()`` on it — that would be a live side
effect visible to concurrent real turns), never persists a replay's outcome to
``task_outcomes`` (the scratch ``StepServices`` carries ``db_pool=None``, which
every write site in the pipeline already treats as a no-op — see
``_capture_outcome``/``persist_turn``/``_record_rejection``), and never delivers a
message (``stream_registry=None`` + ``interactive=False`` + no ``reply_target`` ⇒
``deliver.run`` discards the response). The scratch registry/services are built
fresh inside ``validate()`` and discarded when it returns.

Design decision (not fully prescribed by the story — the constructor signature
below has no ``tool_registry`` parameter): a replay never wires a
``tool_registry``, so ``execute.run`` cannot enter the tool-loop branch and no
tool — consequential or otherwise — ever runs for real during a replay. This is
the only way to make "no side effects" an absolute, structural guarantee rather
than a policy a future caller could accidentally violate by handing this class a
live, unrestricted toolset. It also means a replayed turn's ``verified`` signal
is always derived from the fresh critic ``quality_score`` (see the oracle below),
never from a real ``ToolResult.verified`` — there is nothing else available.

Constants (AD-3 "single shared config, not per-caller" amendment): the class is
constructed with ``n_consecutive_required``/``sample_size`` as EXPLICIT params so
a test can override them, but every PRODUCTION caller (Story 2.6's nightly batch,
Story 3.2's ``evolve_now``) MUST construct :class:`ShadowValidator` with the
module-level defaults below (``_DEFAULT_N_CONSECUTIVE``, ``_DEFAULT_SAMPLE_SIZE``)
— never a looser, caller-specific override. A caller that needs a different
threshold for testing must say so explicitly at the call site; it must never
become the production default for just one caller.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.critic_prompt import CriticScorerPromptBuilder, parse_critic_response
from stackowl.memory.outcome_store import TaskOutcome, TaskOutcomeStore, classify_failure
from stackowl.memory.reflection_store import _HIGH_QUALITY_THRESHOLD
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_attribution import lookback_epoch
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import ModelProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.verification import is_trustworthy_success

# AD-3 shared config — see the class docstring. Both production callers
# (Story 2.6, Story 3.2) must construct ShadowValidator with these, unmodified.
_DEFAULT_N_CONSECUTIVE = 3
_DEFAULT_SAMPLE_SIZE = 5

# Critic tier — mirrors CriticScorerHandler's default `critic_tier="fast"`.
_CRITIC_TIER = "fast"


@dataclass(frozen=True)
class ShadowValidationResult:
    """Outcome of one ``ShadowValidator.validate()`` call.

    ``failures`` carries just enough to log/report per failed replay (Story 2.7's
    observability) — kept minimal on purpose; extend the dict shape later if 2.7
    needs more, don't over-design it now.
    """

    passed: bool
    consecutive_non_regressions: int
    n_replayed: int
    failures: tuple[dict[str, object], ...]


def _eligible_for_replay(outcomes: list[TaskOutcome]) -> list[TaskOutcome]:
    """Outcomes eligible for the held-out replay sample: trustworthy successes only.

    Reuses the SHAPE of ``dna_attribution._filter_scored_outcomes``
    (``o.success and not o.failure_class``) but is NOT that function — this is
    not the positive-only-LEARNING filter (which also requires a ``dna_snapshot``
    and excludes a disliked ``approach_rating`` for DNA *attribution* purposes).
    This filter answers a narrower question: "did this input previously produce a
    clean, trustworthy result, so replaying it is a meaningful regression check?"
    ``outcomes`` is assumed already ordered newest-first (as
    ``TaskOutcomeStore.list_scored_for_owl`` returns it) — order is preserved.
    """
    return [o for o in outcomes if o.success and not o.failure_class]


class ShadowValidator:
    """Replay a held-out sample of an owl's real interactions against proposed DNA.

    See the module docstring for the isolation guarantee and the AD-3 shared-config
    contract. ``n_consecutive_required``/``sample_size`` default to the module-level
    constants; production callers must not override them with a looser value.
    """

    def __init__(
        self,
        db: DbPool,
        provider_registry: ProviderRegistry,
        *,
        n_consecutive_required: int = _DEFAULT_N_CONSECUTIVE,
        sample_size: int = _DEFAULT_SAMPLE_SIZE,
    ) -> None:
        self._db = db
        self._providers = provider_registry
        self._n_consecutive_required = n_consecutive_required
        self._sample_size = sample_size
        self._prompt_builder = CriticScorerPromptBuilder()
        log.owls.debug(
            "[shadow] validator.init: ready",
            extra={"_fields": {
                "n_consecutive_required": n_consecutive_required,
                "sample_size": sample_size,
            }},
        )

    @property
    def n_consecutive_required(self) -> int:
        """Public read accessor (Story 2.7) — rejection-log enrichment and the
        manual dry-run's config-parity check both need to read this without
        reaching into a private attribute. Falls back to the module default
        for test doubles (e.g. ``AlwaysFailShadowValidator``) that skip
        ``__init__`` entirely."""
        return getattr(self, "_n_consecutive_required", _DEFAULT_N_CONSECUTIVE)

    @property
    def sample_size(self) -> int:
        """See :attr:`n_consecutive_required` — same rationale."""
        return getattr(self, "_sample_size", _DEFAULT_SAMPLE_SIZE)

    async def validate(
        self,
        owl_name: str,
        manifest: OwlAgentManifest,
        proposed_dna: OwlDNA,
    ) -> ShadowValidationResult:
        """Replay this owl's held-out sample against ``proposed_dna``; score each.

        Never mutates the live registry, never persists a replay's outcome, never
        delivers a message. See the module docstring for the isolation guarantee.
        """
        # 1. ENTRY
        log.owls.debug(
            "[shadow] validate: entry",
            extra={"_fields": {"owl": owl_name, "sample_size": self._sample_size}},
        )
        store = TaskOutcomeStore(self._db)
        outcomes = await store.list_scored_for_owl(owl_name, since_epoch=lookback_epoch())
        eligible = _eligible_for_replay(outcomes)

        # 2. DECISION — cold-start: too little trustworthy history to validate
        # against fails CLOSED (not a vacuous pass, not a crash).
        if len(eligible) < self._sample_size:
            log.owls.info(
                "[shadow] validate: exit — cold start, insufficient held-out sample",
                extra={"_fields": {
                    "owl": owl_name, "eligible": len(eligible),
                    "required": self._sample_size,
                }},
            )
            return ShadowValidationResult(
                passed=False, consecutive_non_regressions=0,
                n_replayed=len(eligible), failures=(),
            )

        held_out = eligible[: self._sample_size]
        log.owls.debug(
            "[shadow] validate: held-out sample selected",
            extra={"_fields": {"owl": owl_name, "n_held_out": len(held_out)}},
        )

        # Scratch registry + scratch services — the isolation seam. NEVER the
        # live get_services()/global OwlRegistry. Discarded when validate() returns.
        scratch_registry = OwlRegistry()
        scratch_registry.register(manifest.model_copy(update={"dna": proposed_dna}))
        scratch_services = StepServices(
            provider_registry=self._providers,
            owl_registry=scratch_registry,
        )
        critic_provider = self._providers.get_with_cascade(_CRITIC_TIER)

        consecutive = 0
        n_replayed = 0
        failures: list[dict[str, object]] = []
        passed = False
        for outcome in held_out:
            n_replayed += 1
            result_state = await self._replay_one(outcome, owl_name, scratch_services)
            quality = await self._score_replay(outcome, result_state, critic_provider)
            success = not result_state.errors
            # Quality-score proxy for `verified` (AC #2) — this story has no live
            # ToolResult.verified signal for a replay (no tool_registry is ever
            # wired; see the module docstring), so the fresh critic score is the
            # only reality-check signal available. A parse failure (quality is
            # None) is treated as NOT verified — fail closed, same posture as the
            # cold-start guard above.
            verified = quality is not None and quality >= _HIGH_QUALITY_THRESHOLD
            trustworthy = is_trustworthy_success(success, verified)
            # 3. STEP — per-replay pass/fail
            log.owls.debug(
                "[shadow] validate: replay scored",
                extra={"_fields": {
                    "owl": owl_name, "n": n_replayed, "success": success,
                    "quality": quality, "trustworthy": trustworthy,
                }},
            )
            if trustworthy:
                consecutive += 1
                if consecutive >= self._n_consecutive_required:
                    passed = True
                    break
            else:
                failures.append({
                    "input_text": outcome.input_text,
                    "reason": (
                        f"pipeline_errors={list(result_state.errors)}"
                        if result_state.errors
                        else f"quality_score={quality} < {_HIGH_QUALITY_THRESHOLD}"
                    ),
                })
                # "consecutive" means unbroken from the start of the walk — a
                # later trustworthy replay must never rescue a broken streak, so
                # stop here rather than keep counting.
                break

        result = ShadowValidationResult(
            passed=passed, consecutive_non_regressions=consecutive,
            n_replayed=n_replayed, failures=tuple(failures),
        )
        # 4. EXIT
        log.owls.info(
            "[shadow] validate: exit",
            extra={"_fields": {
                "owl": owl_name, "passed": passed,
                "consecutive_non_regressions": consecutive, "n_replayed": n_replayed,
            }},
        )
        return result

    async def _replay_one(
        self, outcome: TaskOutcome, owl_name: str, services: StepServices,
    ) -> PipelineState:
        """Run one held-out input through the REAL pipeline, side-effect-free.

        Mirrors tests/pipeline/test_plan_a_gateway_integration.py's isolated
        state-construction + AsyncioBackend.run pattern. A synthetic session_id +
        interactive=False so nothing attempts a live delivery/clarify side-channel.
        """
        replay_id = uuid4().hex
        state = PipelineState(
            trace_id=f"shadow-validate-{replay_id}",
            session_id=f"shadow-validate-{replay_id}",
            input_text=outcome.input_text,
            channel=outcome.channel,
            owl_name=owl_name,
            pipeline_step="start",
            interactive=False,
        )
        backend = AsyncioBackend(services=services)
        return await backend.run(state)

    async def _score_replay(
        self, outcome: TaskOutcome, result_state: PipelineState, provider: ModelProvider,
    ) -> float | None:
        """Score one replay with the SAME critic prompt builder CriticScorerHandler
        uses — WITHOUT its DB-coupled execute(job) wrapper (this must not touch
        task_outcomes). Returns None on any provider/parse failure."""
        response_text = "\n".join(c.content for c in result_state.responses if c.content)
        replay_outcome = TaskOutcome(
            outcome_id=0,
            trace_id=result_state.trace_id,
            session_id=result_state.session_id,
            owl_name=result_state.owl_name,
            channel=result_state.channel,
            success=not result_state.errors,
            latency_ms=sum(d for _, d in result_state.step_durations),
            tool_call_count=len(result_state.tool_calls),
            failure_class=classify_failure(result_state.errors),
            quality_score=None,
            step_durations=dict(result_state.step_durations),
            input_text=outcome.input_text,
            response_text=response_text,
            captured_at=time.time(),
            scored_at=None,
        )
        messages = self._prompt_builder.build(replay_outcome)
        try:
            result = await provider.complete(messages, model="")
        except Exception as exc:  # B5 — never let a critic-call failure crash validate()
            log.owls.warning(
                "[shadow] score_replay: critic provider.complete failed — treating as unscored",
                exc_info=exc,
                extra={"_fields": {"trace_id": result_state.trace_id}},
            )
            return None
        return parse_critic_response(result.content)
