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
    from stackowl.learning.lessons_index import LessonsIndex
    from stackowl.learning.tool_heuristic_store import ToolHeuristicStore
    from stackowl.memory.bridge import MemoryBridge
    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.memory.preferences import PreferenceStore
    from stackowl.messaging.a2a import A2AQueue
    from stackowl.notifications.router import NotificationRouter
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.streaming import StreamRegistry
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
    event_bus: EventBus | None = field(default=None)
    skill_store: SkillIndexStore | None = field(default=None)
    embedding_registry: EmbeddingRegistry | None = field(default=None)
    lessons_index: LessonsIndex | None = field(default=None)
    heuristic_store: ToolHeuristicStore | None = field(default=None)
    consent_gate: ConsequentialActionGate | None = field(default=None)
    clarify_gateway: ClarifyGateway | None = field(default=None)
    web_search_registry: WebSearchRegistry | None = field(default=None)


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
