"""Tool ABC and ToolResult — base contract for all pipeline tools (ARCH-94)."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.infra.resilience import looks_like_dead_handle
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.plugins import hooks
from stackowl.tools.verification import is_trustworthy_success


def _acceptance_authority_enabled() -> bool:
    """Read the ADR-1 ``acceptance_authority`` flag. Fail-safe to ``False`` (the
    byte-identical default) on any config error — the seam must never break a tool
    call by failing to read a flag. Consulted ONLY when a tool declares a
    post-condition, so the ~92 un-migrated tools never construct Settings here."""
    try:
        from stackowl.config.settings import cached_settings

        return bool(cached_settings().acceptance_authority)
    except Exception as exc:  # noqa: BLE001 — flag read must never raise into a turn
        log.tool.debug(
            "tool.__call__: could not read acceptance_authority flag — treating OFF",
            extra={"_fields": {"err": type(exc).__name__}},
        )
        return False


class ToolResult(BaseModel):
    """The output of a single tool execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    output: str
    error: str | None = None
    # OPTIONAL since D16.1: a tool author should not have to time their own call.
    # Found by being the first real user of the plugin surface (2026-08-16) — this
    # field sat REQUIRED between two optional ones, so the obvious
    # ``ToolResult(success=True, output=...)`` raised a ValidationError and the
    # author was asked for a number they had not measured.
    #
    # The default is 0.0 ONLY because ``Tool.__call__`` stamps its own measurement
    # over it (see the stamp below). A bare default would feed zeros into latency
    # metrics and the cost tracker — silently wrong data, which is worse than the
    # loud error it replaced. The platform already times every call; asking the
    # author too is a second writer to a fact it owns.
    duration_ms: float = 0.0
    #: D03.4 — WAS THIS RESULT CUT, AND BY HOW MUCH.
    #: Measured 2026-08-29: 22% of tool results exceed 10k chars and the largest
    #: seen was 4,201,658 — four times the whole context window. Truncation was
    #: happening already, but SILENTLY: nothing on ToolResult said so and no log
    #: line recorded it, so neither the agent nor the operator could tell
    #: information had been destroyed. A cap without this flag makes that worse.
    truncated: bool = False
    #: The size the output had BEFORE the cap. "Some was cut" is not actionable;
    #: "49,000 of 50,000 characters were cut" is.
    original_output_len: int | None = None
    #: D03.4 level 2 — where the FULL output was written when it was cut. None
    #: when nothing was cut, or when the spill itself failed (losing the spill
    #: must never lose the answer). The path is also placed in ``output`` so the
    #: model is told about it — a file nobody is told about is a write with no
    #: reader.
    spill_path: str | None = None
    # VERIFICATION (the reality check, distinct from `success` the self-report).
    # None  ⇒ not checked — falls back to `success` (byte-identical to pre-
    #         verification behavior; the default for the ~92 un-migrated tools).
    # True  ⇒ the claimed effect was OBSERVED in reality (a fresh, non-empty,
    #         right-shaped artifact).
    # False ⇒ the tool claimed success but reality disagreed (absent/empty/stale
    #         artifact). `success` is NOT mutated — the claim-vs-confirmation
    #         distinction is preserved. The single derived predicate
    #         tools.verification.is_trustworthy_success collapses the two for every
    #         downstream decider (floor, judge, learning, turn-success).
    verified: bool | None = None
    # Structured locator for the artifact this call claims to have produced, set by
    # the tool itself (its OWN trusted path), so verify() reads a real value instead
    # of re-parsing free `output` text. None when the call produces no file.
    artifact_path: str | None = None
    # Did this call cross the side-effect boundary? Default True (conservative: an
    # undeclared failure is assumed to have touched the world, so the honest give-up
    # floor still fires). A tool sets this False on a PRE-EXECUTION refusal — bad/
    # missing args, an unavailable store — where its effectful body provably never
    # ran. The give-up floor counts a failed write/consequential outcome ONLY when
    # the boundary was (or may have been) crossed, so a validation-refused no-op no
    # longer masquerades as a failed consequential action. See
    # tool_outcome_ledger.is_effectful_failure and pipeline/giveup_floor.py.
    side_effect_committed: bool = True


def _spill_dir() -> Path:
    """Where a cut result's full text is kept.

    NOT the sandbox scratch, though D03.4's reference design puts it there and
    that is what was chosen. Measured 2026-08-29: browser_extract — which produced
    ALL 26 results above 100k characters — has no sandbox reference, and
    ~/.stackowl/sandbox holds only `seccomp` because BwrapScratch creates a
    scratch per run and removes it after. The reference runs its tools inside its
    sandbox; this platform runs them in the core process and sandboxes only code
    execution, so there is no sandbox in scope when the overflowing tools run.

    This keeps the reference's LIFETIME — ephemeral, under the sandbox root,
    reaped rather than accumulated — while being reachable by the tools that
    actually overflow.
    """
    return StackowlHome.home() / "sandbox" / "tool_results"


def _spill(tool_name: str, text: str) -> str | None:
    """Write the full result and return its path, or None if that failed.

    Never raises. A full disk or an unwritable path must degrade to "capped but
    not kept", never to a failed tool call — losing the spill must not lose the
    answer as well.
    """
    try:
        trace = str((TraceContext.get() or {}).get("trace_id") or "no-trace")
    except Exception:  # pragma: no cover — trace context is best-effort here
        trace = "no-trace"
    try:
        target = _spill_dir() / trace
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{tool_name}-{uuid.uuid4().hex[:8]}.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)
    except Exception as exc:  # B5 — every except logs
        log.tool.warning(
            "tool.__call__: could not spill the oversized result — it is capped "
            "but NOT kept",
            exc_info=exc,
            extra={"_fields": {"tool": tool_name, "chars": len(text)}},
        )
        return None


#: How much room the truncation NOTICE itself needs. The notice is appended after
#: the cut, so the cap is applied to the body and the notice sits outside it —
#: otherwise a small cap would leave no room to say anything at all.
_TRUNCATION_NOTICE_BUDGET = 512


def _apply_result_cap(tool: Tool, result: ToolResult) -> ToolResult:
    """Cut an oversized result to the tool's declared cap and SAY SO.

    D03.4 level 3. Returns ``result`` unchanged when the tool declares no cap or
    the output fits, so every existing tool is byte-identical.

    THE SIGNAL IS THE POINT, not the cutting. Twelve tools already truncate
    (level 1) and did it silently — nothing on ToolResult said so and no log line
    recorded it, so a page cut to a tenth read to the model exactly like a whole
    page. The flag is for code; the notice appended to ``output`` is for the model,
    which reads the text and not the fields.

    Never raises: a cap must never turn a working tool call into a failure.
    """
    try:
        cap = tool.manifest.max_result_size_chars
    except Exception as exc:  # B5 — an unreadable manifest must not break the call
        log.tool.warning(
            "tool.__call__: could not read max_result_size_chars — leaving result "
            "uncapped",
            exc_info=exc,
            extra={"_fields": {"tool": tool.name}},
        )
        return result
    if not cap or cap <= 0:
        return result
    original = len(result.output)
    if original <= cap:
        return result
    spill_path = _spill(tool.name, result.output)
    where = (
        f" Full result saved to {spill_path} — read it if you need the rest."
        if spill_path else
        " The cut text could NOT be saved and is gone."
    )
    notice = (
        f"\n\n[truncated: {original:,} characters produced, {cap:,} kept — "
        f"{original - cap:,} characters were cut.{where}]"
    )
    # INFO, not debug: this is the evidence that information was destroyed, and a
    # DEBUG line could never close a claim about how often it happens.
    log.tool.info(
        "tool.__call__: result exceeded the tool's cap — truncated",
        extra={"_fields": {"tool": tool.name, "original_len": original,
                           "cap": cap, "cut": original - cap}},
    )
    return result.model_copy(update={
        "output": result.output[:cap] + notice,
        "truncated": True,
        "original_output_len": original,
        "spill_path": spill_path,
    })


class ToolManifest(BaseModel):
    """Declarative metadata for a tool — used by ConsequentialActionGate and MCP adapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    parameters: dict[str, object]
    action_severity: Literal["read", "write", "consequential"] = "read"
    # Trusted, tool-declared consent category (e.g. "lock", "alarm", "destructive").
    # The consent gate keys always-ask exclusions off THIS, never off LLM-supplied
    # call args — the model must not be able to relax its own gating (E0-S1 / B2).
    consent_category: str | None = None
    #: D03.4 level 3 — the largest result this tool may return, in characters.
    #: None (the default) means uncapped, so every existing tool is byte-identical.
    #: Enforced ONCE in Tool.__call__, which the module documents as the tool
    #: chokepoint — twelve tools already self-truncate (level 1) and this must not
    #: become a thirteenth copy of that rule.
    max_result_size_chars: int | None = None
    # Toolset-group name for DNA-gated presentation (e.g. "code", "media", "home").
    # An owl's capability_profile lists group names; a tool joins the presented set
    # when its toolset_group is in that profile (ADR-11 / E1-S4). Distinct from
    # consent_category (which is about consent, not grouping).
    toolset_group: str | None = None
    # Capability tag groups tools that produce the same KIND of result, enabling
    # self-healing substitution: when a tool in a capability class fails, the
    # supervisor can route to a sibling with the same tag (W3 substitution actuator).
    capability_tag: str | None = None
    # ESC-9 — cold-start ordering weight for BUDGETED presentation. Higher is kept
    # first when the token budget cannot fit every eligible tool.
    #
    # It exists because the tiebreak underneath was the alphabet: discretionary
    # tools rank by (-usage_score, name), and with no usage history every score is
    # 0.0, so a browser-profiled owl lost snapshot/type/upload/wait_for/tab_*/
    # vision purely for sorting late. It could see nothing and type nothing.
    #
    # DECLARATIVE ON PURPOSE, not query-derived: D05.2 removed relevance ranking
    # because it made the presented array a function of the question, changing every
    # turn and defeating the position-0 prompt-cache marker. This value is identical
    # on every turn for every query, so ordering stays stable by construction.
    # Measured usage still outranks it — this is a default, not an override.
    presentation_priority: int = 0
    # D05.3 — the SUBSYSTEM this tool cannot work without, by name (e.g.
    # "browser"). Resolved lazily through infra/capabilities.py to one of ADR-6's
    # HealableResource implementations; when that resource reports unavailable,
    # the tool is not presented, tool_search still lists it WITH the reason, and
    # dispatch refuses with the reason rather than letting the tool's own code
    # fail deep.
    #
    # NOT capability_tag (above), despite the similar name: that groups tools by
    # the KIND of result they produce so a failed tool can be substituted by a
    # sibling. This names a PREREQUISITE. A tool can have both.
    #
    # None ⇒ ungated, which is also what an unknown name resolves to. Fail OPEN
    # is deliberate: a typo here must present the tool, never silently delete a
    # whole toolset.
    requires_capability: str | None = None
    # Live-progress vocabulary key (e.g. "SEARCH_WEB", "READ_FILES"). Maps this
    # tool to a friendly, localized "what I'm doing now" status line shown to the
    # user while a turn runs (pipeline/progress/vocabulary.py). None ⇒ the generic
    # localized "Working on it…" fallback — a missing key NEVER leaks the raw tool
    # name to a customer. Keyed on a stable semantic enum, not user language.
    progress_key: str | None = None
    # D1 §6 — how tightly the tool's REAL-WORLD effect is coupled to our local
    # ledger commit. Decides definite-answer-vs-honest_uncertain after a durable
    # child times out / is recovered:
    #   "transactional"     — effect + ledger entry are atomic (L ⟺ E). "Committed
    #                         → done" is honest (e.g. a write to our own SQLite).
    #   "idempotent_keyed"  — effect is replay-safe under a key we own AND the
    #                         downstream contractually honors it (L ⟹ E).
    #   "unconfirmed"       — effect crosses a lossy-ack boundary (SMTP/POST/remote
    #                         FS/Telegram); L and E can diverge irreducibly.
    # None ⇒ undeclared. The resolver (delegate_task) treats undeclared write/
    # consequential tools as "unconfirmed" (fail-safe — never silently "safe").
    commit_coupling: Literal[
        "transactional", "idempotent_keyed", "unconfirmed"
    ] | None = None
    # ADR-T2 / TS1 — the KIND of durable world-effect this tool produces, so the
    # honesty layer (overclaim gate, TS3) can demand a MEASURED `verified==True`
    # before a success of that class is allowed into the answer:
    #   "creates_persistent_entity" — mints a standing record (an owl, a skill).
    #   "sends_message"             — emits an outbound message to the user.
    #   "schedules"                 — installs a recurring/future job.
    # None ⇒ read-only / no durable effect (the default — existing tools unchanged).
    effect_class: Literal[
        "creates_persistent_entity", "sends_message", "schedules"
    ] | None = None


def _derived_from_manifest(field: str) -> property:
    """A read-only property that reads ``field`` off this tool's own manifest."""

    def _get(self: Tool) -> Any:
        return getattr(self.manifest, field)

    _get.__name__ = field
    _get.__doc__ = f"Derived from this tool's manifest.{field}."
    return property(_get)


class Tool(ABC):
    """Abstract base for all tools available to the pipeline (ARCH-94).

    execute() may raise — __call__ catches and wraps into a failed ToolResult.

    DECLARE YOURSELF ONCE. A tool may either spell out ``name`` / ``description`` /
    ``parameters``, as every built-in does, or define ``manifest`` alone and have
    the three derived from it. Both were not possible until D16.1: implementing
    ``manifest`` alone is the obvious guess — ``ToolManifest`` exists and every
    tool has one — and it failed with "Can't instantiate abstract class ... without
    an implementation for abstract methods 'description', 'name', 'parameters'".
    That was the first thing the first plugin author wrote and the first thing that
    broke (measured 2026-08-16, driving a real plugin through the real loader).
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Fill the three fields from ``manifest`` when a subclass declares only it.

        ABSTRACT SLOTS ONLY, and that is the whole safety of it: a subclass that
        inherits a real ``name`` from a concrete parent keeps it, so overriding a
        manifest to change one thing can never silently rewrite the parent's name.
        A class that declares NEITHER stays abstract and still fails loudly at
        instantiation, naming exactly what is missing — the same early, legible
        error as before, rather than a recursion between two defaults.
        """
        super().__init_subclass__(**kwargs)
        if "manifest" not in cls.__dict__:
            return
        for derivable in ("name", "description", "parameters"):
            current = getattr(cls, derivable, None)
            if getattr(current, "__isabstractmethod__", False):
                setattr(cls, derivable, _derived_from_manifest(derivable))

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, object]:
        """JSON Schema describing the tool's parameters."""
        ...

    @property
    def manifest(self) -> ToolManifest:
        """Return a ToolManifest built from this tool's declared metadata.

        Subclasses may override to set a non-default action_severity.
        """
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def consent_summary(self, **call_args: object) -> str | None:
        """Build a TRUSTED, bounded one-line summary of THIS call for the consent
        prompt, or ``None`` to fall back to the static :attr:`description`.

        The consent gate shows what a consequential action will actually DO so the
        user can approve meaningfully (e.g. ``execute_code`` renders the language +
        a bounded code digest + whether network is requested) — not just the
        generic tool description. Overrides MUST render from the tool's OWN trusted
        view of the validated args and stay BOUNDED (never echo unbounded raw LLM
        text); the gate truncates defensively regardless. Never raises.
        """
        return None

    @abstractmethod
    async def execute(self, **kwargs: object) -> ToolResult: ...

    async def verify(
        self, args: dict[str, object], result: ToolResult, *, started_at: float
    ) -> bool | None:
        """Observe reality to confirm the effect this call CLAIMED — the post-condition.

        Default: ``None`` (no verification) ⇒ every un-migrated tool is byte-identical.
        A tool that produces an artifact overrides this to return ``True`` (effect
        observed), ``False`` (claimed but absent/empty/stale), or ``None`` (could not
        check). Runs at the :meth:`__call__` seam ONLY after a ``success=True``
        execute; ``started_at`` is the call-start epoch time for freshness checks
        (see :func:`stackowl.tools.verification.verify_artifact`). MUST NOT re-do the
        side effect and SHOULD NOT raise (the seam catches and falls back to ``None``).
        """
        return None

    def post_condition(
        self, args: dict[str, object], result: ToolResult
    ) -> object | None:
        """ADR-1 — declare an OBSERVABLE post-condition for THIS call, or ``None``.

        Default ``None`` ⇒ no declared effect ⇒ byte-identical: the AcceptanceAuthority
        is never consulted (the ~92 un-migrated tools are unaffected, flag or no flag).
        A migrated tool returns a
        :class:`~stackowl.pipeline.acceptance_authority.PostCondition`
        (``NonEmptyText`` / ``ArtifactFresh`` / ``HttpOk`` / ``DeliveryAck`` / ``Custom``)
        the authority observes against reality after :meth:`execute`, setting ``verified``
        from a check distinct from the tool. Preferred over :meth:`verify` for the
        non-file effect kinds (text / http / delivery) that the file-only ``verify_artifact``
        cannot express. Return type is intentionally loose (``object``) to avoid importing
        the pipeline layer into the tool ABC; the seam validates the shape. SHOULD NOT
        raise (the seam catches and treats a raise as "no declared post-condition").
        """
        return None

    def _is_retry_safe_severity(self) -> bool:
        """True only for a declared READ-severity tool — the one case where re-running
        execute() after a transient error cannot double-commit a side effect. Any
        failure to classify (a raising manifest, a non-read severity) is treated as
        unsafe so the retry never fires for an effectful tool (fail-safe, F-24)."""
        try:
            return self.manifest.action_severity == "read"
        except Exception as exc:
            log.tool.warning(
                "tool.__call__: could not read action_severity — treating as non-retryable",
                exc_info=exc,
                extra={"_fields": {"tool": self.name}},
            )
            return False

    async def __call__(self, **kwargs: object) -> ToolResult:
        """Invoke execute() and wrap any unhandled exception into a failed ToolResult."""
        import time

        TestModeGuard.assert_not_test_mode(f"tool.{self.name}")
        log.tool.debug(
            "tool.__call__: entry",
            extra={"_fields": {"tool": self.name}},
        )
        t0 = time.monotonic()
        started_at = time.time()  # epoch — for verify() freshness (vs t0's monotonic)

        def _wrap_failure(exc: BaseException) -> ToolResult:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.error(
                "tool.__call__: unhandled exception — wrapping",
                exc_info=exc,
                extra={"_fields": {"tool": self.name, "duration_ms": duration_ms}},
            )
            return ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)

        # D16.1 — the observe-only plugin seam. THIS is the tool chokepoint: every
        # invocation goes through __call__ (it is what times the call and wraps a
        # raise into a failed ToolResult), so a hook here cannot be wired on some
        # paths only. Costs one dict lookup when no plugin is installed, which is
        # every deployment today; a hook can never change the call, only watch it.
        await hooks.dispatch(
            hooks.PRE_TOOL_CALL, {"tool": self.name, "arguments": dict(kwargs)}
        )
        async with TraceContext.span(f"tool.{self.name}"):
            try:
                result = await self.execute(**kwargs)
            except Exception as exc:
                # BOUNDED RETRY-ONCE (F-24) — re-run execute() exactly once, but ONLY for a
                # classifiably-transient exception (dead-handle/connection, via the project's
                # one transient oracle) AND ONLY for a READ-severity tool, where re-executing
                # is provably side-effect-free. Write/consequential tools are NEVER retried
                # here (double-execution of a side effect); their recovery is owned by the
                # substitution/retry actuator in pipeline/steps/execute.py.
                if looks_like_dead_handle(exc) and self._is_retry_safe_severity():
                    log.tool.warning(
                        "tool.__call__: transient (dead-handle) error on read-only tool — retrying once",
                        exc_info=exc,
                        extra={"_fields": {"tool": self.name}},
                    )
                    try:
                        result = await self.execute(**kwargs)
                    except Exception as exc2:
                        result = _wrap_failure(exc2)
                else:
                    result = _wrap_failure(exc)
        # D16.1 — fill in the timing the author did not measure. Only when it is
        # absent (0.0): a tool that supplies its own duration keeps it, because a
        # tool may be reporting something more meaningful than our wall clock (an
        # upstream API's own latency). We fill a GAP, we do not overrule a
        # measurement. Every existing tool sets this, so this is byte-identical
        # for all of them.
        if not result.duration_ms:
            result = result.model_copy(
                update={"duration_ms": (time.monotonic() - t0) * 1000}
            )
        # D03.4 level 3 — enforce the tool's declared result cap HERE, at the one
        # chokepoint, so it cannot be wired on some paths only. Applied before
        # verification so verify() sees what the caller will actually receive.
        result = _apply_result_cap(self, result)
        # VERIFICATION seam — only after a success the tool ASSERTED. A tool-supplied
        # verified=True is a CLAIM, never proof: the seam still runs and its verdict
        # takes precedence (B1/F-25 — a self-asserted verification must never be trusted
        # as reality). verified=False (the tool honestly admitting its effect failed) is
        # left untouched — never second-guessed upward. A claim reality refutes becomes
        # verified=False; a verify() that raises or cannot decide falls back to None so it
        # never blocks a real success — and an UNCONFIRMED self-claim of True is demoted
        # to None (unverified) rather than honored.
        if result.success and result.verified is not False:
            self_claimed_verified = result.verified is True
            try:
                verdict = await self.verify(kwargs, result, started_at=started_at)
            except Exception as exc:  # fail-safe — verification never blocks a success
                log.tool.warning(
                    "tool.__call__: verify() raised — leaving unverified",
                    exc_info=exc,
                    extra={"_fields": {"tool": self.name}},
                )
                verdict = None
            if verdict is not None:
                if verdict is False:
                    log.tool.warning(
                        "tool.__call__: claimed success but verification FAILED",
                        extra={"_fields": {"tool": self.name, "artifact_path": result.artifact_path}},
                    )
                result = result.model_copy(update={"verified": verdict})
            elif self_claimed_verified:
                # Self-asserted True with no independent confirmation — demote to None
                # so a tool can never launder its own self-report into a 'verified' win.
                log.tool.warning(
                    "tool.__call__: self-asserted verified=True not independently confirmed — demoting to unverified",
                    extra={"_fields": {"tool": self.name, "artifact_path": result.artifact_path}},
                )
                result = result.model_copy(update={"verified": None})
        # ADR-1 ACCEPTANCE AUTHORITY seam — a tool that DECLARES a PostCondition has it
        # OBSERVED by the one authority (distinct from the actor); the verdict supersedes
        # the self-report. Same guard as verify() (only second-guess a claimed success, and
        # never override an honest verified=False). Default post_condition()=None ⇒ skipped
        # ⇒ byte-identical; the flag is read ONLY when a post-condition is actually declared,
        # so un-migrated tools never touch Settings here.
        if result.success and result.verified is not False:
            try:
                declared = self.post_condition(kwargs, result)
            except Exception as exc:  # a raising declaration ⇒ no post-condition
                log.tool.warning(
                    "tool.__call__: post_condition() raised — treating as undeclared",
                    exc_info=exc,
                    extra={"_fields": {"tool": self.name}},
                )
                declared = None
            if declared is not None and _acceptance_authority_enabled():
                from stackowl.pipeline.acceptance_authority import (
                    AcceptanceAuthority,
                    final_verified,
                )

                acc_verdict = AcceptanceAuthority().observe(
                    declared,  # type: ignore[arg-type]
                    success=result.success,
                    verified=result.verified,
                    output=result.output,
                    started_at=started_at,
                )
                new_verified = final_verified(
                    success=result.success,
                    verified=result.verified,
                    verdict=acc_verdict,
                )
                if new_verified is not result.verified:
                    if acc_verdict.accepted is False:
                        log.tool.warning(
                            "tool.__call__: declared post-condition REFUTED by observation",
                            extra={"_fields": {
                                "tool": self.name,
                                "post_condition": getattr(declared, "kind", "?"),
                                "reason": acc_verdict.reason,
                            }},
                        )
                    result = result.model_copy(update={"verified": new_verified})
        # NEXT-STEP SIGNAL (F-28). The seam runs exactly one execute() and returns;
        # there is no actuator HERE to drive a self-initiated follow-up when the call
        # did not land. Make that gap at least OBSERVABLE: emit one structured trace
        # line — carrying tool + success + verified — that a supervisor/observer can
        # hook to drive the next step (retry / substitute / re-plan) whenever this
        # call did NOT end in a trustworthy success: a plain failure OR a claim that
        # reality refuted (verified=False). Pure signal — never mutates the result or
        # control flow. A richer next-step ACTUATOR is deferred to the recovery layer
        # (pipeline/steps/execute.py), which already owns retry/substitution.
        if not is_trustworthy_success(result.success, result.verified):
            log.tool.info(
                "tool.__call__: next-step signal — result not trustworthy",
                extra={"_fields": {
                    "tool": self.name, "success": result.success,
                    "verified": result.verified, "next_step": "recover",
                }},
            )
        # Latency map — duration_ms here is the WRAPPER-measured elapsed time
        # (t0-based), not the tool's self-reported result.duration_ms, so every
        # tool is comparable in the trace regardless of whether its own execute()
        # bothers to set duration_ms accurately. reported_duration_ms is kept
        # alongside for spotting a tool whose self-report diverges from reality.
        log.tool.debug(
            "tool.__call__: exit",
            extra={"_fields": {
                "tool": self.name, "success": result.success,
                "verified": result.verified,
                "duration_ms": (time.monotonic() - t0) * 1000,
                "reported_duration_ms": result.duration_ms,
            }},
        )
        # Post fires for a FAILURE too — the event an observer most wants. A hook
        # that only saw successes would be useless for the case it exists for.
        await hooks.dispatch(hooks.POST_TOOL_CALL, {
            "tool": self.name, "success": result.success,
            "verified": result.verified, "error": result.error,
            "duration_ms": (time.monotonic() - t0) * 1000,
        })
        return result
