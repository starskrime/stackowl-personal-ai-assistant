"""owl_build — the self-extending owl-builder (Phase-2 A).

Mirrors :mod:`stackowl.tools.meta.tool_build` 1:1: a consequential, isolated meta-tool
the agent uses to mint/edit/retire a SPECIALIST OWL — a standing, named, reusable
persona. Like tool_build it is consent-gated (fail-closed off-TTY), validated through a
structured chokepoint, and depth-0 only (defense in depth — the execute step also
child-excludes it so a sub-agent can never recurse into owl creation).

The agent-facing :class:`OwlBuildSpec` carries NO authority fields; origin / created_by /
creation_ceiling / bounds are forced server-side in the ``create`` / ``edit`` /
``retire`` handlers. A ``create`` may also carry a ``schedule`` cadence, which makes
the owl a SCHEDULED persona (lifecycle="scheduled" + a CronTrigger) auto-provisioned
into a recurring job by the UniOwl reconcile loop.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.commands.config_helpers import config_path
from stackowl.commands.owls_command import OwlsCommand
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.interaction.clarify_gateway import CLARIFY_TTL_SECONDS, OUTCOME_ANSWERED
from stackowl.owls.registry import _SECRETARY_NAME
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.meta.owl_build_authz import build_agent_manifest, clamp_bounds
from stackowl.tools.meta.owl_build_existence import existing_near_match
from stackowl.tools.meta.owl_build_guards import (
    MAX_AGENT_OWLS,
    consent_summary,
    count_agent_owls,
    name_quality_error,
)
from stackowl.tools.meta.owl_build_infer import infer_capability, suggest_display_name
from stackowl.tools.meta.owl_build_spec import (
    MissingFields,
    OwlBuildSpec,
    validate_owl_build_spec,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.skills.manifest import SkillSource

# Isolated toolset group so a read-only / non-admin owl never gets owl-administration
# hydrated into its presented toolset.
_TOOLSET_GROUP = "owl_admin"
# A NON-dangerous, tool-declared consent category — the model cannot relax its gating.
_CONSENT_CATEGORY = "owl_build"
# Source name under which agent-minted owls register (so they survive/unregister cleanly).
_SOURCE_NAME = "agent_owls"
# Audit source — reuses the skills audit sink's "learned" lane for provenance (DRY with
# tool_build), so owl create/edit/retire is provenance-tracked the same way.
_AUDIT_SOURCE: SkillSource = "learned"
_ACTOR = "agent_self:owl_build"

_VALID_ACTIONS: tuple[str, ...] = ("create", "edit", "retire")

# One natural-language question per recoverable create field (ADR-A: the validator
# decides WHICH field is missing; this only PHRASES the ask). User-facing prose,
# not a classification keyword list — same register as the clarify tool's prompts.
_FIELD_QUESTIONS: dict[str, str] = {
    "name": "What should I name this owl?",
    "capability": (
        "What should this owl be able to do? Name a capability preset "
        "(e.g. 'researcher') or the specific tools it needs."
    ),
    "specialty": "In one sentence, what is this owl's standing role?",
    "schedule": (
        "How often should this owl run? Give a cadence like 'every 2h', 'every 30m', "
        "or 'daily@09:00' (minimum every 5 minutes)."
    ),
}
# The create required set is bounded (name, capability, specialty, and — for a
# scheduled owl — schedule), so the elicitation loop can never run longer than this:
# a hard bound against any re-validate cycle.
_MAX_ELICIT_ROUNDS = 4


def can_modify(manifest: object, *, caller: str, target_name: str) -> str | None:
    """no-edit-your-betters: only an ``origin='agent'`` owl YOU minted may be edited/retired.

    Returns a refusal string when the modification is forbidden, or ``None`` when it
    is allowed. Refuses if the target is the Secretary, is a human/builtin owl, or
    was created by a different owl. An edit can never launder authority through an
    owl it does not own.
    """
    if target_name.lower() == _SECRETARY_NAME:
        return "the secretary owl cannot be modified or retired."
    origin = getattr(manifest, "origin", None)
    if origin != "agent":
        return f"'{target_name}' is a {origin} owl and cannot be modified by owl_build."
    if getattr(manifest, "created_by", None) != caller:
        return f"'{target_name}' was created by another owl — you may only modify owls you created."
    return None


class OwlBuildTool(Tool):
    """Create / edit / retire a specialist owl (consent-gated, depth-0 only)."""

    def __init__(self, *, clarify_timeout_s: float = CLARIFY_TTL_SECONDS) -> None:
        """Store the mid-turn clarify park timeout (seconds) used to elicit any
        missing create fields. Defaults to the shared clarify TTL; tests override
        it with a tiny value to exercise the timeout (fail-closed) path."""
        self._clarify_timeout_s = clarify_timeout_s

    @property
    def name(self) -> str:
        return "owl_build"

    @property
    def description(self) -> str:
        return (
            "Create / edit / retire a persistent, NAMED AGENT — an owl persona that "
            "acts on the user's behalf, can run on a SCHEDULE, and can reach the user "
            "PROACTIVELY. This is what a user means by 'create an agent / assistant / "
            "bot that ...' (e.g. an agent that pokes me every 2 hours with AI news). "
            "action='create' mints a new agent — pass a 'schedule' cadence (e.g. "
            "'every 2h', 'daily@09:00') to make it recurring and proactive; 'edit' "
            "adjusts one; 'retire' removes one. RARE: for a one-off task just do the "
            "task, or delegate_task to an EXISTING owl — only mint an agent for a "
            "standing role the human will reuse. Consequential: requires human approval "
            "and fails closed with no interactive user present."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_VALID_ACTIONS),
                    "description": "create | edit | retire",
                },
                "name": {
                    "type": "string",
                    "description": "The owl's name. Required for every action.",
                },
                "preset": {
                    "type": "string",
                    "description": (
                        "A named capability preset for the owl (mutually exclusive with "
                        "explicit_tools). Use for create/edit."
                    ),
                },
                "explicit_tools": {
                    "type": "array",
                    "description": (
                        "An explicit list of tool names the owl may use (mutually "
                        "exclusive with preset). Use for create/edit."
                    ),
                    "items": {"type": "string"},
                },
                "specialty": {
                    "type": "string",
                    "description": (
                        "One sentence describing the owl's standing role. Required for create."
                    ),
                },
                "model_tier": {
                    "type": "string",
                    "description": "Optional model tier hint for the owl.",
                },
                "schedule": {
                    "type": "string",
                    "description": (
                        "Optional recurring cadence (create) — makes this a scheduled, "
                        "proactive agent. Platform format: 'every 2h', 'every 30m', "
                        "'daily@09:00', or a 5-field cron. Minimum interval is 5 minutes."
                    ),
                },
                "goal": {
                    "type": "string",
                    "description": (
                        "Optional instruction the scheduled owl runs each tick "
                        "(defaults to its specialty). Only meaningful with a schedule."
                    ),
                },
                "lifecycle": {
                    "type": "string",
                    "enum": ["on_demand", "scheduled"],
                    "description": (
                        "Optional. 'scheduled' marks a recurring agent; if you set it "
                        "without a 'schedule', you will be asked for the cadence."
                    ),
                },
            },
            "required": ["action", "name"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            commit_coupling="transactional",
            toolset_group=_TOOLSET_GROUP,
            effect_class="creates_persistent_entity",
        )

    # ------------------------------------------------------------------ dispatch

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "owl_build.execute: entry",
            extra={"_fields": {"action": kwargs.get("action"), "name": kwargs.get("name")}},
        )
        # 2. Parse + pydantic-validate the agent-facing spec (structured error, no raise).
        try:
            spec = OwlBuildSpec.model_validate(kwargs)
        except Exception as exc:  # B5 — structured block, never a raise
            log.tool.error(
                "owl_build.execute: malformed spec",
                exc_info=exc,
                extra={"_fields": {"args": list(kwargs.keys())}},
            )
            return self._err(f"invalid owl_build request: {exc}", t0)

        # 3. Structured spec validation. A HARD error (invalid value) refuses now;
        # a MissingFields result (recoverable, create only) is resolved by ASKING
        # the user below, after the depth gate.
        spec_check = validate_owl_build_spec(spec)
        if isinstance(spec_check, str):
            return self._err(spec_check, t0)

        # 4. Depth-0 only — also child-excluded at the execute-step dispatch; this is
        # defense in depth so a sub-agent can never recurse into owl creation.
        ctx = TraceContext.get()
        depth = int(ctx.get("delegation_depth", 0) or 0)
        if depth > 0:
            log.tool.error(
                "owl_build.execute: refused at depth>0",
                exc_info=None,
                extra={"_fields": {"depth": depth, "name": spec.name}},
            )
            return self._err(
                "owl_build is only available to the root owl (refused for sub-agents).", t0
            )

        # 4b. Resumable, validator-gated creation (ADR-A). The validator is the state
        # machine — it said which required fields are missing; ask the user for them
        # mid-turn via the ClarifyGateway, merge, re-validate, loop until complete,
        # then mint via the existing _create path. Fail-closed off-TTY (returns the
        # gap as an error; never hangs).
        if isinstance(spec_check, MissingFields):
            spec, gap_err = await self._elicit_missing(spec_check)
            if gap_err is not None:
                return self._err(gap_err, t0)

        # 5. DECISION — dispatch by validated action (handlers land in Tasks 9/10).
        try:
            if spec.action == "create":
                return await self._create(spec, t0)
            if spec.action == "edit":
                return await self._edit(spec, t0)
            return await self._retire(spec, t0)
        except NotImplementedError:
            return self._err(f"action '{spec.action}' is not yet implemented.", t0)
        except Exception as exc:  # B5 / self-healing — degrade, never raise.
            log.tool.error(
                "owl_build.execute: unhandled failure",
                exc_info=exc,
                extra={"_fields": {"action": spec.action, "name": spec.name}},
            )
            return self._err(f"owl_build failed: {exc}", t0)

    # --------------------------------------------------------- verification (TS2)

    async def verify(
        self, args: dict[str, object], result: ToolResult, *, started_at: float
    ) -> bool | None:
        """ADR-T2 / TS2 — MEASURE that a created owl truly landed, by RE-READING the
        world, never trusting the create's own ``success`` flag (this is where the
        "0 owls but ✅" lie dies). Runs at the ``__call__`` seam after a success the
        tool asserted; the create path stamps the owl name in ``artifact_path``, so
        edit/retire (no stamp) return ``None`` ⇒ byte-identical (out of TS2 scope).

        Three world-reads — ANY observed failure ⇒ ``False`` (``success`` is NOT
        mutated; the claim-vs-confirmation split is preserved):
          1. the owl is present in the LIVE/reachable registry,
          2. its persisted YAML entry exists + parses + carries the owl,
          3. if the persisted manifest is ``scheduled``, its projected job row exists.
        A read that genuinely cannot be performed (no registry / no db wired) ⇒
        ``None`` (no opinion — never flip a real success on an inability to look).
        """
        name = result.artifact_path
        if not name:
            return None  # not a create success — edit/retire are out of TS2 scope
        registry = get_services().owl_registry
        if registry is None:
            log.tool.warning(
                "owl_build.verify: no owl registry to observe — no opinion",
                extra={"_fields": {"owl": name}},
            )
            return None
        # READ 1 — present in the LIVE/reachable registry (not a stale list).
        try:
            manifest = registry.get(name)
        except Exception:  # OwlNotFoundError (or unreadable) — claimed, but absent
            log.tool.warning(
                "owl_build.verify: claimed created but owl NOT in live registry",
                extra={"_fields": {"owl": name}},
            )
            return False
        # READ 2 — the durable YAML record exists + parses + carries this owl.
        if not self._yaml_has_owl(name):
            log.tool.warning(
                "owl_build.verify: claimed created but owl absent from persisted yaml",
                extra={"_fields": {"owl": name}},
            )
            return False
        # READ 3 — a scheduled owl MUST have its projected job row (no-op on_demand).
        if getattr(manifest, "lifecycle", "on_demand") == "scheduled":
            job_present = await self._scheduled_job_exists(name)
            if job_present is None:
                return None  # scheduler/db unobservable — no opinion
            if not job_present:
                log.tool.warning(
                    "owl_build.verify: scheduled owl has NO projected job row",
                    extra={"_fields": {"owl": name}},
                )
                return False
        log.tool.debug(
            "owl_build.verify: all world-reads passed — owl observed",
            extra={"_fields": {"owl": name}},
        )
        return True

    @staticmethod
    def _yaml_has_owl(name: str) -> bool:
        """Read-back: does the persisted owls YAML carry an entry named ``name``?

        Reuses :func:`load_yaml` (total — returns ``{}`` on missing/invalid, never
        raises), so a missing file, an unparseable file, or an absent entry all read
        as ``False`` (the durable record is not trustworthily present)."""
        from stackowl.commands.config_helpers import load_yaml

        data = load_yaml(config_path())
        owls = data.get("owls")
        if not isinstance(owls, list):
            return False
        return any(isinstance(e, dict) and e.get("name") == name for e in owls)

    @staticmethod
    async def _scheduled_job_exists(name: str) -> bool | None:
        """Read-back for a scheduled owl: is its projected job row present?

        ``None`` when the scheduler/db cannot be observed (no opinion). Keys on the
        SAME deterministic id the projection writes (:func:`_job_id_for`) so the
        check can never drift from the writer."""
        db = get_services().db_pool
        if db is None:
            return None
        from stackowl.scheduler.owl_lifecycle import _job_id_for
        from stackowl.scheduler.scheduler import JobScheduler

        try:
            jobs = await JobScheduler(db=db).list_jobs()
        except Exception as exc:  # B5 — unobservable, never raise into the seam
            log.tool.warning(
                "owl_build.verify: job read-back failed — no opinion",
                exc_info=exc,
                extra={"_fields": {"owl": name}},
            )
            return None
        target = _job_id_for(name)
        return any(getattr(j, "job_id", None) == target for j in jobs)

    @staticmethod
    async def _scheduled_next_run(name: str) -> str | None:
        """Read the MEASURED next fire time from the owl's projected job row (TS9).

        Returns the row's ``next_run_at`` ISO timestamp so the create confirmation can
        PROVE the schedule with a concrete, user-verifiable instant instead of a bare
        checkmark — never a fabricated time. ``None`` when the scheduler/db is
        unobservable OR no projected row exists yet (the caller then says so honestly,
        rather than claim a schedule that is not really there). Keys on the SAME
        deterministic id the projection writes so it can never drift from the writer."""
        db = get_services().db_pool
        if db is None:
            return None
        from stackowl.scheduler.owl_lifecycle import _job_id_for
        from stackowl.scheduler.scheduler import JobScheduler

        try:
            jobs = await JobScheduler(db=db).list_jobs()
        except Exception as exc:  # B5 — unobservable, never raise into the success path
            log.tool.warning(
                "owl_build.execute: next-run read-back failed — confirming without a time",
                exc_info=exc,
                extra={"_fields": {"owl": name}},
            )
            return None
        target = _job_id_for(name)
        for job in jobs:
            if getattr(job, "job_id", None) == target:
                return job.next_run_at
        return None

    # ------------------------------------------------------------------ consent

    async def _consent_or_refuse(
        self, summary: str, name: str, *, reversible: bool = False
    ) -> str | None:
        """Consequential consent, fail-closed off-TTY. Returns a refusal or None.

        ``reversible`` (Task 8) marks the gated action as one with a genuine undo so
        the ONE ConsequentialActionGate auto-proceeds WITH-UNDO instead of prompting
        every time. Only ``create`` passes it: a created owl's HONEST undo is
        ``action='retire'`` (deregister → unreachable/can't act, remove durable yaml,
        reconcile deletes its owned scheduler job row). ``edit`` keeps the default
        (fail-closed) — a tool-WIDENING edit has no clean per-edit undo, so it is
        never auto-relaxed. Always-ask tools/categories are never relaxed either."""
        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_id = ctx.get("session_id")
        if not interactive or not channel or not session_id:
            log.tool.error(
                "owl_build.execute: no user present to approve — refused (fail closed)",
                exc_info=None,
                extra={"_fields": {"owl": name, "interactive": interactive}},
            )
            return (
                f"refused: building owl '{name}' needs your approval and no "
                "interactive user is present (fail closed)."
            )
        gate = get_services().consent_gate
        if gate is None:
            log.tool.error(
                "owl_build.execute: no consent gate wired — refused (fail closed)",
                exc_info=None,
                extra={"_fields": {"owl": name}},
            )
            return f"refused: no consent gate available to approve building owl '{name}'."
        try:
            allowed = await gate.policy.request(
                tool_name=self.name,
                channel=channel,
                session_id=session_id,
                category=_CONSENT_CATEGORY,
                summary=summary,
                reversible=reversible,
            )
        except Exception as exc:  # no-hidden-errors — fail closed
            log.tool.error(
                "owl_build.execute: consent gate raised — refused (fail closed)",
                exc_info=exc,
                extra={"_fields": {"owl": name}},
            )
            return f"refused: consent check failed while building owl '{name}'."
        if not allowed:
            return f"declined by user — owl '{name}' was not built."
        return None

    # ------------------------------------------------------- elicitation (ADR-A)

    async def _elicit_missing(
        self, missing: MissingFields,
    ) -> tuple[OwlBuildSpec, str | None]:
        """Ask the user for the validator-reported missing create fields, mid-turn.

        Carries ``missing.partial`` as the ONLY session state through each
        ClarifyGateway resume: ask one field → merge the answer → re-validate (the
        validator decides what is still missing) → loop until the schema is
        satisfied. Returns ``(completed_spec, None)`` on success, or
        ``(partial, error)`` when it cannot complete — off-TTY (fail-closed, never
        hangs), no gateway, gateway failure, or a timeout/pivot (never assume an
        answer). The caller refuses on a non-None error.
        """
        # 1. ENTRY
        log.tool.info(
            "owl_build.execute: eliciting missing create fields",
            extra={"_fields": {"name": missing.partial.name, "missing": list(missing.fields)}},
        )

        # 1b. S5 — goal→type inference (fail-open). Fill capability/specialty from the
        # goal and pre-suggest a name BEFORE asking, so the user is never asked "what
        # type?" and the questions shrink to ~the name. If the LLM is unavailable or
        # low-confidence this is a no-op and the loop below simply ASKS (the S4 path).
        spec, name_suggestion = await self._infer_from_goal(missing.partial)
        check0 = validate_owl_build_spec(spec)
        if check0 is None:
            # Inference closed every gap — mint with ZERO questions asked.
            log.tool.info(
                "owl_build.execute: create spec completed via inference (no questions asked)",
                extra={"_fields": {"name": spec.name}},
            )
            return spec, None
        if isinstance(check0, str):  # inference surfaced a hard (unfixable) error
            return missing.partial, check0
        missing = check0  # narrowed to whatever inference could not fill (e.g. name)

        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_id = ctx.get("session_id")

        # 2. DECISION — fail closed off-TTY (no user to ask). Return the gap as an
        # error so the turn ends cleanly instead of hanging (current behavior).
        if not interactive or not channel or not session_id:
            log.tool.info(
                "owl_build.execute: underspecified create off-TTY — fail closed (no ask)",
                extra={"_fields": {"missing": list(missing.fields), "interactive": interactive}},
            )
            return spec, self._gap_message(missing.fields)

        gateway = get_services().clarify_gateway
        if gateway is None:
            log.tool.error(
                "owl_build.execute: no clarify gateway — cannot ask for missing fields",
                exc_info=None,
                extra={"_fields": {"missing": list(missing.fields)}},
            )
            return spec, self._gap_message(missing.fields)

        # Bounded by the fixed create required set — terminates regardless of input.
        for _ in range(_MAX_ELICIT_ROUNDS):
            check = validate_owl_build_spec(spec)
            if not isinstance(check, MissingFields):
                break  # complete (None) or a freshly surfaced hard error (str)
            field = check.fields[0]
            question = _FIELD_QUESTIONS.get(field, f"Please provide the owl's {field}.")
            if field == "name" and name_suggestion:
                question += f" (I suggest '{name_suggestion}' — reply with it or another name.)"
            try:
                # 3. STEP — blocking ask + park until the user replies (or times out).
                clarify_id = await gateway.ask(
                    str(session_id), str(channel), question,
                    awaiting_text=True, blocking=True,
                )
                answer, outcome = await gateway.wait_for_answer(
                    clarify_id, timeout=self._clarify_timeout_s,
                )
            except Exception as exc:  # self-healing — never raise out of the tool
                log.tool.error(
                    "owl_build.execute: clarify ask/wait failed — fail closed",
                    exc_info=exc,
                    extra={"_fields": {"field": field}},
                )
                return missing.partial, self._gap_message(missing.fields)
            if outcome != OUTCOME_ANSWERED or not answer or not answer.strip():
                log.tool.info(
                    "owl_build.execute: clarify not answered — aborting create (no assume)",
                    extra={"_fields": {"field": field, "outcome": outcome}},
                )
                return missing.partial, (
                    "owl creation set aside — still need: "
                    + ", ".join(check.fields) + "."
                )
            spec = self._merge_answer(spec, field, answer.strip())

        final = validate_owl_build_spec(spec)
        if isinstance(final, MissingFields):
            return missing.partial, self._gap_message(final.fields)
        if isinstance(final, str):
            return missing.partial, final
        # 4. EXIT — fully specified; the caller mints via the existing _create path.
        log.tool.info(
            "owl_build.execute: create spec completed via clarify",
            extra={"_fields": {"name": spec.name}},
        )
        return spec, None

    @staticmethod
    async def _infer_from_goal(spec: OwlBuildSpec) -> tuple[OwlBuildSpec, str | None]:
        """S5 pre-pass — fill missing capability/specialty from the goal and pre-suggest
        a name, via a fast-tier LLM. The goal source is the spec's ``specialty`` (the
        role sentence the agent passes through). Fail-open: on any miss the spec is
        returned unchanged with no suggestion, and the caller falls back to ASKING."""
        goal = (spec.specialty or "").strip()
        if not goal:
            return spec, None
        # capability — only when it is the gap; keep the user's specialty if present.
        if not spec.preset and not spec.explicit_tools:
            inferred = await infer_capability(goal)
            if inferred is not None:
                preset, refined = inferred
                update: dict[str, object] = {"preset": preset}
                if not (spec.specialty and spec.specialty.strip()):
                    update["specialty"] = refined
                spec = spec.model_copy(update=update)
                log.tool.info(
                    "owl_build.execute: capability inferred from goal",
                    extra={"_fields": {"name": spec.name, "preset": preset}},
                )
        # name — pre-suggest only when it is missing (the question seeds the suggestion).
        name_suggestion: str | None = None
        if not spec.name or not spec.name.strip():
            name_suggestion = await suggest_display_name(goal)
        return spec, name_suggestion

    @staticmethod
    def _merge_answer(spec: OwlBuildSpec, field: str, answer: str) -> OwlBuildSpec:
        """Merge one clarify answer into the partial spec (frozen → model_copy).

        The "capability" field maps to ``preset`` (a named capability the builder
        resolves); ``name``/``specialty`` map to their like-named spec fields."""
        key = "preset" if field == "capability" else field
        return spec.model_copy(update={key: answer})

    @staticmethod
    def _gap_message(fields: tuple[str, ...]) -> str:
        """A concise off-TTY/abort refusal naming the still-missing required fields."""
        return (
            "cannot create the owl yet — still missing: "
            + ", ".join(fields)
            + " (no interactive user to ask)."
        )

    # ------------------------------------------------------------------ actions

    async def _create(self, spec: OwlBuildSpec, t0: float) -> ToolResult:
        """Mint a NEW specialist owl. Security order: collision/name-quality (before
        forge) → soft-cap (HARD gate before consent) → existence-redirect → forge →
        consent → persist+register with rollback. Nothing persists before consent."""
        svc = get_services()
        registry = svc.owl_registry
        if registry is None:
            log.tool.error(
                "owl_build.execute: no owl registry wired — cannot create",
                exc_info=None,
                extra={"_fields": {"name": spec.name}},
            )
            return self._err("owl registry unavailable — cannot create an owl.", t0)

        ctx = TraceContext.get()
        creator = str(ctx.get("owl_name") or _SECRETARY_NAME)

        # 1. Collision / reserved — never shadow Secretary or an existing owl.
        if spec.name.strip().lower() == _SECRETARY_NAME or self._exists(registry, spec.name):
            return self._err(
                f"an owl named '{spec.name}' already exists (or is reserved).", t0
            )

        # 2. Name quality (structural, language-neutral) — before any forge.
        nq = name_quality_error(spec.name, registry)
        if nq is not None:
            return self._err(nq, t0)

        # 3. Soft cap — a HARD gate BEFORE consent (the human shouldn't be asked to
        #    approve an owl we'd refuse anyway).
        current = count_agent_owls(registry)
        if current >= MAX_AGENT_OWLS:
            return self._err(
                f"you already have {current} agent-created owls (cap {MAX_AGENT_OWLS}) — "
                "retire one (action='retire') or delegate_task to an existing owl instead.",
                t0,
            )

        # 4. Existence redirect — a near-identical owl is a delegation opportunity.
        match = await existing_near_match(spec, registry, svc)
        if match is not None:
            return self._err(
                f"an existing owl '{match}' already covers this — delegate_task to it "
                "instead of minting a near-duplicate.",
                t0,
            )

        # 5. Forge — authority forced server-side (origin/created_by/creation_ceiling).
        manifest, dropped = build_agent_manifest(
            spec,
            creator=creator,
            parent_ceiling=TraceContext.creation_ceiling(),
            registry=registry,
        )

        # 6. Consent — the real clamp. Surface tools, drops and the existing roster.
        resolved_tools = (
            (manifest.bounds.tools or frozenset()) if manifest.bounds else frozenset()
        )
        summary = consent_summary(
            name=manifest.name,
            role=manifest.role,
            resolved_tools=resolved_tools,
            dropped=dropped,
            roster=tuple(m.name for m in registry.all() if m.origin == "agent"),
            why=spec.specialty or "",
        )
        # Reversible=True: a created owl's honest undo is action='retire' (see
        # _consent_or_refuse) — so creation auto-proceeds WITH-UNDO for a normal
        # (non-always-ask) owl instead of prompting every time.
        refusal = await self._consent_or_refuse(summary, manifest.name, reversible=True)
        if refusal is not None:
            return self._err(refusal, t0)

        # 7. Persist with rollback. Snapshot the yaml first so a failed register can
        #    restore the exact prior bytes (10k-DB-safe: never leave a half state).
        snapshot = self._yaml_snapshot()
        try:
            OwlsCommand()._upsert_to_yaml(manifest_to_yaml_entry(manifest))  # noqa: SLF001
        except Exception as exc:  # B5 — no-hidden-errors
            log.tool.error(
                "owl_build.execute: persist failed — nothing registered",
                exc_info=exc,
                extra={"_fields": {"owl": manifest.name}},
            )
            self._yaml_restore(snapshot)
            return self._err(f"failed to persist owl '{manifest.name}': {exc}", t0)

        await self._audit("create", manifest.name, creator)

        # 8. Register LIVE — on failure restore the yaml snapshot (atomic rollback).
        try:
            registry.register(manifest, source_name=_SOURCE_NAME)
        except Exception as exc:  # B5 — roll back the persisted yaml
            log.tool.error(
                "owl_build.execute: live registration failed — rolling back yaml",
                exc_info=exc,
                extra={"_fields": {"owl": manifest.name}},
            )
            self._yaml_restore(snapshot)
            await self._audit("delete", manifest.name, creator)
            return self._err(
                f"failed to register owl '{manifest.name}' ({exc}) — rolled back.", t0
            )

        # 9. Capture authored DNA baseline (fail-safe — won't break creation).
        if svc.db_pool is not None:
            from stackowl.owls.dna_authored import capture_one_authored

            await capture_one_authored(svc.db_pool, manifest.name, manifest.dna)

        # 10. Reconcile the scheduler projection (ADR-B) — a scheduled owl gets its
        #     owned job now, without a reboot. No-op for an on-demand owl.
        await self._reconcile_schedules()

        # 11. Success. A SCHEDULED owl PROVES itself (TS9): the confirmation shows the
        #     MEASURED next fire time read from the projected job row + an honest "it
        #     reaches you on its own" line + a one-line off-ramp — never a bare ✅.
        tools_str = ", ".join(sorted(resolved_tools)) or "(none)"
        if manifest.lifecycle == "scheduled":
            msg = await self._scheduled_success_message(manifest, tools_str, dropped)
        else:
            msg = (
                f"Created owl '{manifest.name}' ({manifest.role}). Tools: {tools_str}."
            )
            if dropped:
                msg += f" Dropped above your authority: {', '.join(sorted(dropped))}."
            msg += " Delegate to it with delegate_task."
        # Stamp the created owl's NAME as the artifact locator so verify() (TS2) can
        # re-read the world for exactly this owl. Only create sets it → verify() is a
        # no-op for edit/retire (out of TS2 scope).
        return self._ok(
            msg, t0, extra={"owl": manifest.name, "op": "create"},
            artifact_path=manifest.name,
        )

    async def _scheduled_success_message(
        self, manifest: object, tools_str: str, dropped: frozenset[str] | set[str],
    ) -> str:
        """Build the TS9 trustworthy-confirmation for a scheduled owl — prove, don't claim.

        Reads the REAL next fire time from the projected job row (never fabricated). If
        that row is genuinely absent/unobservable, it says so honestly instead of
        promising a schedule that may not exist. Always states it will reach the user
        proactively on its own + names the one-line off-ramp to pause it."""
        name = getattr(manifest, "name", "")
        display = getattr(manifest, "display", None) or name
        role = getattr(manifest, "role", "")
        next_run = await self._scheduled_next_run(name)
        tail = f" Tools: {tools_str}."
        if dropped:
            tail += f" Dropped above your authority: {', '.join(sorted(dropped))}."
        if next_run is None:
            # Honest gap — do NOT claim a fire time we could not read back.
            log.tool.warning(
                "owl_build.execute: scheduled owl created but no job row observed — honest confirmation",
                extra={"_fields": {"owl": name}},
            )
            return (
                f"Created {display} ({role}), but I could NOT confirm its schedule yet — "
                f"no projected job row is readable, so I can't promise when it will first "
                f"run. Check it with the schedule/health surface before relying on it." + tail
            )
        return (
            f"Created {display} ({role}) — it runs on its own and will reach you "
            f"proactively (durably, via your messaging channel). Next run: {next_run}." + tail
            + f" To pause it, say 'stop {display}' anytime — that pauses the pokes "
            "without deleting the owl, and 'resume " + display + "' starts them again."
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    async def _reconcile_schedules() -> None:
        """Re-project owl schedules after a create/edit/retire (ADR-B / S9+S10).

        Manifest = truth; the scheduler rows are reconciled, never imperatively
        poked — so a retired/edited owl's owned job is torn down/updated in the SAME
        operation (no reboot, no orphaned cron). Fail-safe: a reconcile error is
        logged but never fails the build (the manifest mutation already committed).
        """
        svc = get_services()
        db = svc.db_pool
        registry = svc.owl_registry
        if db is None or registry is None:
            log.tool.debug(
                "owl_build.execute: no db/registry for schedule reconcile — skipped"
            )
            return
        try:
            from stackowl.scheduler.owl_lifecycle import reconcile_owl_schedules

            settings = svc.settings
            tz = settings.system.timezone if settings is not None else "UTC"
            await reconcile_owl_schedules(registry, db, tz=tz or "UTC", settings=settings)
        except Exception as exc:  # B5 — never fail the build on a reconcile hiccup
            log.tool.error(
                "owl_build.execute: schedule reconcile failed — owl change persisted",
                exc_info=exc,
                extra={"_fields": {}},
            )

    @staticmethod
    def _exists(registry: object, name: str) -> bool:
        """True if an owl named ``name`` is already registered (case-sensitive get)."""
        getter = getattr(registry, "get", None)
        if getter is None:
            return False
        try:
            getter(name)
            return True
        except Exception:  # OwlNotFoundError — the not-found path is expected
            return False

    @staticmethod
    def _yaml_snapshot() -> bytes | None:
        """Read the owls yaml file's raw bytes (the same file ``_upsert_to_yaml``
        writes), or None if it is absent. Logs + returns None on read error."""
        path = config_path()
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            log.tool.error(
                "owl_build.execute: yaml snapshot read failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
            return None

    @staticmethod
    def _yaml_restore(snapshot: bytes | None) -> None:
        """Restore the owls yaml to ``snapshot`` (or unlink if it had not existed)."""
        path = config_path()
        try:
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(snapshot)
        except OSError as exc:
            log.tool.error(
                "owl_build.execute: yaml rollback failed — manual cleanup may be needed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )

    async def _audit(self, op: str, name: str, actor: str) -> None:
        """Append a provenance audit row via the skills audit sink (best-effort).

        Mirrors :meth:`tool_build._audit` (source='learned'); a missing store
        degrades to a log line — the yaml persist already succeeded. Never raises."""
        store = get_services().skill_store
        if store is None:
            log.tool.info(
                "owl_build.execute: no skill store — audit skipped (owl still persisted)",
                extra={"_fields": {"owl": name, "op": op}},
            )
            return
        try:
            await store.audit_write(
                skill_name=name,
                source=_AUDIT_SOURCE,
                op=op,
                actor=actor,
                details={"kind": "agent_owl", "created_by": actor},
            )
        except Exception as exc:  # B5 — never fail the build on an audit hiccup
            log.tool.warning(
                "owl_build.execute: audit_write failed — owl persisted, audit pending",
                exc_info=exc,
                extra={"_fields": {"owl": name, "op": op}},
            )

    async def _edit(self, spec: OwlBuildSpec, t0: float) -> ToolResult:
        """Edit an agent-minted owl YOU created. Security order: no-edit-your-betters
        → re-forge (clamps to CURRENT floor) → MONOTONE re-clamp against the owl's
        ORIGINAL creation_ceiling (an edit cannot widen past the mint clamp) →
        re-consent only when the edit ADDS a tool → persist+register with rollback."""
        svc = get_services()
        registry = svc.owl_registry
        if registry is None:
            log.tool.error(
                "owl_build.execute: no owl registry wired — cannot edit",
                exc_info=None,
                extra={"_fields": {"name": spec.name}},
            )
            return self._err("owl registry unavailable — cannot edit an owl.", t0)

        ctx = TraceContext.get()
        creator = str(ctx.get("owl_name") or _SECRETARY_NAME)

        # 1. Load the current owl (OwlNotFoundError → not present).
        try:
            current = registry.get(spec.name)
        except Exception:  # OwlNotFoundError — the not-found path is expected
            return self._err(f"no owl named '{spec.name}' to edit.", t0)

        # 2. no-edit-your-betters — only an agent owl YOU minted.
        guard = can_modify(current, caller=creator, target_name=spec.name)
        if guard is not None:
            return self._err(guard, t0)

        # 3. Re-forge — clamps to the creator's CURRENT floor (authority forced server-side).
        rebuilt, dropped = build_agent_manifest(
            spec,
            creator=creator,
            parent_ceiling=TraceContext.creation_ceiling(),
            registry=registry,
        )

        # 4. MONOTONE RATCHET — re-clamp against the owl's ORIGINAL creation_ceiling so an
        #    edit can never widen authority past what was approved at mint time. Keep the
        #    original ceiling on the manifest (the ratchet point never moves outward).
        #    Defense-in-depth: an agent owl with NO recorded ceiling is corrupt/unsafe
        #    (boot revalidator deny-alls these; _create always stamps one) — refuse loudly
        #    rather than fall through to the floor-only clamp (a no-reboot escalation window).
        if current.creation_ceiling is None:
            log.tool.error(
                "owl_build._edit: agent owl missing creation_ceiling — refusing edit (fail closed)",
                exc_info=None,
                extra={"_fields": {"owl": spec.name}},
            )
            return self._err(
                f"owl '{spec.name}' has no recorded creation ceiling (corrupt/unsafe) — "
                "retire and recreate it instead of editing.",
                t0,
            )
        clamped, more = clamp_bounds(
            rebuilt.bounds or current.creation_ceiling, current.creation_ceiling
        )
        rebuilt = rebuilt.model_copy(
            update={
                "bounds": clamped,
                "tools": sorted(clamped.tools or frozenset()),
                "creation_ceiling": current.creation_ceiling,
            }
        )
        dropped = dropped | more

        # 5. Re-consent ONLY on widening — a bounds-narrowing-only edit skips consent.
        old_tools = (current.bounds.tools or frozenset()) if current.bounds else frozenset()
        new_tools = (rebuilt.bounds.tools or frozenset()) if rebuilt.bounds else frozenset()
        widening = new_tools - old_tools
        if widening:
            summary = consent_summary(
                name=rebuilt.name,
                role=rebuilt.role,
                resolved_tools=new_tools,
                dropped=dropped,
                roster=tuple(m.name for m in registry.all() if m.origin == "agent"),
                why=f"edit adds: {sorted(widening)}",
            )
            refusal = await self._consent_or_refuse(summary, rebuilt.name)
            if refusal is not None:
                return self._err(refusal, t0)

        # 6. Persist + register with snapshot rollback (atomic — never a half state).
        snapshot = self._yaml_snapshot()
        try:
            OwlsCommand()._upsert_to_yaml(manifest_to_yaml_entry(rebuilt))  # noqa: SLF001
            registry.replace(rebuilt)
        except Exception as exc:  # B5 — no-hidden-errors, roll back the yaml
            log.tool.error(
                "owl_build.execute: edit persist/register failed — rolling back yaml",
                exc_info=exc,
                extra={"_fields": {"owl": rebuilt.name}},
            )
            self._yaml_restore(snapshot)
            return self._err(
                f"failed to edit owl '{rebuilt.name}' ({exc}) — rolled back.", t0
            )

        await self._audit("edit", rebuilt.name, creator)

        # Reconcile the scheduler projection — a changed lifecycle/trigger updates
        # the owned job in place (no duplicate), an on_demand edit tears it down.
        await self._reconcile_schedules()

        tools_str = ", ".join(sorted(new_tools)) or "(none)"
        msg = f"Updated owl '{rebuilt.name}'. Tools: {tools_str}."
        if dropped:
            msg += f" Dropped above your authority: {', '.join(sorted(dropped))}."
        return self._ok(msg, t0, extra={"owl": rebuilt.name, "op": "edit"})

    async def _retire(self, spec: OwlBuildSpec, t0: float) -> ToolResult:
        """Retire an agent-minted owl YOU created: no-edit-your-betters → deregister +
        remove from yaml with snapshot rollback (atomic — never a half state)."""
        svc = get_services()
        registry = svc.owl_registry
        if registry is None:
            log.tool.error(
                "owl_build.execute: no owl registry wired — cannot retire",
                exc_info=None,
                extra={"_fields": {"name": spec.name}},
            )
            return self._err("owl registry unavailable — cannot retire an owl.", t0)

        ctx = TraceContext.get()
        creator = str(ctx.get("owl_name") or _SECRETARY_NAME)

        # 1. Load the current owl (OwlNotFoundError → not present).
        try:
            current = registry.get(spec.name)
        except Exception:  # OwlNotFoundError — the not-found path is expected
            return self._err(f"no owl named '{spec.name}' to retire.", t0)

        # 2. no-edit-your-betters — only an agent owl YOU minted.
        guard = can_modify(current, caller=creator, target_name=spec.name)
        if guard is not None:
            return self._err(guard, t0)

        # 3. Remove from yaml (DURABLE) FIRST, then deregister (in-memory), with snapshot
        #    rollback. Durable store leads: if the yaml remove fails nothing changed in
        #    memory → clean error. If deregister fails after a successful yaml remove, the
        #    next boot simply won't re-register it (consistent — the durable store already
        #    dropped it), never a yaml-present/registry-absent zombie that resurrects.
        snapshot = self._yaml_snapshot()
        try:
            OwlsCommand()._remove_from_yaml(spec.name)  # noqa: SLF001  # durable first
            registry.deregister(spec.name)
        except Exception as exc:  # B5 — no-hidden-errors, roll back the yaml
            log.tool.error(
                "owl_build.execute: retire failed — rolling back yaml",
                exc_info=exc,
                extra={"_fields": {"owl": spec.name}},
            )
            self._yaml_restore(snapshot)
            return self._err(
                f"failed to retire owl '{spec.name}' ({exc}) — rolled back.", t0
            )

        await self._audit("retire", spec.name, creator)

        # S10 — TRANSACTIONAL teardown: the retired owl's owned scheduler row is
        # deleted in the SAME operation (reconcile sees the owl is gone). A retired
        # owl with a live job is the exact failure this prevents.
        await self._reconcile_schedules()

        return self._ok(f"Retired owl '{spec.name}'.", t0, extra={"owl": spec.name, "op": "retire"})

    # ------------------------------------------------------------------ results

    @staticmethod
    def _ok(
        output: str,
        t0: float,
        *,
        extra: dict[str, object] | None = None,
        artifact_path: str | None = None,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "owl_build.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(
            success=True, output=output, duration_ms=duration_ms, artifact_path=artifact_path,
        )

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "owl_build.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
