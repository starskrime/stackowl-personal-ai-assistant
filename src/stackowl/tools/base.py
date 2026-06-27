"""Tool ABC and ToolResult — base contract for all pipeline tools (ARCH-94)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.infra.resilience import looks_like_dead_handle
from stackowl.tools.verification import is_trustworthy_success


def _acceptance_authority_enabled() -> bool:
    """Read the ADR-1 ``acceptance_authority`` flag. Fail-safe to ``False`` (the
    byte-identical default) on any config error — the seam must never break a tool
    call by failing to read a flag. Consulted ONLY when a tool declares a
    post-condition, so the ~92 un-migrated tools never construct Settings here."""
    try:
        from stackowl.config.settings import Settings

        return bool(Settings().acceptance_authority)
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
    duration_ms: float
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
    # Toolset-group name for DNA-gated presentation (e.g. "code", "media", "home").
    # An owl's capability_profile lists group names; a tool joins the presented set
    # when its toolset_group is in that profile (ADR-11 / E1-S4). Distinct from
    # consent_category (which is about consent, not grouping).
    toolset_group: str | None = None
    # Capability tag groups tools that produce the same KIND of result, enabling
    # self-healing substitution: when a tool in a capability class fails, the
    # supervisor can route to a sibling with the same tag (W3 substitution actuator).
    capability_tag: str | None = None
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


class Tool(ABC):
    """Abstract base for all tools available to the pipeline (ARCH-94).

    execute() may raise — __call__ catches and wraps into a failed ToolResult.
    """

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
        log.tool.debug(
            "tool.__call__: exit",
            extra={"_fields": {
                "tool": self.name, "success": result.success,
                "verified": result.verified, "duration_ms": result.duration_ms,
            }},
        )
        return result
