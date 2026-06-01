"""Pipeline services context — ambient service injection via ContextVar."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stackowl.audit.logger import AuditLogger
    from stackowl.db.pool import DbPool
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.events.bus import EventBus
    from stackowl.interaction.clarify_gateway import ClarifyGateway
    from stackowl.interaction.cost_pause import CostPauseGuard
    from stackowl.learning.lessons_index import LessonsIndex
    from stackowl.learning.tool_heuristic_store import ToolHeuristicStore
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.memory.preferences import PreferenceStore
    from stackowl.messaging.a2a import A2AQueue
    from stackowl.notifications.deliverer import ProactiveDeliverer
    from stackowl.notifications.router import NotificationRouter
    from stackowl.owls.a2a_delegation import A2ADelegator
    from stackowl.owls.concurrency import ConcurrencyGovernor
    from stackowl.owls.registry import OwlRegistry
    from stackowl.owls.session_registry import SessionRegistry
    from stackowl.pipeline.streaming import StreamRegistry
    from stackowl.providers.cost_tracker import CostTracker
    from stackowl.providers.registry import ProviderRegistry
    from stackowl.skills.store import SkillIndexStore
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
    owl_registry: OwlRegistry | None = field(default=None)
    a2a_queue: A2AQueue | None = field(default=None)
    kuzu_adapter: KuzuAdapter | None = field(default=None)
    tool_registry: ToolRegistry | None = field(default=None)
    db_pool: DbPool | None = field(default=None)
    browser_runtime: CamoufoxRuntime | None = field(default=None)
    browser_sessions: BrowserSessionRegistry | None = field(default=None)
    audit_logger: AuditLogger | None = field(default=None)
    preference_store: PreferenceStore | None = field(default=None)
    notification_router: NotificationRouter | None = field(default=None)
    proactive_deliverer: ProactiveDeliverer | None = field(default=None)
    event_bus: EventBus | None = field(default=None)
    skill_store: SkillIndexStore | None = field(default=None)
    embedding_registry: EmbeddingRegistry | None = field(default=None)
    lessons_index: LessonsIndex | None = field(default=None)
    heuristic_store: ToolHeuristicStore | None = field(default=None)
    consent_gate: ConsequentialActionGate | None = field(default=None)
    clarify_gateway: ClarifyGateway | None = field(default=None)
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
    # E8-S0cost — ONE shared CostTracker so the per-turn running total the
    # cost-pause guard reads is fed by the SAME instance MoA/router record into.
    # None → no shared tracker (tools fall back to building an ungated local one).
    cost_tracker: CostTracker | None = field(default=None)
    # E8-S0cost — the soft per-turn cost pause. delegate_task + mixture_of_agents
    # read THIS off services and call gate() BEFORE their expensive op; a "Stop"
    # answer aborts that op. None → no pause (feature absent / non-interactive).
    cost_pause_guard: CostPauseGuard | None = field(default=None)


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
