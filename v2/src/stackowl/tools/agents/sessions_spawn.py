"""SessionsSpawnTool — create a named, persistent child owl session (E8-S3).

Spawns a named session in the DI :class:`stackowl.owls.session_registry.SessionRegistry`
(resolved off ``get_services().session_registry`` at execute time — the tool never
builds its own, so the cap / TTL / mailbox-drain rails stay a single source of
truth). The persona is resolved exactly like ``delegate_task`` (explicit ``owl`` →
role → a sensible default specialist via :func:`resolve_target_owl`). When an
``initial_task`` is supplied, it is run ONCE through a child
:class:`stackowl.pipeline.backends.asyncio_backend.AsyncioBackend` UNDER the shared
``delegation_governor`` (depth-aware, mirroring :class:`A2ADelegator`) with
``session_id=f"session:{label}"`` so the seed turn is persisted to the MemoryBridge
under the SESSION's id (by ``consolidate``); a later ``sessions_send`` (E8-S4)
reads it back via ``classify``. Continuity is the bridge's job — nothing is stored
on the handle.

THREE rails, all self-healing / no-hidden-errors:
* **Duplicate / capacity** — :meth:`SessionRegistry.spawn` raises a structured
  :class:`SessionRegistryError`; the tool surfaces it as a structured refusal
  (``status='refused'``), never a fake-success, never a raise.
* **Spawn / run failure** — any error is logged ERROR and returned as a structured
  ``status='error'`` record; the tool never raises.
* **Depth gate (S0)** — ``sessions_spawn`` is in ``_CHILD_EXCLUDED_TOOLS`` so a
  delegated child (delegation_depth>0) is refused it at the presentation AND
  execution layers; this tool needs no own depth check (defense lives upstream).

Reads :class:`stackowl.infra.trace.TraceContext` (trace/session/owl/depth) — never
``PipelineState`` directly. Severity ``write``; ``toolset_group`` ``agents``.
"""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.owls.delegation_limits import GOVERNOR_ACQUIRE_TIMEOUT_SECONDS
from stackowl.owls.session_registry import SessionRegistryError
from stackowl.pipeline.authz_compose import child_floor
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.tools.agents.resolver import resolve_target_owl
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_TOOLSET_GROUP = "agents"
_DEFAULT_CALLER = "secretary"

SESSIONS_SPAWN_DESCRIPTION = (
    "Create a named, persistent child owl session you can address later. Provide a "
    "short unique 'label' (the session's address), optionally an 'owl' persona (or "
    "leave blank for a sensible specialist), and an optional 'initial_task' to run "
    "once at spawn. Returns the session's label, owl, and status. Refuses (without "
    "crashing) if the label is already taken or too many sessions are live."
)

SESSIONS_SPAWN_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "Unique address for the new session."},
        "owl": {"type": "string", "description": "Persona/owl name for the session (optional)."},
        "initial_task": {"type": "string", "description": "A task to run once at spawn (optional)."},
    },
    "required": ["label"],
    "additionalProperties": False,
}


class SessionsSpawnArgs(BaseModel):
    """Validated arguments for one ``sessions_spawn`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    owl: str | None = None
    initial_task: str | None = None


class SessionsSpawnTool(Tool):
    """Spawn a named persistent owl session in the SessionRegistry."""

    @property
    def name(self) -> str:
        return "sessions_spawn"

    @property
    def description(self) -> str:
        return SESSIONS_SPAWN_DESCRIPTION

    @property
    def parameters(self) -> dict[str, object]:
        return SESSIONS_SPAWN_PARAMETERS

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute
    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "sessions_spawn.execute: entry",
            extra={"_fields": {"has_owl": "owl" in kwargs, "has_initial_task": "initial_task" in kwargs}},
        )
        try:
            args = SessionsSpawnArgs.model_validate(kwargs)
        except ValidationError as exc:
            log.tool.warning(
                "sessions_spawn.execute: validation failed",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return _failed(f"sessions_spawn: invalid arguments — {exc.errors()!r}", t0)

        ctx = TraceContext.get()
        trace_id = str(ctx.get("trace_id") or ctx.get("session_id") or "sessions-spawn")
        caller = str(ctx.get("owl_name") or _DEFAULT_CALLER)

        services = get_services()
        registry = services.session_registry
        if registry is None:
            log.tool.warning(
                "sessions_spawn.execute: no session_registry wired — degraded",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return _refused(t0, "unavailable", "sessions are not available in this environment.")

        # 2. DECISION — resolve the persona exactly like delegate_task.
        owl_name = resolve_target_owl(
            registry=services.owl_registry, to_owl=args.owl, role=None, caller=caller,
        )
        if owl_name is None:
            log.tool.warning(
                "sessions_spawn.execute: owl unresolved — refusing",
                extra={"_fields": {"trace_id": trace_id, "owl": args.owl}},
            )
            return _refused(
                t0, "unresolved_owl",
                f"could not resolve a persona for this session (owl={args.owl!r}).",
            )

        # 3. STEP — spawn (structured dup/cap refusal), then optional initial run.
        try:
            registry.spawn(args.label, owl_name)
        except SessionRegistryError as exc:
            log.tool.warning(
                "sessions_spawn.execute: registry refused spawn — structured",
                extra={"_fields": {"trace_id": trace_id, "label": args.label, "reason": exc.reason}},
            )
            return _refused(t0, exc.reason, exc.detail)
        except Exception as exc:  # B5 — spawn is contracted not to raise otherwise.
            log.tool.error(
                "sessions_spawn.execute: spawn raised — structured error",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "label": args.label}},
            )
            return _error(t0, args.label, owl_name, str(exc))

        status = "spawned"
        if args.initial_task:
            status = await self._run_initial_task(
                label=args.label,
                owl_name=owl_name,
                invoking_owl=caller,
                initial_task=args.initial_task,
                trace_id=trace_id,
                channel=str(ctx.get("channel") or "internal"),
            )

        record: dict[str, object] = {"label": args.label, "owl": owl_name, "status": status}
        # 4. EXIT
        return _ok(record, t0, note=f"session {args.label!r} spawned with owl {owl_name!r}")

    # ---------------------------------------------------------------- helpers
    async def _run_initial_task(
        self,
        *,
        label: str,
        owl_name: str,
        invoking_owl: str,
        initial_task: str,
        trace_id: str,
        channel: str,
    ) -> str:
        """Run ``initial_task`` once under the governor. Self-healing.

        The run uses ``session_id=f"session:{label}"`` so ``consolidate`` persists
        the seed turn to the MemoryBridge under the SESSION's id — that IS the
        continuity (a later ``sessions_send`` reads it back via ``classify``). The
        handle stores NO history. A run failure NEVER tears down the already-spawned
        session (it stays live, addressable later) — it is logged and surfaced as
        ``'spawned_initial_failed'`` so the model knows the seed run produced
        nothing rather than inventing a result.
        """
        # A2A sub-pipeline: no user channel binding (default-deny clarify), and
        # delegation_depth=1 so the child cannot itself spawn/delegate (S0 cap).
        # session_id is the SESSION's id (NOT the caller's) so the seed turn lands
        # in the bridge under this session — consolidate persists it (no depth skip).
        services = get_services()
        sub_state = PipelineState(
            trace_id=trace_id or "sessions-spawn",
            session_id=f"session:{label}",
            input_text=initial_task,
            channel=channel,
            owl_name=owl_name,
            pipeline_step="dispatch",
            interactive=False,
            delegation_depth=1,
            # E2-S2 delegation floor — clamp to parent EFFECTIVE bounds (owl ∩ ceiling)
            # — closes TOCTOU-delegation: a resumed parent whose owl was widened after
            # creation still clamps children to its persisted ceiling (FR35-runtime).
            # Back-compat: unstamped context ceiling (None) → owl bounds only (prior behavior).
            creation_ceiling=child_floor(
                invoking_owl, TraceContext.creation_ceiling(), services.owl_registry
            ),
        )
        backend = AsyncioBackend(services=services)
        try:
            final_state = await self._run_under_governor(backend, sub_state)
        except Exception as exc:  # B5 — never raise out of the tool.
            log.tool.error(
                "sessions_spawn._run_initial_task: initial run failed — session kept",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "label": label}},
            )
            return "spawned_initial_failed"

        if final_state.errors:
            log.tool.warning(
                "sessions_spawn._run_initial_task: initial run reported errors",
                extra={"_fields": {"trace_id": trace_id, "label": label,
                                   "errors": list(final_state.errors)}},
            )
            return "spawned_initial_failed"
        answer_len = sum(len(c.content) for c in final_state.responses)
        log.tool.debug(
            "sessions_spawn._run_initial_task: seed turn persisted to bridge",
            extra={"_fields": {"trace_id": trace_id, "label": label, "answer_len": answer_len}},
        )
        return "spawned_with_initial"

    @staticmethod
    async def _run_under_governor(backend: AsyncioBackend, sub_state: PipelineState) -> PipelineState:
        """Run the seed pipeline under the shared budget (mirrors A2ADelegator).

        Acquires a bounded governor slot before ``backend.run`` and releases it in
        ``finally`` (via the slot context manager) so a crash never leaks a permit.
        When no governor is wired (early-stage tests), run ungated + log a warning.
        """
        governor = get_services().delegation_governor
        if governor is None:
            log.tool.warning(
                "sessions_spawn._run_under_governor: no delegation_governor — running ungated",
                extra={"_fields": {"trace_id": sub_state.trace_id, "owl": sub_state.owl_name}},
            )
            return await backend.run(sub_state)
        async with governor.slot(timeout=GOVERNOR_ACQUIRE_TIMEOUT_SECONDS):
            return await backend.run(sub_state)


# ----------------------------------------------------------- result builders
def _ok(record: dict[str, object], t0: float, *, note: str) -> ToolResult:
    """Wrap a structured ``record`` into a success ToolResult (logs exit)."""
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "sessions_spawn.execute: exit",
        extra={"_fields": {"success": True, "status": record.get("status"), "duration_ms": duration_ms}},
    )
    payload = json.dumps({"note": note, "record": record}, ensure_ascii=False)
    return ToolResult(success=True, output=payload, duration_ms=duration_ms)


def _refused(t0: float, reason: str, detail: str) -> ToolResult:
    """A structured (success=True) refusal — a safety rail, not a crash."""
    return _ok({"status": "refused", "reason": reason, "detail": detail}, t0, note=detail)


def _error(t0: float, label: str, owl: str, detail: str) -> ToolResult:
    """A structured error record (success=True) — the model learns it failed."""
    return _ok(
        {"status": "error", "label": label, "owl": owl, "detail": detail},
        t0,
        note=f"sessions_spawn for {label!r} failed",
    )


def _failed(msg: str, t0: float) -> ToolResult:
    """A hard-failed ToolResult for invalid-argument cases (logs exit)."""
    duration_ms = (time.monotonic() - t0) * 1000
    log.tool.info(
        "sessions_spawn.execute: exit",
        extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
    )
    return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
