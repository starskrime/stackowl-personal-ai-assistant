"""ToolRegistry — holds all registered Tool instances."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from stackowl.infra.observability import log
from stackowl.tools.base import Tool
from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope

# A tool declaring one of these consent categories MUST be consequential — else
# it would declare itself dangerous yet skip the consent gate (E1-S4 / §17).
_DANGEROUS_CONSENT_CATEGORIES = frozenset({"lock", "alarm", "destructive"})

# Defensive cap on the summary shown in the consent prompt. A tool's
# consent_summary() is supposed to be bounded already (E11 GAP-A), but the gate
# truncates regardless so a buggy/hostile summary can never flood the prompt.
_MAX_CONSENT_SUMMARY_CHARS = 1200


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
        call_args: dict[str, object] | None = None,
    ) -> bool:
        """Return True if execution should proceed.

        Non-consequential tools always pass without consulting the policy.
        Consequential tools delegate to :meth:`ConsentPolicy.request`.

        ``call_args`` are the validated per-call arguments (E11 GAP-A): when the
        tool builds a per-call :meth:`Tool.consent_summary`, the gate shows THAT
        (e.g. the code + language + network for ``execute_code``) so the user
        approves what will actually run — not the static tool description.
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
        summary = self._build_summary(tool, call_args)
        reversible = self._is_reversible(tool)
        allowed = await self._policy.request(
            tool_name=tool.name,
            channel=channel or "",
            session_id=session_id or "",
            category=effective_category,
            summary=summary,
            reversible=reversible,
        )
        # 4. EXIT
        log.tool.debug(
            "[gate] check: exit",
            extra={"_fields": {"tool": tool.name, "allowed": allowed}},
        )
        return allowed

    @staticmethod
    def _is_reversible(tool: Tool) -> bool:
        """Derive a low-blast-radius REVERSIBLE signal from the TRUSTED manifest (F-27).

        Reuses the existing ``commit_coupling`` declaration rather than inventing a
        keyword list: only ``"transactional"`` — the effect is atomic with our OWN
        local ledger (e.g. a write to our SQLite), so it is locally owned and
        rollback-able — counts as reversible. ``"unconfirmed"`` (remote/lossy sends),
        ``"idempotent_keyed"`` (replay-safe but downstream-remote), and ``None``
        (undeclared) all stay irreversible ⇒ ALWAYS_ASK (fail-safe). Never raises —
        an unreadable manifest is treated as irreversible.
        """
        try:
            return tool.manifest.commit_coupling == "transactional"
        except Exception as exc:
            log.tool.warning(
                "[gate] could not read commit_coupling — treating as irreversible",
                exc_info=exc,
                extra={"_fields": {"tool": tool.name}},
            )
            return False

    @staticmethod
    def _build_summary(tool: Tool, call_args: dict[str, object] | None) -> str:
        """Resolve the consent-prompt summary, preferring the per-call digest.

        Tries the tool's :meth:`Tool.consent_summary` (the trusted, bounded view of
        what THIS call does — E11 GAP-A); falls back to the static
        :attr:`Tool.description`. The result is truncated to
        :data:`_MAX_CONSENT_SUMMARY_CHARS` so a buggy/oversized summary can never
        flood the prompt. Never raises — a summary failure degrades to the
        description, never blocks the gate.
        """
        summary: str | None = None
        try:
            summary = tool.consent_summary(**(call_args or {}))
        except Exception as exc:  # B5 — a summary error must not block the gate
            log.tool.warning(
                "[gate] consent_summary raised — falling back to description",
                exc_info=exc,
                extra={"_fields": {"tool": tool.name}},
            )
        text = summary if summary else tool.description
        if len(text) > _MAX_CONSENT_SUMMARY_CHARS:
            text = text[:_MAX_CONSENT_SUMMARY_CHARS] + "…[truncated]"
        return text


class ToolRegistry:
    """Process-level registry of available tools."""

    def __init__(self, gate: ConsequentialActionGate | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._source_map: dict[str, list[str]] = {}
        self._gate = gate
        # F045 — the registry is a process-level singleton read from concurrent
        # dispatch paths (parallel across chats) while tool_build registers a
        # learned tool LIVE mid-turn. A reentrant lock guards every mutation and
        # every snapshot of the name→tool dict + source map. It is held only for
        # the O(1) dict op / list copy — never across any tool call — so it never
        # serializes the actual work. RLock so register() can call _is_dangerous
        # / nested helpers without self-deadlock. threading (not asyncio) because
        # register/unregister/all/get are SYNC methods reachable off-loop.
        self._lock = threading.RLock()

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
        # Register-time fail-closed (E1-S4 / §17): a tool that declares a dangerous
        # consent_category but is NOT marked consequential would slip past the gate.
        # Computed outside the lock (no shared state read).
        manifest = tool.manifest
        if manifest.consent_category in _DANGEROUS_CONSENT_CATEGORIES and manifest.action_severity != "consequential":
            from stackowl.exceptions import ToolRegistrationError

            raise ToolRegistrationError(
                tool.name,
                f"consent_category {manifest.consent_category!r} requires action_severity='consequential'",
            )
        # F045 — read-existing + mutate under one lock so a concurrent dispatch
        # never observes a half-updated dict/source map (and two registers cannot
        # clobber each other).
        with self._lock:
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
        with self._lock:
            names = self._source_map.pop(source_name, [])
            for name in names:
                self._tools.pop(name, None)
        log.tool.debug(
            "[tools] registry.unregister_by_source: exit",
            extra={"_fields": {"source": source_name, "removed": len(names)}},
        )
        return len(names)

    def unregister(self, name: str) -> bool:
        """Remove a single tool by name; return True if it was present (F044).

        THE public single-name removal seam — atomically drops the name→tool
        entry AND any source-map references under the registry lock, so a learned
        tool (tool_build) can be retired without poking ``_tools``/``_source_map``
        directly. A dangerous (consequential / consent-category) tool may not be
        silently dropped — removing one returns ``False`` and logs a warning so a
        native consequential tool can never be unregistered out from under the
        gate by a learned-source cleanup path.
        """
        with self._lock:
            tool = self._tools.get(name)
            if tool is None:
                return False
            if self._is_dangerous(tool):
                log.tool.warning(
                    "[tools] registry.unregister: refusing to drop a dangerous-category tool",
                    extra={"_fields": {"tool": name}},
                )
                return False
            self._tools.pop(name, None)
            for names in self._source_map.values():
                if name in names:
                    names.remove(name)
        log.tool.debug(
            "[tools] registry.unregister: removed",
            extra={"_fields": {"tool": name}},
        )
        return True

    # F-26 — how many effectful failures of the SAME tool THIS turn before the
    # get/dispatch surface emits a prior-failure advisory. 2 = a repeated pattern
    # (one failure is noise; two is a trend worth flagging).
    _REPEAT_FAILURE_ADVISORY_THRESHOLD = 2

    def get(self, name: str) -> Tool | None:
        with self._lock:
            tool = self._tools.get(name)
        # F-26 — before handing the tool to the dispatcher, consult the turn-scoped
        # outcome ledger (read-only, in-process, no DB) for recent REPEATED failures
        # of this same tool and emit an ADVISORY. This never blocks (the tool is
        # still returned) and writes NOTHING back — a pure consult of existing
        # outcomes, no negative learning. Outside the lock: the consult touches only
        # the per-turn ContextVar ledger, never the registry dict.
        if tool is not None:
            self._advise_on_prior_failures(name)
        return tool

    @classmethod
    def _advise_on_prior_failures(cls, name: str) -> None:
        """Log a read-only advisory when this tool has repeatedly failed THIS turn.

        Consults :func:`tool_outcome_ledger.get_outcomes` (the in-process per-turn
        ledger the backend already binds) and counts effectful failures of ``name``
        via the shared :func:`is_effectful_failure` predicate. Never raises, never
        blocks, never writes. When the ledger is unbound (introspection off the turn
        path) ``get_outcomes`` returns empty ⇒ silent. DEFERRED: a cross-turn
        PERSISTENT trust history (``tool_outcome_trust_counts``) is not consulted
        here — the registry holds no DB handle (see report).
        """
        try:
            from stackowl.infra.tool_outcome_ledger import get_outcomes, is_effectful_failure

            failures = sum(
                1
                for o in get_outcomes()
                if o.name == name
                and is_effectful_failure(
                    o.action_severity, o.success, o.side_effect_committed, o.verified
                )
            )
            if failures >= cls._REPEAT_FAILURE_ADVISORY_THRESHOLD:
                log.tool.warning(
                    "[tools] registry.get: prior-failure advisory — tool repeatedly "
                    "failed this turn (advisory only, not blocked)",
                    extra={"_fields": {"tool": name, "prior_effectful_failures": failures}},
                )
        except Exception as exc:  # a consult must NEVER break dispatch
            log.tool.error(
                "[tools] registry.get: prior-failure consult failed",
                exc_info=exc,
                extra={"_fields": {"tool": name}},
            )

    def source_of(self, name: str) -> str | None:
        """Return the source name that registered tool ``name``, or ``None``.

        Used by the skill loader (PLUG-3/F047) to tell an idempotent re-register
        of the SAME source apart from a genuine cross-source name collision.
        """
        with self._lock:
            for source, names in self._source_map.items():
                if name in names:
                    return source
            return None

    def all(self) -> list[Tool]:
        # Snapshot under the lock — never iterate the live dict (F045: a concurrent
        # register/unregister must not raise "dict changed size during iteration").
        with self._lock:
            return list(self._tools.values())

    def to_provider_schema(
        self,
        protocol: str,
        *,
        profile: list[str] | None = None,
        pins: list[str] | None = None,
        hydrated: set[str] | None = None,
        restrict_to: frozenset[str] | None = None,
        request_text: str | None = None,
        budget: dict[str, int] | None = None,
    ) -> list[dict[str, object]]:
        """Emit tool schemas for the given provider protocol.

        With no gating args (the default) every registered tool is emitted —
        backward-compatible. When ``profile``/``pins``/``hydrated`` are supplied
        (the per-owl path, E1-S4), the presented set is DNA-gated and capped via
        :class:`ToolPresentation`; overflow stays reachable through tool_search.

        ``restrict_to`` (E2-S3): when a task has a planned envelope, pass the
        frozenset of planned tool names here. The presented set is narrowed to
        ``always_present`` (discovery) ∪ (``restrict_to`` ∩ catalog). The broad
        base set + profile groups are dropped for this turn. ``is not None``,
        NOT truthiness — ``frozenset()`` yields discovery-only, never base+groups.

        ``budget`` (opt-in, Task 4): when supplied as ``{"window": N,
        "fixed_cost_tokens": M}``, ranks candidates via
        :class:`ToolPresentation.rank_candidates` and greedy-fits them into the
        measured token budget via :func:`fit_items`. Guaranteed (base + always-
        present) are never dropped. When ``None`` (default) behavior is byte-
        identical to the previous implementation. ``request_text`` is forwarded
        to the relevance ranker when ``budget`` is set.
        """

        def _schema_for(t: Tool) -> dict[str, object]:
            if protocol == "anthropic":
                return {"name": t.name, "description": t.description, "input_schema": t.parameters}
            return {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
            }

        if restrict_to is not None:
            from stackowl.tools._infra.presentation import ToolPresentation

            tools = ToolPresentation().select(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated,
                restrict_to=restrict_to,
            )
            return [_schema_for(t) for t in tools]

        if budget is not None:
            import json

            from stackowl.pipeline.context_budget import (
                fit_items,
                resolve_tool_count_cap,
                tool_budget_tokens,
            )
            from stackowl.tools._infra.presentation import ToolPresentation

            guaranteed, ranked = ToolPresentation().rank_candidates(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated,
                request_text=request_text,
            )
            b = tool_budget_tokens(
                window=budget["window"], fixed_cost_tokens=budget["fixed_cost_tokens"],
            )

            def _size(t: Tool) -> int:
                return len(json.dumps(_schema_for(t))) // 4

            # Cap the COUNT too: a weak model derails when offered too many tools
            # even if they fit in tokens. Effective cap comes from the budget dict's
            # optional "max_tools" (OrchestratorSettings.tool_count_cap), default 40.
            fitted = fit_items(
                guaranteed=guaranteed, candidates=ranked, budget=b, size_of=_size,
                hard_cap=resolve_tool_count_cap(budget.get("max_tools")),
            )
            return [_schema_for(t) for t in fitted]

        if profile is None and pins is None and hydrated is None:
            tools = self.all()
        else:
            from stackowl.tools._infra.presentation import ToolPresentation

            tools = ToolPresentation().select(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated
            )
        return [_schema_for(t) for t in tools]

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
        from stackowl.tools.code.execute_code import ExecuteCodeTool
        from stackowl.tools.interaction.batch_approve import BatchApproveTool
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
        from stackowl.tools.knowledge.output_preference import SetOutputPreferenceTool
        from stackowl.tools.knowledge.reflect_now import ReflectNowTool
        from stackowl.tools.knowledge.session_search import SessionSearchTool
        from stackowl.tools.knowledge.skill_manage import SkillManageTool
        from stackowl.tools.knowledge.skill_view import SkillViewTool
        from stackowl.tools.knowledge.skills_list import SkillsListTool
        from stackowl.tools.knowledge.synthesize_skills import SynthesizeSkillsTool
        from stackowl.tools.knowledge.transcripts import TranscriptsTool
        from stackowl.tools.media.browser_vision import BrowserVisionTool
        from stackowl.tools.media.image_generate import ImageGenerateTool
        from stackowl.tools.media.tts import TtsTool
        from stackowl.tools.media.vision_analyze import VisionAnalyzeTool
        from stackowl.tools.meta.note_applied_lesson import NoteAppliedLessonTool
        from stackowl.tools.meta.owl_build import OwlBuildTool
        from stackowl.tools.meta.tool_build import ToolBuildTool
        from stackowl.tools.meta.tool_describe import ToolDescribeTool
        from stackowl.tools.meta.tool_search import ToolSearchTool
        from stackowl.tools.planning.store import PlanStore
        from stackowl.tools.planning.todo import TodoTool
        from stackowl.tools.planning.update_plan import UpdatePlanTool
        from stackowl.tools.process.process_tool import ProcessTool
        from stackowl.tools.process.wait_tool import WaitTool
        from stackowl.tools.scheduling.cronjob import CronjobTool
        from stackowl.tools.scheduling.heartbeat_respond import HeartbeatRespondTool
        from stackowl.tools.scheduling.objective_tool import ObjectiveTool
        from stackowl.tools.scheduling.owl_schedule import OwlScheduleTool
        from stackowl.tools.scheduling.send_file import SendFileTool
        from stackowl.tools.scheduling.send_message import SendMessageTool
        from stackowl.tools.search.web_search import WebSearchTool
        from stackowl.tools.system.shell import ShellTool
        from stackowl.tools.tasks.task_status import TaskStatusTool

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
        # objective — creates a STANDING OBJECTIVE (decomposed + driven by the
        # objective_driver handler) from get_services().db_pool +
        # provider_registry at execute time (no constructor wiring — 1D).
        registry.register(ObjectiveTool())
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
        registry.register(TaskStatusTool())
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
        # note_applied_lesson — non-consequential pillar ④ self-report: the model
        # honestly records that a surfaced lesson changed its actions this turn.
        registry.register(NoteAppliedLessonTool())
        # tool_build — self-extension meta-tool (H4): the agent authors a NEW
        # declarative tool (validate → security-scan → consent → persist →
        # register live → reload on every boot). Authored tools run only via the
        # allowlisted shell argv boundary (no in-process eval). Consequential:
        # consent-gated at dispatch + a second internal consent at the persist step.
        registry.register(ToolBuildTool())
        # owl_build — self-extending owl-builder (Phase-2 A): create/edit/retire a
        # specialist owl (consent-gated, depth-0 only, child-excluded at dispatch).
        registry.register(OwlBuildTool())
        # owl_schedule — the user's off-ramp (TS11): pause/snooze/resume a scheduled
        # owl's proactive pokes (recoverable; never deletes the owl). write-severity
        # (instant, no consent); toggles the owl's projected job row via the scheduler.
        registry.register(OwlScheduleTool())
        registry.register(MemoryTool())
        registry.register(SetOutputPreferenceTool())
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
        # batch_approve — present N planned consequential actions as ONE batch
        # consent (J8). Reuses the clarify_gateway round-trip for the single
        # prompt; on approve-all it executes each action DIRECTLY (pre-consented,
        # bypassing the per-action gate) + audits. Severity write (NOT
        # consequential) so the per-action dispatch gate does not double-prompt:
        # the batch presentation IS the consent. No constructor wiring — it reads
        # tool_registry / clarify_gateway / audit_logger off get_services().
        registry.register(BatchApproveTool())
        # process — run/supervise a long-running or interactive background OS
        # process (start/poll/log/write/submit/kill/close/list). A thin surface over
        # get_services().process_registry (E9-S0): the catastrophic gate + concurrency
        # cap + mandatory TTL live INSIDE the registry; the tool surfaces its
        # structured refusals as clean results. No constructor wiring; severity write.
        registry.register(ProcessTool())
        # wait — pause the turn for a duration OR (the correct way to await a
        # background process) block until a `process`-started process exits. A thin
        # read-severity surface over get_services().process_registry (E9-S2): the
        # deadline uses an injected Clock; the poll loop sleeps between polls (never
        # a busy spin) and honors cancellation. No constructor wiring; severity read.
        registry.register(WaitTool())
        # vision_analyze — describe / answer a question about an image (local path
        # or http(s) URL) on the LOCAL-FIRST vision substrate (E10-S1). Composes the
        # ImageLoader + VisionSelector + a provider.complete() image-block call; the
        # image stays on-box when a local vision model is configured, and a CLOUD
        # backend is disclosed in the output (egress, mirroring pdf Mode B). Reads
        # get_services().provider_registry at execute time (no constructor wiring);
        # self-healing → structured result, never raises. Severity read; group media.
        registry.register(VisionAnalyzeTool())
        # browser_vision — screenshot the CURRENT browser page (the E2 mechanism:
        # sessions.get_page + page.screenshot under screenshots_dir) and analyze it
        # on the same LOCAL-FIRST vision substrate as vision_analyze (the shared
        # analyze_image_bytes core). The screenshot lives outside the workspace, so
        # the captured bytes are fed straight to the analyzer (not the workspace-
        # confined ImageLoader). Returns the description + screenshot path; a CLOUD
        # backend is disclosed (egress). Reads get_services() at execute time (no
        # constructor wiring); self-healing → structured result, never raises.
        # Severity read; group media.
        registry.register(BrowserVisionTool())
        # tts — synthesize speech from text on the LOCAL-FIRST TTS substrate
        # (E10-S3). Composes the TtsSelector (local OSS engine first, opt-in cloud
        # fallback only when enabled + configured) over the media/tts backends. The
        # text stays on-box when the local engine is available; a CLOUD fallback is
        # disclosed in the output (egress, mirroring pdf Mode B). Returns the audio
        # PATH under media_dir (send_file delivers it), never raw bytes. Builds its
        # selector from Settings().tts at execute time (no constructor wiring);
        # self-healing → structured result, never raises. Severity read; group media.
        registry.register(TtsTool())
        # image_generate — generate an image from a prompt on the LOCAL-FIRST image
        # substrate (E10-S4). Composes the ImageSelector (a capability-PROBED local
        # SDXL model first — only where x86+CUDA+enough memory/disk clears, so an
        # incapable Tegra host NEVER pip-installs a multi-GB wheel the probe rejects;
        # opt-in cloud fallback only when enabled + configured). The prompt stays
        # on-box when local is available; a CLOUD fallback discloses egress + cost.
        # Returns the image PATH under media_dir (send_file delivers it), never raw
        # bytes. Builds its selector from Settings().image at execute time (no
        # constructor wiring); self-healing → structured result, never raises.
        # Severity read; group media.
        registry.register(ImageGenerateTool())
        # execute_code — run code in an ISOLATED sandbox (E11-S5, the keystone tool).
        # Reads get_services().sandbox_selector at execute time (no constructor
        # wiring; the bwrap-primary/Docker-for-network policy + capability probe live
        # in the selector). Consequential + always-ask: the consent gate fires before
        # execute and shows the actual code (bounded) + language + network (GAP-A) via
        # consent_summary; a delegated child (depth>0) is refused at dispatch (GAP-B).
        # Self-healing: no selector / selector-unavailable / backend error →
        # structured "unavailable", NEVER a host subprocess. Severity consequential;
        # group code.
        registry.register(ExecuteCodeTool())
        return registry
