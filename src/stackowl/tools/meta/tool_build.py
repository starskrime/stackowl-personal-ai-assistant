"""tool_build — the agent AUTHORS, vets, persists and registers a NEW tool.

This is the self-extension meta-tool of H4. When the agent hits a capability gap
(no tool wraps the CLI it needs), instead of refusing it can MINT a reusable tool:
it supplies a DECLARATIVE spec (name / params / a fixed argv template) and this tool
runs the spec through a HARD chokepoint and, on success, registers it LIVE and
persists it so it survives reboots.

SAFETY — the authored tool is NOT model-authored code. Its ``argv_template`` is a
LIST (argv) whose values are substituted as WHOLE elements and run through the
existing allowlisted shell ``create_subprocess_exec`` boundary (``shell=False``), so
a value can never become code. There is NO in-process eval/exec of model text and
NO reuse of the skill loader's ``tools/*.py`` exec path. The author/vet chokepoint
REUSES the ``skill_manage`` machinery:

    pydantic parse → validate_spec (structured) → collision pre-check → HARD
    security_scan_gate (fails closed on its own crash) → consent (consequential,
    fail-closed off-TTY) → persist with audit/snapshot provenance → register live.

NOTHING is persisted or registered if ANY gate fails. ``delete`` removes the spec +
unregisters (resurrectable via the audit snapshot); ``list`` lists learned tools.


Registration note (moved verbatim from ToolRegistry.with_defaults by D05.1,
when auto-discovery replaced the hand-written register() calls; the rationale
belongs with the tool, not with the line that used to construct it):

    tool_build — self-extension meta-tool (H4): the agent authors a NEW
    declarative tool (validate → security-scan → consent → persist →
    register live → reload on every boot). Authored tools run only via the
    allowlisted shell argv boundary (no in-process eval). Consequential:
    consent-gated at dispatch + a second internal consent at the persist step.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from stackowl.commands.spec.submit import submit_command
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.knowledge.skill_validation import security_scan_gate
from stackowl.tools.meta.tool_build_commands import CREATE, DELETE, DeleteToolPayload
from stackowl.tools.meta.tool_spec import LearnedToolSpec, validate_spec

# Isolated toolset group so a read-only owl never gets self-extension hydrated.
_TOOLSET_GROUP = "meta_write"
# A NON-dangerous consent category — the author may not mint a dangerous one.
_CONSENT_CATEGORY = "tool_build"

_VALID_ACTIONS: tuple[str, ...] = ("create", "delete", "list")


class ToolBuildTool(Tool):
    """Author / delete / list agent-authored (learned) tools."""

    @property
    def name(self) -> str:
        return "tool_build"

    @property
    def description(self) -> str:
        return (
            "Author a genuinely NEW reusable tool to overcome a capability gap, "
            "instead of refusing. You supply a declarative spec: a name, a "
            "description, typed params, and a fixed argv template (a LIST: "
            "argv[0] is the program, each '{param}' is a WHOLE argv token). The "
            "new tool runs that command through the safe shell boundary (values "
            "are data, never code). action='create' validates, security-scans, "
            "asks for approval, then persists + registers it LIVE (available this "
            "turn and on every reboot); 'delete' removes one; 'list' lists them. "
            "LANE: minting a reusable command-wrapping tool. ANTI-LANE: do NOT use "
            "it to run a one-off command (use the shell tool) or to author a "
            "procedure/how-to (use skill authoring)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_VALID_ACTIONS),
                    "description": "create | delete | list",
                },
                "name": {
                    "type": "string",
                    "description": "Tool name (^[a-z][a-z0-9_]*$). Required for create/delete.",
                },
                "description": {
                    "type": "string",
                    "description": "What the tool does (shown to the model). Required for create.",
                },
                "params": {
                    "type": "array",
                    "description": (
                        "Declared params. Each: {name, type (string|integer|number|"
                        "boolean), description, required}. Required for create."
                    ),
                    "items": {"type": "object"},
                },
                "argv_template": {
                    "type": "array",
                    "description": (
                        "The command as a LIST of argv tokens. argv[0] is a fixed "
                        "program name; each '{param}' must be a WHOLE token bound to "
                        "a declared param (use two tokens '--x' and '{p}', NOT "
                        "'--x={p}'). Required for create."
                    ),
                    "items": {"type": "string"},
                },
                "timeout_sec": {
                    "type": "number",
                    "description": "Optional per-call timeout (bounded by the shell ceiling).",
                },
                "action_severity": {
                    "type": "string",
                    "enum": ["read", "write", "consequential"],
                    "description": "Optional severity of the authored tool (default consequential).",
                },
            },
            "required": ["action"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            command_types=(CREATE, DELETE),
            commit_coupling="transactional",
            toolset_group=_TOOLSET_GROUP,
        )

    # ------------------------------------------------------------------ dispatch

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        action = str(kwargs.get("action", "")).strip().lower()
        # 1. ENTRY
        log.tool.info("tool_build.execute: entry", extra={"_fields": {"action": action}})
        if action not in _VALID_ACTIONS:
            return self._err(
                f"Unknown action {action!r}. Valid actions: {'|'.join(_VALID_ACTIONS)}.", t0
            )
        try:
            # 2. DECISION — dispatch by validated action.
            if action == "create":
                return await self._create(kwargs, t0)
            if action == "delete":
                return await self._delete(kwargs, t0)
            return self._list(t0)
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "tool_build.execute: action failed — degrading to structured error",
                exc_info=exc,
                extra={"_fields": {"action": action}},
            )
            return self._err(f"{type(exc).__name__}: {exc}", t0)

    # ------------------------------------------------------------------ create

    async def _create(self, kwargs: dict[str, object], t0: float) -> ToolResult:
        # 1. Parse + pydantic-validate the spec (structured error, no raise).
        spec_payload = {
            "name": str(kwargs.get("name", "")).strip(),
            "description": str(kwargs.get("description", "")),
            "params": kwargs.get("params") or [],
            "argv_template": kwargs.get("argv_template") or [],
        }
        if kwargs.get("timeout_sec") is not None:
            spec_payload["timeout_sec"] = kwargs.get("timeout_sec")
        if kwargs.get("action_severity") is not None:
            spec_payload["action_severity"] = kwargs.get("action_severity")
        try:
            spec = LearnedToolSpec.model_validate(spec_payload)
        except Exception as exc:  # B5 — structured block, never a raise
            return self._err(f"Invalid tool spec: {exc}", t0)

        # 2. Structured spec validation (whole-token argv, argv[0] literal, etc.).
        spec_err = validate_spec(spec)
        if spec_err is not None:
            return self._err(f"BLOCKED — invalid spec: {spec_err}", t0)

        name = spec.name
        # 3. Collision pre-check — never shadow an existing tool or spec file.
        registry = get_services().tool_registry
        if registry is not None and registry.get(name) is not None:
            return self._err(
                f"BLOCKED — name '{name}' is already in use by an existing tool. "
                "Pick a different name (built-in tools cannot be shadowed).",
                t0,
            )
        spec_path = StackowlHome.learned_tools_dir() / f"{name}.json"
        if spec_path.exists():
            return self._err(
                f"BLOCKED — a learned tool named '{name}' already exists. "
                "Delete it first (action='delete') before re-authoring.",
                t0,
            )

        # 4. HARD security scan (fail-closed on its own crash). Stage a synthetic
        # scannable doc embedding the description + argv + params, then gate it.
        blocked = self._scan_or_block(spec, name)
        if blocked is not None:
            return self._err(blocked, t0)

        # 5. Consent — consequential, fail-closed off-TTY.
        refusal = await self._consent_or_refuse(name)
        if refusal is not None:
            return self._err(refusal, t0)

        # 6/7. AD-1 — submit the declared command instead of writing the spec
        # file + registering it live directly. The handler
        # (tool_build_commands.py::_create_handler) does the persist +
        # register with rollback and writes the audit row itself.
        db = get_services().db_pool
        if db is None:
            return self._err("no database configured — cannot register a new tool.", t0)
        submission = await submit_command(db, CREATE, spec)
        if submission.outcome is None:
            return self._ok(
                f"Tool '{name}' is pending approval before it is registered.", t0,
                extra={"tool": name, "op": "create", "pending": True},
            )
        if not submission.outcome.success:
            return self._err(
                submission.outcome.error or f"BLOCKED — could not register tool '{name}'.", t0,
            )

        # 8. Success.
        return self._ok(
            f"Built and registered tool '{name}'. Available now and on every reboot.",
            t0,
            extra={"tool": name, "op": "create"},
        )

    # ------------------------------------------------------------------ delete

    async def _delete(self, kwargs: dict[str, object], t0: float) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        if not name:
            return self._err("action='delete' requires a tool 'name'.", t0)
        spec_path = StackowlHome.learned_tools_dir() / f"{name}.json"
        if not spec_path.exists():
            return self._err(f"No learned tool named '{name}' to delete.", t0)
        # AD-1 — submit the declared command instead of unlinking the spec
        # file + dropping the registry entry directly. The handler
        # (tool_build_commands.py::_delete_handler) does both with the same
        # provenance snapshot, and captures the undo payload (undo=create).
        db = get_services().db_pool
        if db is None:
            return self._err("no database configured — cannot delete a tool.", t0)
        submission = await submit_command(db, DELETE, DeleteToolPayload(name=name))
        if submission.outcome is None:
            return self._ok(
                f"Deletion of tool '{name}' is pending approval.", t0,
                extra={"tool": name, "op": "delete", "pending": True},
            )
        if not submission.outcome.success:
            return self._err(
                submission.outcome.error or f"Could not delete learned tool '{name}'.", t0,
            )
        return self._ok(f"Deleted learned tool '{name}'.", t0, extra={"tool": name, "op": "delete"})

    # ------------------------------------------------------------------ list

    def _list(self, t0: float) -> ToolResult:
        learned_dir = StackowlHome.learned_tools_dir()
        try:
            names = sorted(p.stem for p in learned_dir.glob("*.json"))
        except OSError as exc:
            return self._err(f"Could not list learned tools: {exc}", t0)
        if not names:
            return self._ok("No learned tools yet.", t0, extra={"op": "list", "count": 0})
        return self._ok(
            "Learned tools:\n" + "\n".join(f"- {n}" for n in names),
            t0,
            extra={"op": "list", "count": len(names)},
        )

    # ------------------------------------------------------------------ gates

    def _scan_or_block(self, spec: LearnedToolSpec, name: str) -> str | None:
        """Run the HARD security gate over a synthetic scannable doc. Fails closed.

        The doc embeds the description, argv template and param descriptions —
        exactly the agent-authored text that could carry an exfil/injection
        payload — so the SAME scanner that guards skills guards learned tools.
        """
        argv_line = " ".join(spec.argv_template)
        param_lines = "\n".join(f"- {p.name}: {p.description}" for p in spec.params)
        doc = (
            f"---\nname: {name}\ndescription: {spec.description}\n---\n\n"
            f"# Learned tool: {name}\n\n{spec.description}\n\n"
            f"## Command\n\n{argv_line}\n\n## Parameters\n\n{param_lines}\n"
        )
        staging_parent = tempfile.mkdtemp(prefix="stackowl-toolscan-")
        try:
            staged = Path(staging_parent) / name
            staged.mkdir(parents=True, exist_ok=True)
            (staged / "SKILL.md").write_text(doc, encoding="utf-8")
            ok, reason = security_scan_gate(staged)
            if not ok:
                log.tool.warning(
                    "tool_build.execute: security gate BLOCKED — nothing persisted",
                    extra={"_fields": {"tool": name}},
                )
                return (
                    "BLOCKED by security scan — nothing persisted/registered.\n"
                    f"{reason}"
                )
            return None
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)

    async def _consent_or_refuse(self, name: str) -> str | None:
        """Consequential consent. Returns a refusal string, or None to proceed.

        NO LONGER REFUSES FOR WANT OF A HUMAN (Bakir, 2026-08-16). This used to
        check ``interactive`` and refuse before consulting the consent policy at
        all — 28 times in the logs, every one of them an unattended run that had
        decided to build a tool and was stopped without anybody being asked. The
        refusal was silent to the user by construction: there was no prompt to
        answer, so it read as the agent simply failing.

        It also short-circuited the reasoning immediately below it, which already
        argues that creating a learned tool is REVERSIBLE (action='delete' removes
        the spec and unregisters it) and should auto-proceed with undo rather than
        prompt every time. That logic never got to run off-TTY.

        The decision now belongs to ConsentPolicy for every context: it honours the
        always-ask tools and categories first, and falls through to the autonomous
        grant when no human is attached.
        """
        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_key = ctx.get("session_key")
        if not channel or not session_key:
            # Not "no human" — no LANE at all, so there is nothing to scope a grant
            # or an audit record to. That is a wiring fault, not an autonomy case.
            log.tool.error(
                "tool_build.execute: no channel/session to scope consent — refused",
                extra={"_fields": {
                    "tool": name, "interactive": interactive,
                    "channel": channel, "has_session": bool(session_key),
                }},
            )
            return (
                f"refused: registering a new tool ('{name}') needs a conversation "
                "to attribute the approval to, and this turn has none."
            )
        gate = get_services().consent_gate
        if gate is None:
            log.tool.error(
                "tool_build.execute: no consent gate wired — refused (fail closed)",
                extra={"_fields": {"tool": name}},
            )
            return f"refused: no consent gate available to approve registering '{name}'."
        try:
            allowed = await gate.policy.request(
                tool_name=self.name,
                channel=channel,
                session_key=session_key,
                reply_target=ctx.get("reply_target"),
                category=_CONSENT_CATEGORY,
                summary=f"Register new tool {name}",
                # Graded self-authorization (Task 8): a learned tool has a genuine
                # undo — action='delete' removes the spec + unregisters it — so
                # CREATION is reversible and auto-proceeds WITH-UNDO instead of
                # prompting every time. Still fails closed for always-ask tools/
                # categories (the policy never relaxes those).
                reversible=True,
            )
        except Exception as exc:  # no-hidden-errors — fail closed
            log.tool.error(
                "tool_build.execute: consent gate raised — refused (fail closed)",
                exc_info=exc,
                extra={"_fields": {"tool": name}},
            )
            return f"refused: consent check failed while registering '{name}'."
        if not allowed:
            return f"declined by user — tool '{name}' was not registered."
        return None

    # AD-1 — the audit write (source='learned') now lives in
    # tool_build_commands.py's handlers, alongside the mutation itself; this
    # tool no longer writes to the audit sink directly.

    # ------------------------------------------------------------------ results

    @staticmethod
    def _ok(output: str, t0: float, *, extra: dict[str, object] | None = None) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "tool_build.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "tool_build.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
