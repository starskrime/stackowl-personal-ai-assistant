"""Pipeline services context — ambient service injection via ContextVar."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.audit.logger import AuditLogger
    from stackowl.channels.telegram.approach_rating import ApproachRatingTracker
    from stackowl.commands.resolver import CommandResolver
    from stackowl.config.settings import Settings
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.events.bus import EventBus
    from stackowl.gateway.turn_registry import TurnRegistry
    from stackowl.interaction.clarify_gateway import ClarifyGateway
    from stackowl.interaction.cost_pause import CostPauseGuard
    from stackowl.interaction.feedback_classifier import FeedbackClassifier
    from stackowl.interaction.retrieval_intent_classifier import RetrievalIntentClassifier
    from stackowl.interaction.retry_intent_classifier import RetryIntentClassifier
    from stackowl.interaction.schedule_commit_classifier import ScheduleCommitClassifier
    from stackowl.interaction.turn_achievement_judge import TurnAchievementJudge
    from stackowl.interaction.turn_achievement_writer import TurnAchievementWriter
    from stackowl.learning.failure_outcome_miner import RcaVerdict
    from stackowl.learning.lessons_index import LessonsIndex
    from stackowl.learning.tool_heuristic_store import ToolHeuristicStore
    from stackowl.memory.bridge import ConversationStore, MemoryBridge
    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.memory.message_ledger_store import MessageLedgerStore
    from stackowl.memory.preferences import PreferenceStore
    from stackowl.memory.providers import MemoryProviderRegistry
    from stackowl.memory.retry_queue_store import RetryQueueStore
    from stackowl.messaging.a2a import A2AQueue
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.notifications.router import NotificationRouter
    from stackowl.owls.a2a_delegation import A2ADelegator
    from stackowl.owls.concurrency import ConcurrencyGovernor
    from stackowl.owls.registry import OwlRegistry
    from stackowl.owls.session_registry import SessionRegistry
    from stackowl.owls.sticky_route_cache import StickyRouteCache
    from stackowl.pipeline.retry_actuator import RetryActuator
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.streaming import StreamRegistry
    from stackowl.process.registry import ProcessRegistry
    from stackowl.providers.cost_tracker import CostTracker
    from stackowl.providers.registry import ProviderRegistry
    from stackowl.sandbox.governor import SandboxGovernor
    from stackowl.sandbox.selector import SandboxSelector
    from stackowl.sessions.store import SessionStore
    from stackowl.skills.store import SkillIndexStore
    from stackowl.tenancy.identity import IdentityResolver
    from stackowl.tools.browser.runtime import CamoufoxRuntime
    from stackowl.tools.browser.sessions import BrowserSessionRegistry
    from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry
    from stackowl.web_search.registry import WebSearchRegistry


@dataclass
class StepServices:
    """Services available to pipeline steps via get_services()."""

    provider_registry: ProviderRegistry | None = field(default=None)
    stream_registry: StreamRegistry | None = field(default=None)
    memory_bridge: MemoryBridge | None = field(default=None)
    #: The resolved memory-provider set (D08.2 slice C). Carried here so
    #: presentation and dispatch can reach it without threading the whole
    #: MemoryComponents through the pipeline. None on every path that does not
    #: build memory — which is most tests — and `provider_surface` treats that as
    #: the ordinary no-providers case rather than an error.
    memory_providers: MemoryProviderRegistry | None = field(default=None)
    owl_registry: OwlRegistry | None = field(default=None)
    a2a_queue: A2AQueue | None = field(default=None)
    kuzu_adapter: KuzuAdapter | None = field(default=None)
    tool_registry: ToolRegistry | None = field(default=None)
    db_pool: DbPool | None = field(default=None)
    #: The ONE loop's durable store (Bakir, 2026-08-17). Present ⇒ every chat turn
    #: is recorded as a task and completes only when its reply is DELIVERED, so a
    #: turn the fast path drops is recoverable. None ⇒ byte-identical to before the
    #: loop existed: no rows, no recovery, and every existing construction site
    #: (tests included) keeps working untouched.
    durable_task_store: object | None = field(default=None)
    #: The running TaskLoop, so an enqueue can WAKE it rather than making the user
    #: wait out the tick. None ⇒ the tick is the only trigger (correct, just
    #: slower).
    task_loop: object | None = field(default=None)
    browser_runtime: CamoufoxRuntime | None = field(default=None)
    browser_sessions: BrowserSessionRegistry | None = field(default=None)
    audit_logger: AuditLogger | None = field(default=None)
    preference_store: PreferenceStore | None = field(default=None)
    # Retry-queue bookkeeping — persist_turn reads THIS off services to enqueue a
    # pending row whenever a turn ends in the honest floor, so a later background
    # sweep can retry it. None → the retry-queue insert is a no-op (byte-identical
    # to before this feature existed).
    retry_queue_store: RetryQueueStore | None = field(default=None)
    # Universal per-message status lifecycle (pending/completed/failed/absorbed).
    # persist_turn flips this alongside retry_queue on every turn; _handle_ingress
    # inserts the pending row at intake. None -> both are no-ops (byte-identical
    # to before this feature existed).
    message_ledger_store: MessageLedgerStore | None = field(default=None)
    # D01.7 — conversation lanes and their incarnations. Proactive delivery reads
    # THIS to turn a lane back into a channel-native recipient, which a composite
    # lane key can no longer supply by itself. None → delivery falls back to the
    # legacy "the lane IS the chat id" heuristic, i.e. byte-identical to before.
    session_store: SessionStore | None = field(default=None)
    # D01.1 — the frozen per-session system prompt. None (unwired, and every
    # settings-less unit test) means assemble cold-builds every turn exactly as
    # it did before, so the freeze is an enhancement to a working pipeline
    # rather than a precondition for one.
    session_prompt_store: object | None = field(default=None)
    # Approach-rating like/dislike votes — consolidate.py reads THIS off services
    # to record a pending vote + build the inline keyboard for a qualifying final
    # answer. ONE process-wide singleton (in-memory trace_id -> message map) so
    # the Telegram adapter's post-send backfill and the "apr" callback handler
    # observe the SAME pending state consolidate.py wrote. None → the keyboard
    # attach is a byte-identical no-op (feature absent).
    approach_rating_tracker: ApproachRatingTracker | None = field(default=None)
    # Task 7 — manual "do it again" retry path. triage.py reads THESE off
    # services (right after get_services(), before any other routing) to
    # check for a pending retry_queue row and, if the classifier confirms
    # retry intent, dispatch the SAME RetryActuator instance the cron sweep
    # uses (retry_sweep.py, Task 6) immediately instead of waiting up to a
    # minute. Either None → the check is a byte-identical no-op (today's
    # behavior — falls through to normal routing / the cron sweep).
    retry_intent_classifier: RetryIntentClassifier | None = field(default=None)
    retry_actuator: RetryActuator | None = field(default=None)
    notification_router: NotificationRouter | None = field(default=None)
    proactive_deliverer: ProactiveDeliverer | None = field(default=None)
    event_bus: EventBus | None = field(default=None)
    skill_store: SkillIndexStore | None = field(default=None)
    embedding_registry: EmbeddingRegistry | None = field(default=None)
    lessons_index: LessonsIndex | None = field(default=None)
    heuristic_store: ToolHeuristicStore | None = field(default=None)
    consent_gate: ConsequentialActionGate | None = field(default=None)
    clarify_gateway: ClarifyGateway | None = field(default=None)
    # LS4 — the feedback-capture classifier. The pipeline ``feedback`` step reads
    # THIS off services to decide whether a user message is a reaction to the last
    # render (and, if so, its aspect-scoped polarity) before writing an
    # ``output_style`` preference. None → the step is a byte-identical no-op.
    feedback_classifier: FeedbackClassifier | None = field(default=None)
    # PBC — overclaim trigger 3's retrieval-intent classifier. The
    # surface_overclaim_gate async wrapper reads THIS off services to lazily
    # stamp state.requires_retrieval (one fast one-token call, gated by
    # _should_classify_retrieval) before re-evaluating _is_overclaim. None → the
    # stamp is a no-op — requires_retrieval stays False, byte-identical.
    retrieval_intent_classifier: RetrievalIntentClassifier | None = field(default=None)
    # Overclaim trigger 4's scheduling-commitment classifier. The
    # surface_overclaim_gate async wrapper reads THIS off services to lazily
    # stamp state.requires_scheduling_commit (one fast one-token call, gated by
    # _should_classify_schedule_commit) before re-evaluating _is_overclaim.
    # None → the stamp is a no-op — requires_scheduling_commit stays False,
    # byte-identical.
    schedule_commit_classifier: ScheduleCommitClassifier | None = field(default=None)
    #: Writes what would COUNT as a turn being done, from the request alone.
    #: SHADOW (2026-08-31): observed and logged; nothing judges or reopens yet.
    turn_achievement_writer: TurnAchievementWriter | None = field(default=None)
    #: Judges the criterion above against the result, with a structural veto.
    #: SHADOW (2026-08-31): the verdict is logged; nothing is reopened.
    turn_achievement_judge: TurnAchievementJudge | None = field(default=None)
    web_search_registry: WebSearchRegistry | None = field(default=None)
    # E8-S0 — shared budget for in-flight delegated + parliament pipelines.
    # ONE instance, injected here AND into the parliament fan-out so both draw
    # from a single budget (fork-bomb / concurrency rail). None → ungated.
    delegation_governor: ConcurrencyGovernor | None = field(default=None)
    # E8-S1 — Secretary→specialist round-trip orchestrator. The delegate_task
    # tool reads THIS instance off services at execute time (it never builds its
    # own, so the depth/governor/queue rails stay a single source of truth). The
    # same instance shares the governor + a2a_queue wired above. None → the tool
    # degrades to a structured "no delegator wired" result (self-healing, B5).
    a2a_delegator: A2ADelegator | None = field(default=None)
    # E8-S3 — named persistent owl sessions. The sessions_spawn tool reads THIS
    # instance off services at execute time (it never builds its own, so the cap /
    # TTL / mailbox-drain rails stay a single source of truth). Shares the same
    # a2a_queue wired above so a cleared/reaped session drains the right mailbox.
    # None → the tool degrades to a structured "sessions unavailable" result (B5).
    session_registry: SessionRegistry | None = field(default=None)
    # E9-S0 — the process substrate. The (S1) process tool reads THIS instance off
    # services at execute time (it never builds its own, so the concurrency cap /
    # mandatory-TTL / aggregate-buffer / checkpoint rails stay a single source of
    # truth). None → the tool degrades to a structured "process substrate
    # unavailable" result (self-healing, B5).
    process_registry: ProcessRegistry | None = field(default=None)
    # E8-S0cost — ONE shared CostTracker so the per-turn running total the
    # cost-pause guard reads is fed by the SAME instance MoA/router record into.
    # None → no shared tracker (tools fall back to building an ungated local one).
    cost_tracker: CostTracker | None = field(default=None)
    # E8-S0cost — the soft per-turn cost pause. delegate_task + mixture_of_agents
    # read THIS off services and call gate() BEFORE their expensive op; a "Stop"
    # answer aborts that op. None → no pause (feature absent / non-interactive).
    cost_pause_guard: CostPauseGuard | None = field(default=None)
    # E11-S5 — the sandbox backend selector (bwrap-primary, Docker for network).
    # The execute_code tool reads THIS instance off services at execute time (it
    # never builds its own, so the configured backend set + capability probe stay a
    # single source of truth). None → execute_code degrades to a structured "code
    # execution unavailable — no sandbox backend" result and NEVER runs on the host
    # (self-healing, B5; the load-bearing safety invariant).
    sandbox_selector: SandboxSelector | None = field(default=None)
    # E11-S6 — the global sandbox concurrency governor. ONE shared instance bounding
    # total concurrent sandbox runs so N runs × the per-run memory cap cannot OOM the
    # host. The execute_code tool reads THIS off services and acquires a slot around
    # the run; saturated past a bounded wait it REFUSES (typed) and nothing runs.
    # None → ungated (back-compat; the tool runs without a concurrency cap).
    sandbox_governor: SandboxGovernor | None = field(default=None)
    # concurrent-msg Task 10 — the process-wide TurnRegistry (one running turn +
    # FIFO intake per session, plus each turn's steering mailbox). The execute step
    # reads THIS instance off services to build its steering-drain callback: it
    # reaches the running turn via registry.get(state.trace_id).steering_mailbox and
    # folds a [steering] message into the live ReAct loop. None → no steering
    # (fail-safe; the loop proceeds normally, e.g. in non-orchestrated unit tests).
    turn_registry: TurnRegistry | None = field(default=None)
    # STEER-7/F094 — the resolved application Settings, threaded so steps can read
    # config-driven policy (e.g. the per-channel clarify Raise/Stop wait timeout)
    # without a global settings singleton. None in non-orchestrated unit tests →
    # callers fall back to documented defaults (resolve_clarify_wait_timeout → 120s).
    settings: Settings | None = field(default=None)
    # Self-heal degraded boot — set True when EVERY configured provider was
    # unreachable at boot (see StartupOrchestrator._phase_providers). The gateway
    # still comes up so slash commands (e.g. /provider, which needs no LLM) keep
    # working; _dispatch_turn reads THIS to short-circuit conversational/parliament
    # turns with a graceful notice instead of hanging on a dead provider. False →
    # byte-identical (normal routing).
    providers_degraded: bool = field(default=False)
    # Cross-channel identity — maps per-channel handles (e.g. "telegram:123") to a
    # stable identity_key so durable knowledge (preferences, facts) follows the user
    # across channels. None → unconfigured; callers degrade to session_key (per-channel
    # behavior, byte-identical to before this feature existed).
    identity_resolver: IdentityResolver | None = field(default=None)
    # WS-D command-hint resolver (issue 3) — a CommandResolver indexed over the
    # slash-command tree. The pre-delivery command-hint surfacer reads THIS to
    # additively suggest a high-confidence slash command for a natural-language
    # turn (marked, never auto-run). None → no hint (feature off); built only
    # when ui.command_hints is enabled.
    command_hint_resolver: CommandResolver | None = field(default=None)
    # FR-9 — the sticky-routing cache (session_key -> last-resolved owl +
    # intent_class, 30-min TTL). triage.py reads THIS instance to bypass the
    # LLM SecretaryRouter call on short, same-session follow-ups. None → the
    # bypass never fires (byte-identical to pre-FR-9 behavior — always calls
    # the router).
    sticky_route_cache: StickyRouteCache | None = field(default=None)
    # ADR-6 Task 7 — background-incident RCA lookup, keyed by the SAME
    # ``failure_class`` string ``surface_critical_failure`` already derives via
    # ``_critical_failure_classes`` (an exception class name). ``surface_critical_failure``
    # reads THIS off services to enrich its apology/neutral-fallback text with a
    # one-line incident summary when a verified verdict exists for the SAME
    # failure class this turn just hit — reusing the EXISTING cascade/parameter,
    # never a new gate. None → byte-identical (no enrichment, today's text only).
    incident_verdict_lookup: Callable[[str], RcaVerdict | None] | None = field(default=None)

    @property
    def conversation_store(self) -> ConversationStore | None:
        """The LIVE half of the memory bridge — what a normal turn needs (D08.2).

        A PROPERTY over :attr:`memory_bridge`, deliberately not a second field:
        two fields holding one object is two copies of one fact and they drift.
        One source; this is the narrow view of it.

        Steps that only store, retrieve or read recent turns take the bridge from
        here instead, so they cannot reach ``stage``/``recall``/``delete``/
        ``list_staged`` — the retired extraction pipeline's surface, over a table
        with 0 rows and no writers since D08.1's migration 0112.

        Still ``| None``: memory can be disabled, every live caller already
        guards on that, and inventing an object here would break the guard.
        """
        return self.memory_bridge


_ctx: ContextVar[StepServices] = ContextVar("pipeline_services")


def set_services(services: StepServices) -> Token[StepServices]:
    """Set the pipeline services for the current async context. Returns a reset token."""
    return _ctx.set(services)


def reset_services(token: Token[StepServices]) -> None:
    _ctx.reset(token)


def get_services() -> StepServices:
    """Return the current step services. Returns empty StepServices if not set."""
    try:
        return _ctx.get()
    except LookupError:
        return StepServices()


def owner_scope_key(state: PipelineState) -> str:
    """The key durable knowledge is filed under for this turn.

    ``identity_key`` when a resolver produced one (so the same person is one owner
    across Telegram, Slack and the CLI), else the conversation lane.

    WHY THIS IS A FUNCTION. The expression ``state.identity_key or
    state.session_key`` was written inline in four places — preferences, feedback,
    delivery — and MISSING in a fifth: conversation facts were stored under the raw
    lane. That drift is not cosmetic. It is the reason the lane could not be
    re-keyed to a composite (owl-prefixed) value without silently emptying recall:
    four call sites would have followed the identity, one would have followed the
    lane, and only the odd one out held the 109,380 existing rows.

    Knowledge is about a PERSON, not about which owl happened to hear it. Scoping
    it to the owl-prefixed lane would mean telling Brain your timezone and having
    Scout not know it.
    """
    return state.identity_key or state.session_key


def conversation_scope_keys(state: PipelineState) -> tuple[str, ...]:
    """Every key this lane's conversation turns may be filed under.

    THE WRITER AND THE READER DISAGREED. Turns are written under
    :func:`owner_scope_key` — ``identity_key or session_key`` — and were read back
    with ``session_key`` alone, so any turn written while identity resolution
    succeeded became invisible to short-term memory. Worse, the write key is
    CONDITIONAL: the same conversation splits between the two buckets depending on
    whether identity resolved on that particular turn, so neither key holds the
    whole thread.

    Measured 2026-08-16: 2,390 rows under 591 identity-style refs against 13 under
    6 lane keys; one real lane had 33 turns of which the reader could see 7.

    Returning BOTH is what makes recall whole again without a migration, and it
    stays correct if the write key changes shape later. Deduped and
    order-preserving, primary key first.
    """
    seen: dict[str, None] = {}
    for key in (owner_scope_key(state), state.session_key):
        if key:
            seen.setdefault(key, None)
    return tuple(seen)


def resolve_identity_key(services: StepServices, session_key: str) -> str:
    """Resolve the inbound channel handle to a cross-channel identity_key.

    Returns the HANDLE when no resolver is wired, and the handle unchanged when
    the resolver has no alias for it. Either way the answer names a PERSON.

    IT USED TO RETURN "" (ESC-17, Bakir 2026-08-25: "fix core issue"), and that
    empty string was the whole defect. `owner_scope_key` is
    ``state.identity_key or state.session_key``, so "" silently handed the scope
    to `session_key` — which is the composite LANE on the turn path and the RAW
    channel handle on the command path. One field, two meanings, and which one you
    got depended on whether a resolver happened to be wired.

    That defeated owner_scope_key's own stated purpose, which is why this is a
    defect rather than a preference: "Knowledge is about a PERSON, not about which
    owl happened to hear it. Scoping it to the owl-prefixed lane would mean
    telling Brain your timezone and having Scout not know it." The "" return
    produced exactly the owl-prefixed scoping that comment forbids.

    Two live consequences, both closed by this one line. The five-table key-shape
    split (task_outcomes lane 440 / identity 799; staged_facts 103 / 100; tasks
    189 / 126). And /reset's silent under-delete — measured 2026-08-25 on a
    naturally refilled table, staged_facts.source_ref held 8 raw-handle rows
    against 4 lane-shaped ones, and /reset passes the raw handle, so it missed a
    third of them.

    Existing rows are NOT migrated (his decision) and keep their old meaning; this
    changes what is written from here on.
    """
    if services.identity_resolver is None:
        return session_key
    return services.identity_resolver.resolve(session_key)


async def resolve_runner_lane(
    *,
    runner: str,
    runner_id: str,
    owl_name: str,
    channel: str,
    fallback: str,
    parent_session_key: str | None = None,
) -> tuple[str, str, str | None]:
    """Resolve the conversation lane for a non-chat runner (D01.7 Q9).

    Returns ``(session_key, conversation_id, identity_key)`` for a cron job, objective,
    delegated subagent or recovery drive — the same lane machinery a chat turn
    gets, so background work earns its own incarnation, its own boundary and (with
    `D01.1`) its own stable prompt. That is a deliberate divergence from the reference platform,
    which gives non-chat work no lane at all.

    ``parent_session_key`` is the conversation that ASKED for the work, when there
    was one. The lane inherits that conversation's identity, so the runner's
    durable knowledge is filed under the PERSON rather than under machinery nobody
    queries. Two of the four callers can read their parent straight off a row they
    already hold — ``objective.session_key`` and ``task.session_key``, both added
    for invariant I4 — which is the second thing that column bought.

    DEGRADES TO TODAY'S BEHAVIOUR. With no store wired, or if the store errors,
    this returns ``(fallback, "", None)`` — the ad-hoc key the caller used before
    this slice, no incarnation, no identity. A lane is an enhancement to background
    work, never a precondition for it running: an objective must not fail because
    its conversation could not be resolved.
    """
    store = getattr(get_services(), "session_store", None)
    if store is None:
        return fallback, "", None
    try:
        from stackowl.sessions.models import SessionSource

        entry, branch, _ = await store.resolve_for(
            SessionSource(
                owl_name=owl_name, channel=channel,
                runner=runner, runner_id=runner_id,
                parent_session_key=parent_session_key,
            ),
            datetime.datetime.now().astimezone(),
        )
    except Exception as exc:
        log.gateway.error(
            "[pipeline] resolve_runner_lane: falling back to the ad-hoc key — "
            "the work continues without a lane",
            exc_info=exc,
            extra={"_fields": {"runner": runner, "runner_id": runner_id,
                               "fallback": fallback}},
        )
        return fallback, "", None
    log.gateway.info(
        "[pipeline] resolve_runner_lane: exit",
        extra={"_fields": {"runner": runner, "runner_id": runner_id,
                           "session_key": entry.session_key,
                           "conversation_id": entry.conversation_id,
                           "branch": branch.value,
                           "has_identity": entry.identity_key is not None,
                           "parent": parent_session_key}},
    )
    return entry.session_key, entry.conversation_id, entry.identity_key
