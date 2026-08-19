"""ToolRegistry — holds all registered Tool instances."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

from stackowl.infra.observability import log
from stackowl.tools.base import Tool
from stackowl.tools.consent import ConsentPolicy, ConsentRequest, ConsentScope

# A tool declaring one of these consent categories MUST be consequential — else
# it would declare itself dangerous yet skip the consent gate (E1-S4 / §17).
_DANGEROUS_CONSENT_CATEGORIES = frozenset({"lock", "alarm", "destructive"})

# FX-08 — below this many words, a description is thin enough that tool_search's
# description-match term (weighted low relative to a name-match by explicit,
# voted design) rarely fires. A lint threshold, not a hard requirement.
_MIN_DESCRIPTION_WORDS = 15

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
        session_key: str | None = None,
        category: str | None = None,
        call_args: dict[str, object] | None = None,
        reply_target: int | str | None = None,
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
            session_key=session_key or "",
            category=effective_category,
            summary=summary,
            reversible=reversible,
            # WHERE to ask. Without it the Telegram prompter had only the session
            # KEY to work from and failed closed on every structured lane.
            reply_target=reply_target,
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
            self._lint_description(tool)
        log.tool.debug(
            "[tools] registry.register: tool registered",
            extra={"_fields": {"tool": tool.name, "source": source_name, "replace": replace}},
        )

    def _lint_description(self, tool: Tool) -> None:
        """FX-08 — warn (never reject) on a thin or duplicate tool description.

        tool_search's lexical scorer weighs a description-match well below a
        name-match (an explicit, voted-and-ported design — see
        tools/meta/tool_search.py's field weights; NOT something this lint
        second-guesses), so a tool with a short or copy-pasted description is
        under-discoverable via tool_search. Surfacing that once, at
        registration, is cheaper than debugging "the model never finds this
        tool" later. Must be called under ``self._lock`` (reads ``self._tools``).
        """
        description = tool.description or ""
        word_count = len(description.split())
        if word_count < _MIN_DESCRIPTION_WORDS:
            log.tool.warning(
                "[tools] registry.register: thin tool description — "
                "tool_search under-ranks short descriptions",
                extra={"_fields": {"tool": tool.name, "word_count": word_count}},
            )
        if not description:
            return
        for existing_name, existing_tool in self._tools.items():
            if existing_name != tool.name and existing_tool.description == description:
                log.tool.warning(
                    "[tools] registry.register: duplicate tool description",
                    extra={"_fields": {"tool": tool.name, "duplicate_of": existing_name}},
                )
                break

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
        usage_scores: Mapping[str, float] | None = None,
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
        identical to the previous implementation. ``usage_scores`` is forwarded
        to the ranker when ``budget`` is set.

        ``usage_scores`` (D05.2) replaced a ``request_text`` relevance ranker.
        The old signal made this method's output a function of the turn's
        question; the new one is a function of the owl's measured history, which
        is stable for the life of a session. NOTE that the caller is still
        responsible for the OTHER half of that stability: ``budget`` carries a
        per-turn ``fixed_cost_tokens``, so calling this every turn with a growing
        history shrinks the fit even under a fixed ordering. See
        ``infra/presented_tools.py`` — the caller memoizes the result rather than
        this method pretending to be pure across turns it cannot see.
        """

        catalog = frozenset(t.name for t in self.all())

        def _schema_for(t: Tool, description: str | None = None) -> dict[str, object]:
            desc = t.description if description is None else description
            if protocol == "anthropic":
                return {"name": t.name, "description": desc, "input_schema": t.parameters}
            return {
                "type": "function",
                "function": {"name": t.name, "description": desc, "parameters": t.parameters},
            }

        def _emit(tools: list[Tool]) -> list[dict[str, object]]:
            """Build schemas for the FINAL presented set (D05.6).

            The single exit for all three paths. Cross-reference stripping can
            only happen here, because it needs to know which tools are present —
            which is not decided until the list is final. Running inside
            to_provider_schema also means D05.2's memo caches the already-stripped
            array, so descriptions stay byte-stable for the session rather than
            varying per turn.
            """
            from stackowl.tools._infra.cross_refs import strip_dangling_references

            presented = frozenset(t.name for t in tools)
            capability_of = {
                t.name: getattr(t.manifest, "requires_capability", None)
                for t in self.all()
            }
            out: list[dict[str, object]] = []
            for t in tools:
                try:
                    desc = strip_dangling_references(
                        t.description,
                        tool_name=t.name,
                        presented=presented,
                        catalog=catalog,
                        capability_of=capability_of,
                    )
                except Exception as exc:  # noqa: BLE001 — never cost a turn its tools
                    log.tool.error(
                        "[tools] cross-reference strip failed — using the raw description",
                        exc_info=exc, extra={"_fields": {"tool": t.name}},
                    )
                    desc = t.description
                out.append(_schema_for(t, desc))
            return out

        if restrict_to is not None:
            from stackowl.tools._infra.presentation import ToolPresentation

            tools = ToolPresentation().select(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated,
                restrict_to=restrict_to,
            )
            return _emit(tools)

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
                usage_scores=usage_scores,
            )
            b = tool_budget_tokens(
                window=budget["window"], fixed_cost_tokens=budget["fixed_cost_tokens"],
            )

            def _size(t: Tool) -> int:
                return len(json.dumps(_schema_for(t))) // 4

            # Cap the COUNT too: a weak model derails when offered too many tools
            # even if they fit in tokens. Effective cap comes from the budget dict's
            # optional "max_tools" (OrchestratorSettings.tool_count_cap), default 40.
            hard_cap = resolve_tool_count_cap(budget.get("max_tools"))
            if len(guaranteed) >= hard_cap:
                # The non-evictable guaranteed set alone already meets/exceeds the
                # configured cap — every discretionary/profile-group/tool_search-
                # hydrated candidate is silently starved (fit_items' loop breaks on
                # its first iteration). This has bitten us before: an operator
                # lowered tool_count_cap below the guaranteed floor, and no
                # candidate tool (e.g. a newly tool_search-hydrated one) could ever
                # be presented again. Loud, not silent.
                log.tool.warning(
                    "registry.to_provider_schema: guaranteed tools >= tool_count_cap "
                    "— discretionary/hydrated tools are entirely starved this turn",
                    extra={"_fields": {"guaranteed": len(guaranteed), "hard_cap": hard_cap}},
                )
            fitted = fit_items(
                guaranteed=guaranteed, candidates=ranked, budget=b, size_of=_size,
                hard_cap=hard_cap,
            )
            # ESC-9 — SAY what was evicted. The budget itself is fine; the defect
            # was that it dropped tools silently, so an operator asking "why can't
            # my browser owl type?" had nothing to read. INFO, because production
            # runs at INFO and a DEBUG line cannot answer that question.
            kept = {t.name for t in fitted}
            dropped = [t.name for t in ranked if t.name not in kept]
            if dropped:
                log.tool.info(
                    "registry.to_provider_schema: eligible tools NOT presented — "
                    "the turn's token budget could not fit them",
                    extra={"_fields": {
                        "dropped": dropped[:20],
                        "dropped_count": len(dropped),
                        "presented": len(fitted),
                        "hard_cap": hard_cap,
                    }},
                )
            return _emit(fitted)

        if profile is None and pins is None and hydrated is None:
            tools = self.all()
        else:
            from stackowl.tools._infra.presentation import ToolPresentation

            tools = ToolPresentation().select(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated
            )
        return _emit(tools)

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

    #: Tool classes whose constructor takes parameters BUT for which ``cls()`` is
    #: correct — the defaults are what ``with_defaults`` always passed. Listed
    #: explicitly so that adding a tool with a constructor forces a decision here
    #: rather than silently getting whatever its defaults happen to be.
    _DEFAULT_CONSTRUCTIBLE: frozenset[str] = frozenset({
        "BatchApproveTool", "ClarifyTool", "CronjobTool", "HeartbeatRespondTool",
        "ImageGenerateTool", "OwlBuildTool", "SendFileTool", "SendMessageTool",
        "TtsTool", "VisionAnalyzeTool", "WaitTool",
    })

    @classmethod
    def with_defaults(cls) -> ToolRegistry:
        """Bootstrap the registry by DISCOVERING every tool under ``tools/`` (D05.1).

        This was ~61 hand-written imports and ~60 ``register()`` calls. A tool is
        now registered by existing; there is no line to forget.

        The per-tool rationale that used to live in the comments here was moved
        VERBATIM into each tool's own module docstring, under a "Registration
        note" heading — it described the tool, not the line that constructed it,
        and a reader looks in the tool's file.

        TWO GROUPS SHARE A DEPENDENCY AND ARE STILL WIRED BY HAND. This is the
        whole reason discovery yields CLASSES rather than instances:
        ``edit``/``apply_patch``/``undo_write`` must share ONE ``UndoStore`` so
        undo can restore what edit snapshotted, and ``todo``/``update_plan`` must
        share one ``PlanStore``. Every one of those constructors accepts
        ``store=None`` and quietly builds its own, so auto-instantiating them
        would register five working tools and leave undo silently unable to undo
        anything — green tests, broken behaviour.

        Anything else with a constructor parameter must be named in
        :data:`_DEFAULT_CONSTRUCTIBLE`, or this raises. Failing loudly at boot is
        the point: the alternative is a future shared-dependency tool quietly
        getting a private instance, which is exactly the bug above.
        """
        from stackowl.tools._infra.discovery import (
            discover_tool_classes,
            requires_explicit_wiring,
        )
        from stackowl.tools.io.undo_store import UndoStore
        from stackowl.tools.planning.store import PlanStore

        registry = cls()

        # edit + apply_patch + undo_write share one UndoStore so undo_write can
        # restore the pre-image that edit/apply_patch snapshotted (E3-S2/E3-S3).
        undo_store = UndoStore()
        # todo + update_plan share one PlanStore so a plan written by one is
        # visible to the other.
        plan_store = PlanStore()
        wired: dict[str, object] = {
            "EditTool": {"store": undo_store},
            "ApplyPatchTool": {"store": undo_store},
            "UndoWriteTool": {"store": undo_store},
            "TodoTool": {"store": plan_store},
            "UpdatePlanTool": {"store": plan_store},
        }

        unwired: list[str] = []
        for tool_cls in discover_tool_classes():
            name = tool_cls.__name__
            kwargs = wired.get(name)
            if kwargs is not None:
                registry.register(tool_cls(**kwargs))  # type: ignore[arg-type]
                continue
            if requires_explicit_wiring(tool_cls) and name not in cls._DEFAULT_CONSTRUCTIBLE:
                unwired.append(name)
                continue
            registry.register(tool_cls())

        if unwired:
            # Loud, not silent. A tool with a constructor that nobody decided
            # about is either a shared dependency (and must be wired) or safe on
            # its defaults (and must say so) — guessing is how undo broke.
            raise RuntimeError(
                "tool(s) with constructor parameters are neither wired nor listed "
                f"in _DEFAULT_CONSTRUCTIBLE: {sorted(unwired)}. Add the shared "
                "dependency to `wired`, or add the class to _DEFAULT_CONSTRUCTIBLE "
                "to record that its defaults are correct."
            )

        log.tool.info(
            "[tools] registry.with_defaults: discovered",
            extra={"_fields": {"tools": len(registry.all())}},
        )
        return registry
