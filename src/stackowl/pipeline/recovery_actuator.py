"""RecoveryActuator — one bounded recovery ladder any failing operation hands a failure to.

ADR-2. Retry / tier-fallback / substitution / replan / surrender existed as ~12 point
solutions (the execute-loop B4 ladder, the provider gateway cascade, the substitution
actuator, the scheduler/channel/objective sites), each re-deciding "is this recoverable?"
and "may I retry?" on its own. This module is the single authority they delegate to:

* :class:`Failure` — the typed input every site builds (kind/transient/consequential/
  capability_tag/attempt history). Classification REUSES the project's transient
  vocabulary (``DEFAULT_DEAD_HANDLE_MARKERS``) and the ADR-1 ``verified`` signal — no new
  keyword list, no per-site heuristic.
* :class:`RecoveryActuator` — ``recover(failure, attempt, …)`` runs the bounded ladder
  ``retry → reroute → substitute → honest-surrender``, RE-VERIFYING each rung's result
  (ADR-1) and stopping at the first trustworthy success. ``should_retry`` is the one
  predicate; a CONSEQUENTIAL failure is NEVER auto-retried (it skips the retry rung).

Nothing removed: the existing sites keep their own rung handlers (their reroute/substitute
thunks); they gain a shared policy + a shared "recovered" ledger
(:mod:`stackowl.infra.recovery_context`). A "recovered" claim is only made on a verified
rung — recovery counts only when reality confirms it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from stackowl.infra import decision_ledger, recovery_context
from stackowl.infra.observability import log
from stackowl.tools.base import ToolResult

# A rung is an async thunk producing a fresh attempt at the operation; a verifier decides
# whether a rung's result is a trustworthy success (ADR-1). Deliberately typed over
# ``object`` (not a TypeVar): the actuator does not need to preserve the caller's concrete
# result type — callers read RecoveryOutcome.result (object) or capture results in their own
# closures. This keeps the authority general over every subsystem's result shape.
Rung = Callable[[], Awaitable[object]]
Verifier = Callable[[object], bool]


def is_transient_result(tr: ToolResult) -> bool:
    """Classify a tool result as a TRANSIENT failure (a dropped connection, reset socket,
    momentarily locked DB, closed pipe) that can self-heal on a second attempt — versus a
    deterministic failure (bad input, missing capability, refusal) that cannot. Reuses the
    project's established dead-handle marker set (the SAME infrastructure-fault vocabulary
    the resource-recycling layer trusts), reading only the structured error/output text.
    This is the relocated home of the former ``execute._is_transient_failure`` (F-7); the
    execute loop delegates here so the classifier lives with the recovery authority."""
    from stackowl.infra.resilience import DEFAULT_DEAD_HANDLE_MARKERS

    text = f"{tr.error or ''}\n{tr.output or ''}"
    return any(marker in text for marker in DEFAULT_DEAD_HANDLE_MARKERS)


@dataclass(frozen=True)
class Failure:
    """A typed, classified failure handed to the actuator. ``transient`` and
    ``unverified_effect`` are the two recoverable shapes; ``consequential`` is the hard
    no-auto-retry guard. ``capability_tag``/``goal_ref`` let later rungs (substitute/replan)
    target the right sibling/goal. ``attempt`` is a free-form history label for the ledger."""

    name: str
    kind: str = "tool"
    transient: bool = False
    unverified_effect: bool = False
    consequential: bool = False
    capability_tag: str | None = None
    goal_ref: str | None = None
    error: str | None = None
    attempt: int = 1


def classify_tool_failure(
    tr: ToolResult,
    *,
    name: str,
    consequential: bool,
    capability_tag: str | None = None,
    goal_ref: str | None = None,
) -> Failure:
    """Build a :class:`Failure` from a tool result — derive ``transient`` from the marker
    vocabulary and ``unverified_effect`` from the ADR-1 verified signal (success claimed but
    reality refuted it). No keyword list, no per-site logic."""
    return Failure(
        name=name,
        kind="tool",
        transient=(not tr.success) and is_transient_result(tr),
        unverified_effect=bool(tr.success) and tr.verified is False,
        consequential=consequential,
        capability_tag=capability_tag,
        goal_ref=goal_ref,
        error=tr.error,
    )


@dataclass(frozen=True)
class RecoveryOutcome:
    """The actuator's verdict. ``recovered`` ⇒ a rung produced a re-VERIFIED success;
    ``via`` names the rung (``retry``/``reroute``/``substitute``/``surrender``);
    ``result`` is the recovered value (None on surrender)."""

    recovered: bool
    via: str
    result: object | None = None
    detail: str = ""
    rungs_tried: tuple[str, ...] = field(default_factory=tuple)


class RecoveryActuator:
    """The single recovery authority. Stateless; the bounded ladder is its only policy."""

    def should_retry(self, failure: Failure) -> bool:
        """True iff this failure may be AUTO-retried: a recoverable shape (transient or an
        unverified effect) that is NOT consequential. A consequential action is never
        auto-retried (it could double-commit a side effect) — it skips straight to
        reroute/substitute/surrender. THE single retry predicate every site reads."""
        if failure.consequential:
            return False
        return failure.transient or failure.unverified_effect

    async def recover(
        self,
        failure: Failure,
        attempt: Rung | None = None,
        *,
        reroute: Rung | None = None,
        substitute: Rung | None = None,
        verify: Verifier | None = None,
        record: bool = True,
    ) -> RecoveryOutcome:
        """Run the ladder and emit one ADR-7 ``recovery`` Decision for its verdict.

        Thin wrapper over :meth:`_recover` (the bounded ladder): records which rungs were
        tried and whether the turn recovered or honestly surrendered, so "why did it retry
        / why did it give up?" is a read of the ledger. No-op when the ledger is unbound."""
        outcome = await self._recover(
            failure, attempt, reroute=reroute, substitute=substitute,
            verify=verify, record=record,
        )
        decision_ledger.record_decision(
            point="recovery",
            verdict="recovered" if outcome.recovered else "surrendered",
            reason=outcome.detail,
            inputs={
                "failure": failure.name,
                "kind": failure.kind,
                "consequential": failure.consequential,
            },
            alternatives_considered=outcome.rungs_tried,
            evidence={"via": outcome.via},
        )
        return outcome

    async def _recover(
        self,
        failure: Failure,
        attempt: Rung | None = None,
        *,
        reroute: Rung | None = None,
        substitute: Rung | None = None,
        verify: Verifier | None = None,
        record: bool = True,
    ) -> RecoveryOutcome:
        """Run the bounded ladder and return the first re-verified success, or an honest
        surrender. Rungs are optional thunks the caller supplies (its own retry/reroute/
        substitute); each is RE-VERIFIED via ``verify`` (default: a non-None result), and a
        rung that raises is contained (advances the ladder, never propagates). Records a
        :mod:`recovery_context` event for the rung that succeeds (so the give-up floor sees
        the consequential goal WAS achieved). Never raises."""
        _verify: Verifier = verify or (lambda r: r is not None)
        tried: list[str] = []

        # RUNG 1 — retry once (only an auto-retryable, non-consequential failure).
        if attempt is not None and self.should_retry(failure):
            tried.append("retry")
            ok, value = await self._run_rung("retry", attempt, _verify, failure)
            if ok:
                self._record(record, "retry", failure)
                return RecoveryOutcome(True, "retry", value, "retried once", tuple(tried))

        # RUNG 2 — reroute (alternate provider tier / channel) — supplied by the caller.
        if reroute is not None:
            tried.append("reroute")
            ok, value = await self._run_rung("reroute", reroute, _verify, failure)
            if ok:
                self._record(record, "reroute", failure)
                return RecoveryOutcome(True, "reroute", value, "rerouted", tuple(tried))

        # RUNG 3 — substitute (capability sibling) — supplied by the caller.
        if substitute is not None:
            tried.append("substitute")
            ok, value = await self._run_rung("substitute", substitute, _verify, failure)
            if ok:
                self._record(record, "substitution", failure)
                return RecoveryOutcome(
                    True, "substitute", value, "substituted", tuple(tried)
                )

        # RUNG 4 — honest surrender: the ladder is exhausted, nothing was re-verified.
        log.engine.info(
            "[recovery_actuator] ladder exhausted — honest surrender",
            extra={"_fields": {
                "failure": failure.name, "kind": failure.kind,
                "consequential": failure.consequential, "rungs_tried": tried,
            }},
        )
        return RecoveryOutcome(False, "surrender", None, "ladder exhausted", tuple(tried))

    async def _run_rung(
        self, label: str, rung: Rung, verify: Verifier, failure: Failure
    ) -> tuple[bool, object | None]:
        """Run one rung; contain a raise; re-verify the result. Returns (accepted, value)."""
        try:
            value = await rung()
        except Exception as exc:  # noqa: BLE001 — a failing rung advances the ladder
            log.engine.warning(
                "[recovery_actuator] rung raised — advancing ladder",
                exc_info=exc,
                extra={"_fields": {"rung": label, "failure": failure.name}},
            )
            return (False, None)
        try:
            accepted = bool(verify(value))
        except Exception as exc:  # noqa: BLE001 — a raising verifier is "not verified"
            log.engine.warning(
                "[recovery_actuator] verifier raised — treating rung as unverified",
                exc_info=exc,
                extra={"_fields": {"rung": label, "failure": failure.name}},
            )
            return (False, None)
        return (accepted, value if accepted else None)

    def _record(self, record: bool, kind: str, failure: Failure) -> None:
        """Record a recovery event so the give-up floor sees the goal was achieved."""
        if not record:
            return
        recovery_context.record_recovery(
            kind=kind,
            failed=failure.name,
            recovered_via=failure.name if kind == "retry" else f"{failure.name}:{kind}",
            detail=f"recovered via {kind}",
            user_visible=False,
        )
