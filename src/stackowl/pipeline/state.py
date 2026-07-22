"""PipelineState — immutable pipeline execution state with evolve()."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from stackowl.authz.bounds import BoundsSpec
from stackowl.objectives.model import ExpectedOutcome
from stackowl.pipeline.acceptance import HttpProbeOutcome
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.base import Message

# Intent classes that NEVER enter the tool loop: a tool-free reply (conversational)
# or a single clarifying question (clarify). Shared by provider_select (skip the
# tool-capability gate), execute (plain-stream vs tool-loop branch), and assemble
# (skip skills/tool-protocol description). classify.py no longer gates its
# lessons/graph-context/skill-relevance blocks on this (owner decision
# 2026-07-22) — those now run for every intent class.
TOOL_FREE_CLASSES: frozenset[str] = frozenset({"conversational", "clarify"})


class ToolCall(BaseModel, frozen=True):
    """A record of a single tool invocation within the pipeline."""

    tool_name: str
    args: dict[str, Any]
    result: str | None
    error: str | None
    duration_ms: float


class StepError(BaseModel, frozen=True):
    """A STRUCTURED per-step failure record (REACT-7 / F092).

    Carried alongside the human-readable ``errors`` string so honesty surfaces
    (critical_failure) read typed fields instead of re-parsing a free-form string
    whose format could drift between the writer and the reader.
    """

    step: str
    exc_type: str
    message: str


class PipelineState(BaseModel, frozen=True):
    """Immutable snapshot of pipeline execution state.

    Mutation is via evolve(**kwargs) — returns a new instance.
    """

    trace_id: str
    session_id: str
    input_text: str
    channel: str
    owl_name: str
    pipeline_step: str
    #: Stable cross-channel identity for durable-knowledge scoping (preferences,
    #: extracted facts). Resolved at the gateway from the channel handle via the
    #: IdentityResolver. Empty ⇒ consumers fall back to session_id (per-channel),
    #: i.e. unconfigured behavior is byte-identical.
    identity_key: str = ""
    # A human-readable routing-correction notice from the scanner (e.g. a fuzzy
    # "@Maxx → @max" owl correction, or "owl not registered → @secretary"). The
    # gateway sets this from RouteDecision.suggestion so the pre-delivery command
    # hint surfacer can additively show it — otherwise the field is dead. None on
    # every turn the scanner inferred no correction (byte-identical default).
    route_suggestion: str | None = None
    # Coarse turn classification stamped by the triage step via SecretaryRouter.
    # Fail-safe default "standard" = byte-identical to pre-classification behavior.
    # "conversational" marks trivial greetings/small-talk (no task) so downstream
    # steps can choose a lean path and skip heavy prompt assembly.
    intent_class: Literal["conversational", "standard", "clarify"] = "standard"
    #: True only once the SecretaryRouter has POSITIVELY classified this turn
    #: (work/standard/conversational/clarify). Stays False on the direct-address
    #: path (triage returns before the router runs) and on any router error —
    #: so failure-history admission can fail CLOSED rather than treating the
    #: untouched ``intent_class="standard"`` default as a confirmed work turn.
    intent_classified: bool = False
    # The ONE clarifying question to surface when intent_class == "clarify"
    # (router-authored, same fast-tier call). None for every other class.
    clarify_question: str | None = None
    # Context-window size (tokens) of the resolved model for this turn, probed by
    # the assemble step. None = unknown / probe failed. When set and at or below
    # LEAN_WINDOW_THRESHOLD the assemble step selects the lean charter and DNA.
    model_window: int | None = None
    # Coarse language tag of the user's turn (F089/F098), stamped by triage via a
    # stdlib script detector. Drives the deterministic honest floor's localization
    # when providers are down and the LLM cascade can't re-derive the language.
    # Default "en" matches synthesize_floor's own default → byte-identical to today.
    language: str = "en"
    # True when a user is present on the originating channel and can answer a
    # mid-turn clarify question. FAIL-CLOSED: defaults to False — a human is
    # assumed ABSENT unless a user-facing channel (CLI/Telegram/etc.) EXPLICITLY
    # sets interactive=True for a real user turn. cron/scheduler, parliament, and
    # A2A sub-pipelines ride this False default so a clarify call default-denies
    # (returns its ABORT sentinel) instead of parking a coroutine with no one to
    # answer it. A forgotten flag therefore degrades safely to "clarify
    # unavailable" rather than faking a human presence.
    interactive: bool = False
    # Per-turn delivery target for fan-out channels (e.g. a Telegram chat_id),
    # threaded from IngressMessage.chat_id by the orchestrator at construction.
    # The deliver step stamps it onto every outgoing ResponseChunk so a turn's
    # output routes back to ITS OWN chat under concurrency — never the shared
    # _last_chat_id (overwritten by every newer inbound update). CLI turns leave
    # it None; the adapter then resolves the destination itself. Carried across
    # evolve() like every other field; default None keeps every non-Telegram turn
    # byte-for-byte unchanged.
    # String targets are for Slack (channel id / thread_ts); int for Telegram chat_id.
    reply_target: int | str | None = None
    # When True, the deliver step performs NO send for this turn — a non-interactive
    # producer (e.g. a scheduler handler) owns delivery via the durable seam.
    # Prevents double-send. Default False = unchanged behavior.
    defer_delivery: bool = False
    # True when this turn IS RetryActuator's own replay of an existing retry_queue
    # row (retry_actuator.py stamps this on construction). Prevents persist_turn's
    # floored-turn handling from calling retry_queue_store.insert_pending() AGAIN —
    # that call has no dedup, so every floored replay was minting a brand-new
    # attempt_count=0 row instead of feeding back into the row RetryActuator is
    # already tracking via mark_attempt_failed(), losing that row's attempt
    # history and compounding into duplicate, ever-multiplying retry rows.
    # Default False = every non-replay turn (live user, scheduled job) unchanged.
    retry_replay: bool = False
    # True when this turn IS a delivery-gate corrective re-run (one bounded
    # in-turn "your draft was rejected — fix it" replay spawned by
    # RetryActuator.run_corrective). The gates read this to never correct a
    # correction (a rejected corrective replay floors normally — no recursion).
    # Default False = every normal turn unchanged.
    corrective_replay: bool = False
    # LS4 — set True by the ``feedback`` step when it captured a reaction to the
    # last render into the durable ``output_style`` preference and stamped a
    # plain-language confirmation onto ``responses``. ``execute`` reads this and
    # SKIPS the tool loop (the confirmation IS the turn's reply); ``deliver`` then
    # enforces the freshly-written style on that confirmation. Default False =
    # byte-identical to every non-feedback turn.
    feedback_handled: bool = False
    # LAT.3 — the in-flight ``FeedbackClassifier.classify(...)`` + verdict-application
    # task, started (non-blocking) by the ``feedback`` step via ``asyncio.create_task``
    # and carried across the ``feedback`` -> ``execute`` step boundary so the classify
    # LLM round-trip runs CONCURRENTLY with execute's own answer-prep instead of
    # blocking in front of it. ``execute`` joins this task at the last safe point
    # before it would generate/stream the first user-visible chunk (so a confident
    # reaction still short-circuits correctly) and clears it back to None once
    # consumed. None on every turn feedback.run() had nothing to classify (default =
    # byte-identical). Typed ``Any`` (not ``asyncio.Task``) — pydantic cannot generate
    # a schema for a live Task object; this field is never validated, only carried.
    feedback_classify_task: Any = None
    # Recursion depth of this (sub-)pipeline in the delegation tree. 0 for a
    # top-level user turn; incremented by one each time A2ADelegator spawns a
    # specialist child (see _run_specialist). Carried across evolve() like every
    # other field. The child-toolset exclusion gates on depth>0 (PRIMARY
    # fork-bomb cap), and the S1 delegate_task tool refuses past
    # MAX_DELEGATION_DEPTH (defense-in-depth).
    delegation_depth: int = 0
    # Owl-name ancestry of the current delegation (governor-stamped, model-untouchable).
    # Powers cycle detection (refuse if a target is already in the chain). len() == delegation_depth.
    delegation_chain: tuple[str, ...] = ()
    # Phase 0 (coding-capability build plan) — "interactive" (default; a human is
    # watching each delegation level) vs "autonomous" (an unattended run, e.g. an
    # ObjectiveDriverHandler-driven epic subgoal). Carried across evolve() like
    # every other field, so a delegated child of an autonomous run stays
    # autonomous. delegate_task reads the PROJECTED TraceContext value (not this
    # field directly — tools never see PipelineState) to resolve the effective
    # depth/width cap via owls.delegation_limits.depth_cap/width_cap. Default
    # "interactive" is byte-identical to every existing turn.
    delegation_profile: Literal["interactive", "autonomous"] = "interactive"
    # ID of the durable task this pipeline turn belongs to, or None for an
    # ephemeral (non-durable) turn. Carried across evolve() like every other
    # field. Consumed by the langgraph backend to isolate per-task checkpoints
    # (thread_id = "session::task_id") so a durable task's resume replays its own
    # checkpoint, not a sibling turn's. Additive — default None preserves the
    # exact prior behavior for every non-durable turn.
    task_id: str | None = None
    # E2-S2/S3 — the task-scoped authorization fields.
    # ENFORCEMENT formula: effective = owl.bounds(now) ∩ creation_ceiling
    #
    # creation_ceiling — a snapshot of the owl's bounds taken at DURABLE task
    # creation, persisted on the task row. It narrows nothing on a normal run
    # (owl ∩ owl = owl); its sole effect is on RESUME after the owl's bounds were
    # widened mid-task, where owl.bounds(now) ∩ creation_ceiling clamps to the
    # narrower historical set (resume-monotonicity / TOCTOU ratchet). None for a
    # non-durable turn — no clamp. A missing ceiling is therefore NEVER
    # global-unrestricted, because owl.bounds(now) always remains a factor.
    creation_ceiling: BoundsSpec | None = None
    # task_envelope — the least-privilege-per-task slot. NOT enforced (E2-S3).
    # Drives presentation restrict_to and drift telemetry only. ALWAYS None in S2;
    # the E2-S3 preflight planner fills it with a goal-derived (tighter) spec.
    # Carried here now so S3 populates an existing field rather than re-threading.
    task_envelope: BoundsSpec | None = None
    # B2 durable-react — additive carriers for the durable activation in the
    # execute step. ALL default None so a non-durable turn (task_id is None) is
    # byte-for-byte unchanged. `durable_owner_id` is the owning principal whose
    # ledger/store rows this drive writes (falls back to DEFAULT_PRINCIPAL_ID when
    # None). The `durable_resume_*` trio is populated later by the B4 checkpoint
    # reconstruction and forwarded verbatim into complete_with_tools; in B2 they
    # are merely carried across evolve() like every other field.
    durable_owner_id: str | None = None
    durable_resume_messages: list[dict[str, Any]] | None = None
    durable_resume_tool_calls: list[dict[str, Any]] | None = None
    durable_resume_iteration: int | None = None
    # B2 durable-react — PARK signal. Set True when a durable drive hit a
    # DurableReplayUncertain (an `intent` ledger row without a matching commit:
    # a prior attempt may have half-run a side effect, so the guard refuses to
    # re-run it). This is a STRUCTURED park signal distinct from a transient
    # failure: the B3 router reads `durable_parked` to decide park-vs-retry,
    # rather than string-matching state.errors. Additive — default False keeps
    # every non-durable turn (and every durable turn that did not park)
    # byte-for-byte unchanged.
    durable_parked: bool = False
    # ID of an in-flight clarify question awaiting a user answer for this run.
    # The Event itself lives in the (out-of-band) clarify registry — a frozen
    # model cannot hold an asyncio.Event — so only the id is carried in state.
    pending_clarify_id: str | None = None
    responses: tuple[ResponseChunk, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    # SP-2 — the consolidate merge/trust decision, carried forward for persist_turn
    # (F088). Stamped by consolidate.run at the merge site (where responses is still
    # empty) from the FILTERED tool content, and read by the post-floor persist to
    # set trust="untrusted" — NEVER recomputed from post-floor responses (which the
    # honest floor may have replaced), so trust cannot be laundered. Default False =
    # byte-identical to a clean (non-tool-merged) turn.
    merged_external: bool = False
    # Verification B3 — an OPTIONAL declared, deterministically-observable
    # post-condition for this turn (the goal-level half of the verification
    # primitive). On the objectives path the driver threads the sub-goal's
    # acceptance_criteria here; on a normal user turn it is None (the flag-OFF
    # LLM-derived acceptance layer is the only future populator). None ⇒ the
    # AcceptanceChecker no-ops — byte-identical to pre-acceptance behavior. May also
    # carry the general network re-probe kind (HttpProbeOutcome, F-12).
    expected_outcome: ExpectedOutcome | HttpProbeOutcome | None = None
    memory_context: str | None = None
    # Query embedding computed once in classify (semantic only), forwarded so assemble
    # can score owned skills without re-embedding. None = no usable relevance signal. Story B.
    query_embedding: tuple[float, ...] | None = None
    # Real prior conversation turns (user/assistant), oldest-first. Populated by
    # the classify step from staged conversation rows and threaded into the
    # provider messages array by execute. Empty for the first turn / non-chat
    # pipelines. RC-C fix.
    history: tuple[Message, ...] = ()
    # Final assembled system prompt (owl persona + DNA directives + memory
    # blocks). Built by the assemble step; consumed by execute. None until
    # assemble runs. RC-B fix.
    system_prompt: str | None = None
    errors: tuple[str, ...] = ()
    # REACT-7/F092 — STRUCTURED per-step failure records, written in lockstep with
    # `errors` via stackowl.pipeline.step_error.format_step_error. The critical-failure
    # honesty surface reads these typed fields (PRIMARY); the string parser is the
    # back-compat fallback. Default () = byte-identical to a clean turn.
    step_errors: tuple[StepError, ...] = ()
    # REACT-7/F099 — consequential give-up SNAPSHOT, stamped onto immutable state at
    # the end of execute (while the turn-scoped tool_outcome_ledger / recovery_context
    # ContextVars are still bound). The honest giveup floor reads this snapshot when
    # present so its decision travels with the state, not an implicit bind() lifetime.
    # Names of consequential/write tools that FAILED, that SUCCEEDED, and the failed
    # names that were BRIDGED by a successful substitution this turn. Empty tuples =
    # no snapshot taken → the floor falls back to reading the live ledger (today's path).
    consequential_failures: tuple[str, ...] = ()
    consequential_successes: tuple[str, ...] = ()
    recovered_consequential: tuple[str, ...] = ()
    # Parallel to consequential_failures (same filter, same order) — each failed
    # tool's own ToolResult.error text (None when absent). Lets the honest floor
    # cite the REAL technical detail instead of a blank slot. Empty = byte-
    # identical to before this field existed.
    consequential_failure_errors: tuple[str | None, ...] = ()
    # ADR-6 Task 6 fix — the FAILED tool names bridged SPECIFICALLY by a
    # substitution (never "retry") this turn, stamped by the SAME snapshot while
    # recovery_context is still bound. Deliberately narrower than
    # ``recovered_consequential`` (which also includes "retry"-kind bridges,
    # not a masking pattern) — outcome capture reads THIS field, never the
    # ContextVar directly, since by the time _capture_outcome runs the backend's
    # finally has already reset() it (get_recovery() would silently return ()).
    recovered_via_substitution: tuple[str, ...] = ()
    # True once execute has stamped the snapshot above. Set explicitly so a CLEAN
    # turn (execute recorded zero consequential activity → all three tuples empty)
    # is still trusted as a snapshot rather than falling back to the live ledger.
    # The floor uses ``has_consequential_snapshot`` (this flag OR any non-empty
    # snapshot tuple) so honesty data that rides on state is never silently ignored
    # just because the flag was not threaded through.
    consequential_snapshot_taken: bool = False
    # P0/budget-cap GOAL-RELEVANT ACCOUNTING. ``consequential_successes`` above counts
    # EVERY effectful (write+consequential) success — fine for a clean model-chosen stop
    # and for the nudge veto. But a turn cut off by the BUDGET CAP mid-work is untrusted:
    # an incidental local-workspace FILE mutation (write_file / edit / apply_patch /
    # undo_write) is NOT the user's outcome — it never crossed the boundary OUT.
    # ``delivered_successes`` is the goal-relevant / user-delivered subset: every effectful
    # success EXCEPT those local file mutations (so consequential sends AND boundary-
    # crossing dispatches like delegate_task / sessions_* DO count as delivered).
    # ``budget_capped`` marks a turn terminated by the budget governor. The honest give-up
    # floor uses delivered-only accounting ONLY when ``budget_capped`` is True; a clean stop
    # and the nudge veto are byte-identical (they keep reading ``consequential_successes``).
    # Empty/False = no change to today's paths.
    delivered_successes: tuple[str, ...] = ()
    budget_capped: bool = False
    # ADR-T2 / TS3 — MEASURED overclaim veto input. Names of tools that declared a
    # durable ``effect_class`` (creates_persistent_entity / sends_message / schedules)
    # whose result this turn was NOT verified==True. DEFAULT-DENY: a ``verified`` of
    # False OR None (unknown), or a plain failure, all land a tool's name here — the
    # burden is on PROOF, and ``unknown`` is NOT success. Stamped by execute's
    # consequential snapshot (rides immutable state, since the live tool_outcome_ledger
    # ContextVar may be unbound by the time the overclaim gate runs). The gate floors an
    # affirmative non-floor draft when this is non-empty — keyed on the LEDGER (effect
    # presence), never on the claim prose. Empty () = byte-identical: no effect-classed
    # tool ran, or every one returned a verified receipt.
    unverified_effects: tuple[str, ...] = ()
    # PBC — overclaim trigger 3 (retrieval-intent). Stamped lazily by
    # surface_overclaim_gate's async wrapper (never inside the pure predicate)
    # via RetrievalIntentClassifier.requires_lookup when a clean, non-delivering,
    # non-conversational turn used no retrieval tool. Default False = byte-
    # identical for every un-classified/legacy turn — never floors on its own.
    requires_retrieval: bool = False
    # Turn-progress supervisor (TPS). ``turn_made_progress`` defaults True so any
    # non-tool path is byte-identical (never floored as no-progress). execute stamps
    # False + ``no_progress_tools`` when the tracker saw no PROGRESS dispatch.
    # INDEPENDENT of the consequential ledger.
    turn_made_progress: bool = True
    no_progress_tools: tuple[str, ...] = ()
    # Overclaim delivery-gate (Task 6). Stamped True by surface_overclaim_gate
    # when it replaces a confident non-floor draft with the honest floor because
    # nothing was delivered while a tool failed/bounced. Default False = byte-identical
    # on every normal turn; persisted to task_outcomes.overclaim_blocked.
    overclaim_blocked: bool = False
    # Overclaim trigger 4 (SCHEDULING-COMMITMENT) inputs. ``ran_effect_classes`` is
    # every ``effect_class`` any tool declared this turn (regardless of verified —
    # unlike ``unverified_effects``, this asks "did a schedules-tool run AT ALL",
    # not "did it prove itself"), stamped alongside the other snapshot tuples by
    # execute's ``_snapshot_consequential``. ``requires_scheduling_commit`` is the
    # lazy classifier stamp (mirrors ``requires_retrieval``): True when the draft's
    # own text commits to doing something for the user LATER on a schedule (ping/
    # remind/check-in/notify) — the no-tool-call sibling of the retrieval-intent
    # trigger. Both default to byte-identical no-op values.
    ran_effect_classes: tuple[str, ...] = ()
    requires_scheduling_commit: bool = False
    # Per-pipeline-step elapsed time in milliseconds, keyed by step name.
    # Populated by the backend's step loop; consumed by the outcome-capture
    # helper at end-of-run. Frozen tuple-of-tuples to keep PipelineState
    # immutable (pydantic frozen=True forbids mutable dicts).
    step_durations: tuple[tuple[str, float], ...] = ()
    # Task 7 — manual "do it again" retry path. Set True by the triage step when
    # it detected a session's pending retry_queue row and RetryIntentClassifier
    # confirmed the incoming message is asking to retry it; triage then already
    # dispatched RetryActuator.attempt_retry itself and short-circuits the rest
    # of this pipeline run. Default False = byte-identical to every turn with no
    # pending retry row / not a retry-intent message.
    retry_dispatched: bool = False

    @property
    def has_consequential_snapshot(self) -> bool:
        """REACT-7/F099 — True when the consequential give-up snapshot rides on state.

        The snapshot is present if execute stamped the flag (covers a clean turn where
        every snapshot tuple is empty) OR any snapshot tuple carries data — including the
        budget-cap ``delivered_successes`` subset — so honesty data that travels on state
        is honored even if the flag was not threaded. When False the floor falls back to
        the live ledger (the original, byte-identical path).
        """
        return (
            self.consequential_snapshot_taken
            or bool(self.consequential_failures)
            or bool(self.consequential_successes)
            or bool(self.delivered_successes)
            or bool(self.recovered_consequential)
        )

    def evolve(self, **kwargs: Any) -> PipelineState:
        """Return a new PipelineState with the given fields updated."""
        return self.model_copy(update=kwargs)
