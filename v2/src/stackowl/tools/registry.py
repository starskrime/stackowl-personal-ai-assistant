"""ToolRegistry — holds all registered Tool instances."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stackowl.infra.observability import log
from stackowl.tools.base import Tool
from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope

# A tool declaring one of these consent categories MUST be consequential — else
# it would declare itself dangerous yet skip the consent gate (E1-S4 / §17).
_DANGEROUS_CONSENT_CATEGORIES = frozenset({"lock", "alarm", "destructive"})


class _SyncConfirmPrompter:
    """Adapts a legacy ``(tool_name) -> bool`` confirm_fn to the async prompter API.

    Preserves the historical CLI/test contract: True → approve once, False → deny.
    """

    def __init__(self, confirm_fn: Callable[[str], bool]) -> None:
        self._confirm_fn = confirm_fn

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        return ConsentScope.ONCE if self._confirm_fn(req.tool_name) else ConsentScope.DENY


class ConsequentialActionGate:
    """Requires consent before a consequential tool executes.

    The decision logic — trust tiers, session batch, time-window grants and
    always-ask exclusions — lives in :class:`ConsentPolicy`; the gate is the
    thin call site that the pipeline invokes before ``tool.execute()``. With no
    policy and no ``confirm_fn`` it fails CLOSED.
    """

    def __init__(
        self,
        policy: ConsentPolicy | None = None,
        *,
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> None:
        # 1. ENTRY
        log.tool.debug("[gate] ConsequentialActionGate.__init__: entry")
        if policy is None:
            # 2. DECISION — legacy sync confirm_fn vs fail-closed default
            if confirm_fn is not None:
                policy = ConsentPolicy(prompter=_SyncConfirmPrompter(confirm_fn))
            else:
                policy = ConsentPolicy()  # FailClosedPrompter — denies by default
        self._policy = policy
        log.tool.debug(
            "[gate] ConsequentialActionGate.__init__: exit",
            extra={"_fields": {"explicit_policy": policy is not None}},
        )

    @property
    def policy(self) -> ConsentPolicy:
        """The underlying consent policy (so callers can register tiers/grants)."""
        return self._policy

    async def check(
        self,
        tool: Tool,
        *,
        channel: str | None = None,
        session_id: str | None = None,
        category: str | None = None,
    ) -> bool:
        """Return True if execution should proceed.

        Non-consequential tools always pass without consulting the policy.
        Consequential tools delegate to :meth:`ConsentPolicy.request`.
        """
        # 1. ENTRY
        log.tool.debug(
            "[gate] check: entry",
            extra={"_fields": {"tool": tool.name, "severity": tool.manifest.action_severity}},
        )
        # 2. DECISION — skip gate for non-consequential tools
        if tool.manifest.action_severity != "consequential":
            log.tool.debug(
                "[gate] check: exit — non-consequential, allowing",
                extra={"_fields": {"tool": tool.name}},
            )
            return True
        # 3. STEP — delegate to the consent policy (which audits + fails closed).
        # The always-ask category is taken from the TRUSTED manifest; an explicit
        # category (e.g. a tool computing it from validated args) may supplement it,
        # but never from raw LLM-supplied call args (E0-S1 / B2).
        effective_category = tool.manifest.consent_category or category
        allowed = await self._policy.request(
            tool_name=tool.name,
            channel=channel or "",
            session_id=session_id or "",
            category=effective_category,
            summary=tool.description,
        )
        # 4. EXIT
        log.tool.debug(
            "[gate] check: exit",
            extra={"_fields": {"tool": tool.name, "allowed": allowed}},
        )
        return allowed


class ToolRegistry:
    """Process-level registry of available tools."""

    def __init__(self, gate: ConsequentialActionGate | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._source_map: dict[str, list[str]] = {}
        self._gate = gate

    @staticmethod
    def _is_dangerous(tool: Tool) -> bool:
        """A tool is dangerous if it is consequential or declares a consent category."""
        manifest = tool.manifest
        return manifest.action_severity == "consequential" or manifest.consent_category is not None

    def register(self, tool: Tool, source_name: str | None = None, *, replace: bool = False) -> None:
        """Register a tool under its name.

        Hardened (E0-S4): names are unique by default — a collision raises
        :class:`ToolRegistrationError` unless ``replace=True``. A dangerous
        (consequential / consent-category) tool may never shadow an existing
        tool, nor may any tool replace an existing dangerous one — so a skill or
        MCP server can never silently clobber a native consequential tool.
        """
        existing = self._tools.get(tool.name)
        if existing is not None:
            # Fail closed if either side is dangerous — no shadowing of/by a
            # consequential tool, even when replace=True is requested.
            if self._is_dangerous(tool) or self._is_dangerous(existing):
                from stackowl.exceptions import ToolRegistrationError

                raise ToolRegistrationError(
                    tool.name,
                    "refusing to shadow or replace a dangerous-category tool",
                )
            if not replace:
                from stackowl.exceptions import ToolRegistrationError

                raise ToolRegistrationError(
                    tool.name, "already registered (pass replace=True to override)"
                )
            # Intentional replace — drop the stale name from any source mapping.
            for names in self._source_map.values():
                if tool.name in names:
                    names.remove(tool.name)
        # Register-time fail-closed (E1-S4 / §17): a tool that declares a dangerous
        # consent_category but is NOT marked consequential would slip past the gate.
        manifest = tool.manifest
        if manifest.consent_category in _DANGEROUS_CONSENT_CATEGORIES and manifest.action_severity != "consequential":
            from stackowl.exceptions import ToolRegistrationError

            raise ToolRegistrationError(
                tool.name,
                f"consent_category {manifest.consent_category!r} requires action_severity='consequential'",
            )
        self._tools[tool.name] = tool
        if source_name:
            self._source_map.setdefault(source_name, []).append(tool.name)
        log.tool.debug(
            "[tools] registry.register: tool registered",
            extra={"_fields": {"tool": tool.name, "source": source_name, "replace": replace}},
        )

    def unregister_by_source(self, source_name: str) -> int:
        """Remove all tools registered under source_name. Returns count removed."""
        log.tool.debug(
            "[tools] registry.unregister_by_source: entry",
            extra={"_fields": {"source": source_name}},
        )
        names = self._source_map.pop(source_name, [])
        for name in names:
            self._tools.pop(name, None)
        log.tool.debug(
            "[tools] registry.unregister_by_source: exit",
            extra={"_fields": {"source": source_name, "removed": len(names)}},
        )
        return len(names)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def to_provider_schema(
        self,
        protocol: str,
        *,
        profile: list[str] | None = None,
        pins: list[str] | None = None,
        hydrated: set[str] | None = None,
    ) -> list[dict[str, object]]:
        """Emit tool schemas for the given provider protocol.

        With no gating args (the default) every registered tool is emitted —
        backward-compatible. When ``profile``/``pins``/``hydrated`` are supplied
        (the per-owl path, E1-S4), the presented set is DNA-gated and capped via
        :class:`ToolPresentation`; overflow stays reachable through tool_search.
        """
        if profile is None and pins is None and hydrated is None:
            tools = self.all()
        else:
            from stackowl.tools._infra.presentation import ToolPresentation

            tools = ToolPresentation().select(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated
            )
        if protocol == "anthropic":
            return [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        return [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
            }
            for t in tools
        ]

    def render_text_catalog(self, schemas: list[dict[str, Any]]) -> str:
        """Render presented tool schemas into a compact text block for text-protocol mode.

        Used when a model has no native tool-calling: it reads this catalog and replies
        with ``ACTION: <name>`` + a ```json args block, which the ReAct fallback parses.
        Defensive about schema shape (openai/anthropic/gemini differ) — malformed entries
        are skipped, never raised. Kept compact for small-context (4B) models.
        """
        header = (
            "TOOLS (to use one, output: ACTION: <name> then a ```json args block):"
        )
        lines: list[str] = [header]
        for entry in schemas:
            if not isinstance(entry, dict):
                continue
            # openai: {"function": {name, description, parameters}}; anthropic: flat.
            fn = entry.get("function")
            body = fn if isinstance(fn, dict) else entry
            name = body.get("name")
            if not isinstance(name, str) or not name:
                continue
            description = body.get("description")
            params = body.get("parameters")
            if not isinstance(params, dict):
                params = body.get("input_schema") if isinstance(body.get("input_schema"), dict) else {}
            props = params.get("properties") if isinstance(params, dict) else None
            arg_names = list(props.keys()) if isinstance(props, dict) else []
            sig = ", ".join(arg_names)
            desc = ""
            if isinstance(description, str) and description:
                first = description.strip().splitlines()[0]
                desc = f" — {first}"
            lines.append(f"- {name}({sig}){desc}")
        catalog = "\n".join(lines)
        log.tool.debug(
            "[tools] registry.render_text_catalog: exit",
            extra={"_fields": {"tool_count": len(lines) - 1, "chars": len(catalog)}},
        )
        return catalog

    @classmethod
    def with_defaults(cls) -> ToolRegistry:
        """Bootstrap the registry with the foundation tools + browser family."""
        from stackowl.tools.agents.delegate_task import DelegateTaskTool
        from stackowl.tools.agents.mixture_of_agents import MixtureOfAgentsTool
        from stackowl.tools.agents.sessions_send import SessionsSendTool
        from stackowl.tools.agents.sessions_spawn import SessionsSpawnTool
        from stackowl.tools.browser.back import BrowserBackTool
        from stackowl.tools.browser.browse import BrowserBrowseTool
        from stackowl.tools.browser.console import BrowserConsoleTool
        from stackowl.tools.browser.dialog import BrowserDialogTool
        from stackowl.tools.browser.get_images import BrowserGetImagesTool
        from stackowl.tools.browser.press import BrowserPressTool
        from stackowl.tools.browser.snapshot import BrowserSnapshotTool
        from stackowl.tools.browser.tools import ATOMIC_BROWSER_TOOLS
        from stackowl.tools.interaction.clarify import ClarifyTool
        from stackowl.tools.io.apply_patch import ApplyPatchTool
        from stackowl.tools.io.edit import EditTool
        from stackowl.tools.io.pdf import PdfTool
        from stackowl.tools.io.read_file import ReadFileTool
        from stackowl.tools.io.search_files import SearchFilesTool
        from stackowl.tools.io.undo_store import UndoStore, UndoWriteTool
        from stackowl.tools.io.web_fetch import WebFetchTool
        from stackowl.tools.io.write_file import WriteFileTool
        from stackowl.tools.knowledge.memory import MemoryTool
        from stackowl.tools.knowledge.reflect_now import ReflectNowTool
        from stackowl.tools.knowledge.session_search import SessionSearchTool
        from stackowl.tools.knowledge.skill_manage import SkillManageTool
        from stackowl.tools.knowledge.skill_view import SkillViewTool
        from stackowl.tools.knowledge.skills_list import SkillsListTool
        from stackowl.tools.knowledge.synthesize_skills import SynthesizeSkillsTool
        from stackowl.tools.knowledge.transcripts import TranscriptsTool
        from stackowl.tools.meta.tool_describe import ToolDescribeTool
        from stackowl.tools.meta.tool_search import ToolSearchTool
        from stackowl.tools.planning.store import PlanStore
        from stackowl.tools.planning.todo import TodoTool
        from stackowl.tools.planning.update_plan import UpdatePlanTool
        from stackowl.tools.scheduling.cronjob import CronjobTool
        from stackowl.tools.scheduling.heartbeat_respond import HeartbeatRespondTool
        from stackowl.tools.scheduling.send_file import SendFileTool
        from stackowl.tools.scheduling.send_message import SendMessageTool
        from stackowl.tools.search.web_search import WebSearchTool
        from stackowl.tools.system.shell import ShellTool

        registry = cls()
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(SearchFilesTool())
        registry.register(PdfTool())
        # edit + apply_patch + undo_write share one UndoStore so undo_write can
        # restore the pre-image that edit/apply_patch snapshotted (E3-S2/E3-S3).
        _undo_store = UndoStore()
        registry.register(EditTool(store=_undo_store))
        registry.register(ApplyPatchTool(store=_undo_store))
        registry.register(UndoWriteTool(store=_undo_store))
        registry.register(ShellTool())
        registry.register(WebFetchTool())
        # web_search — reads get_services().web_search_registry at execute time, so
        # no constructor wiring here (the registry is built in the gateway phase).
        registry.register(WebSearchTool())
        # cronjob — schedules agent-goal jobs via the JobScheduler facade it
        # builds from get_services().db_pool at execute time (no constructor
        # wiring; reuses the goal_execution handler — E7-S1).
        registry.register(CronjobTool())
        # heartbeat_respond — declares a heartbeat turn's outcome and (notify=True)
        # routes a clamped Notification through get_services().proactive_deliverer
        # at execute time (the S0 transport chokepoint); no constructor wiring.
        registry.register(HeartbeatRespondTool())
        # send_message — agent-initiated outbound text; routes a clamped (normal)
        # Notification through get_services().proactive_deliverer (the S0 transport
        # chokepoint) at execute time. Consequential: the registry's consent gate
        # fires before execute (fails closed off-TTY). No constructor wiring.
        registry.register(SendMessageTool())
        # send_file — agent-initiated outbound FILE/media; threads a workspace-scoped
        # path through a Notification(file_path=...) into get_services().
        # proactive_deliverer (the S0 chokepoint), which routes it to the channel
        # adapter's send_file. Consequential: the consent gate fires before execute
        # (fails closed off-TTY). Workspace-scoped + size-capped + flood-capped.
        registry.register(SendFileTool())
        # delegate_task — hands a sub-task to a specialist owl via the shared
        # A2ADelegator resolved off get_services().a2a_delegator at execute time
        # (no constructor wiring; the depth/width rails live in the tool). The S0
        # execution gate withholds it at delegation_depth>0 (E8-S1).
        registry.register(DelegateTaskTool())
        # mixture_of_agents — fans one hard question across healthy_distinct()
        # providers, then synthesizes via the parliament synthesizer. Reads
        # provider_registry/db_pool/event_bus off get_services() at execute time
        # (no constructor wiring). Self-healing: partial-ensemble tolerant,
        # structured refusal on a thin roster. Severity read (E8-S2).
        registry.register(MixtureOfAgentsTool())
        # sessions_spawn — creates a named persistent owl session in the DI
        # SessionRegistry resolved off get_services().session_registry at execute
        # time (no constructor wiring; the cap/TTL/drain rails live in the
        # registry). The S0 execution gate withholds it at delegation_depth>0
        # (it is in _CHILD_EXCLUDED_TOOLS) so a child cannot spawn (E8-S3).
        registry.register(SessionsSpawnTool())
        # sessions_send — CONTINUE-RUN: looks an existing session up by label in
        # the DI SessionRegistry (get_services().session_registry) and runs its owl
        # once with the persisted history + the new message under the shared
        # delegation_governor (depth=1), persisting the grown history. Self-healing:
        # unknown session / run failure / timeout / rate-limit → structured, session
        # preserved, never raises. The S0 execution gate withholds it at
        # delegation_depth>0 (it is in _CHILD_EXCLUDED_TOOLS). Severity write (E8-S4).
        registry.register(SessionsSendTool())
        for tool_cls in ATOMIC_BROWSER_TOOLS:
            registry.register(tool_cls())
        registry.register(BrowserBrowseTool())
        registry.register(BrowserSnapshotTool())
        registry.register(BrowserBackTool())
        registry.register(BrowserPressTool())
        registry.register(BrowserGetImagesTool())
        registry.register(BrowserConsoleTool())
        registry.register(BrowserDialogTool())
        # E1 meta tools — always present (tool_search is the overflow-discovery
        # primitive per ADR-11; tool_describe is its inspect sibling).
        registry.register(ToolSearchTool())
        registry.register(ToolDescribeTool())
        registry.register(MemoryTool())
        registry.register(SkillManageTool())
        registry.register(SkillViewTool())
        registry.register(SkillsListTool())
        # Phase B — wire the EXISTING self-improvement engines as owl tools.
        # reflect_now constructs ReflectionWriterHandler off get_services() at
        # execute time (self-learning); synthesize_skills constructs
        # SkillSynthesizerHandler (gap-analysis + skill-build). REUSE the handlers
        # (no logic reimplemented). synthesize_skills is consequential (authors
        # learned/ skills) → consent-gated; reflect_now is read.
        registry.register(ReflectNowTool())
        registry.register(SynthesizeSkillsTool())
        registry.register(SessionSearchTool())
        registry.register(TranscriptsTool())
        # todo + update_plan share ONE PlanStore so they write a single plan slot
        # (operator decision): todo mutates individual items; update_plan replaces
        # the whole plan — same source of truth (cf. the shared UndoStore above).
        _plan_store = PlanStore()
        registry.register(TodoTool(store=_plan_store))
        registry.register(UpdatePlanTool(store=_plan_store))
        # clarify — ask the user mid-turn and BLOCK until they answer (default
        # 30-minute park timeout; the concurrent gateway loop frees the loop).
        registry.register(ClarifyTool())
        return registry
