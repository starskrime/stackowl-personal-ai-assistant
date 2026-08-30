"""ToolCallGuardrailController — per-turn tool-loop detection (D05.7).

A PORT OF THE DESIGN, not the code. The reference platform's
``agent/tool_guardrails.py`` is a side-effect-free controller that observes tool
calls and returns *decisions*; the runtime decides whether a decision becomes
guidance, a synthetic result, or a halt. That separation is the good idea and is
kept exactly.

THREE DETECTORS, where StackOwl previously had one:

* **exact repeat** — same tool AND same arguments. This is the one that matters
  here and the one a name-only counter cannot express. Measured: a 46-call
  ``web_search`` turn by ``headhunter`` returned a real synthesized list of live
  job postings — legitimate fan-out with 46 different queries. Any guard keyed on
  the tool NAME alone would have halted productive work; keyed on (name, args) it
  does not fire at all.
* **same-tool failure** — the same tool failing repeatedly with different args.
* **idempotent no-progress** — a read-only tool returning the SAME RESULT again.
  This is what our previous tracker was structurally blind to:
  ``is_trustworthy_success`` → ``record_progress`` reset the streak, so a
  *successful* repeat looked like progress.

WARN-ONLY, BY OPERATOR DECISION, AND THERE IS NO OTHER MODE. Repetition is a
smell, not proof: the headhunter case (46 web_searches returning a real
synthesized result) shows a high count can be correct, and a wrong halt truncates
real work. A genuine loop is instead bounded by the existing turn iteration cap
(``DEFAULT_TURN_MAX_STEPS = 20``), so "warn only" is bounded, not unbounded.

THE HARD-STOP MACHINERY WAS REMOVED 2026-08-30, by operator decision, after an
audit found it could never run. ``hard_stop_enabled`` had no setter anywhere in
``src/`` or ``tests/``, so three block/halt branches were unreachable — and two of
them lived in ``before_call``, which production never called at all (execute.py
constructs this controller and uses only ``after_call``). Code that reads as
active protection and cannot fire is worse than no code. It is in git if the
decision is ever revisited; the reference platform documents these as opt-in
circuit breakers for autonomous sessions, which is the case for bringing them
back.

TWO DELIBERATE DIVERGENCES from the reference implementation:

1. **Idempotence is read from the manifest, not a hardcoded name list.** Theirs
   carries ``IDEMPOTENT_TOOL_NAMES`` / ``MUTATING_TOOL_NAMES`` frozensets. We
   already declare ``action_severity`` per tool, and ``"read"`` *is* the
   idempotence claim — so a new read-only tool is covered the day it is written,
   with no list to update. Hardcoded tool-name lists are against a standing rule
   in this repo.
2. **Failure is supplied by the caller, never sniffed from result text.** Theirs
   falls back to ``classify_tool_failure`` — substring-matching ``'"error"'`` in
   the result blob. That fallback is NOT their production path (their own
   docstring: *"Production callers always pass an explicit failed="*), so
   requiring ``failed`` here is faithful to their design and lets StackOwl feed
   the structured ``is_trustworthy_success(success, verified)`` verdict — the B4a
   rule that a claimed-but-unobserved effect must not look like progress.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from stackowl.infra.observability import log

__all__ = [
    "ToolCallGuardrailConfig",
    "ToolCallGuardrailController",
    "ToolCallSignature",
    "ToolGuardrailDecision",
    "canonical_tool_args",
]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def canonical_tool_args(args: Mapping[str, Any] | None) -> str:
    """Sorted, compact JSON for tool arguments — the identity of a call.

    ``sort_keys`` so argument ORDER never changes the signature, and
    ``default=str`` so an unserialisable value degrades to its repr rather than
    raising inside a guardrail (which must never cost a turn its tools).
    """
    if not isinstance(args, Mapping):
        return "{}"
    try:
        return json.dumps(
            args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
    except Exception:  # noqa: BLE001 — identity is best-effort, never fatal
        return repr(sorted(args))


@dataclass(frozen=True)
class ToolCallSignature:
    """Stable, NON-REVERSIBLE identity for a tool name plus canonical args.

    The args are hashed, never stored. A tool call can carry a password, a token
    or a file's contents, and this object is logged.
    """

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> ToolCallSignature:
        return cls(tool_name=tool_name, args_hash=_sha256(canonical_tool_args(args)))


@dataclass(frozen=True)
class ToolGuardrailDecision:
    """What the controller observed. Carries no side effects — the caller acts."""

    action: str = "allow"  # allow | warn | block | halt
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}



@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Thresholds for per-turn loop detection.

    Warnings are on by default and NEVER prevent execution. There is no hard-stop
    threshold: repetition is a smell, not proof, and a genuine loop runs to the
    iteration cap rather than risking a truncated valid fan-out (D05.7).
    """

    warnings_enabled: bool = True
    exact_repeat_warn_after: int = 2
    same_tool_failure_warn_after: int = 3
    no_progress_warn_after: int = 2


class ToolCallGuardrailController:
    """Observes one turn's tool calls; returns decisions, changes nothing."""

    def __init__(self, config: ToolCallGuardrailConfig | None = None) -> None:
        self.config = config or ToolCallGuardrailConfig()
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._exact_repeats: dict[ToolCallSignature, int] = {}

    # ------------------------------------------------------------------- after
    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str | None,
        *,
        failed: bool,
        idempotent: bool,
    ) -> ToolGuardrailDecision:
        """Record the outcome of a completed dispatch and return a decision.

        ``failed`` is REQUIRED — there is deliberately no result-sniffing
        fallback. StackOwl's caller has a structured verdict
        (``is_trustworthy_success(success, verified)``); guessing from the text
        would be strictly worse and would silently disagree with it.

        ``idempotent`` comes from the tool's declared ``action_severity``, so no
        tool-name list is maintained here.
        """
        signature = ToolCallSignature.from_call(tool_name, args)

        if failed:
            return self._record_failure(tool_name, signature)

        # Success clears the FAILURE counters — a working call is not a failure
        # loop, whatever came before it.
        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        # EXACT REPEAT — the detector our previous tracker could not express.
        # Counted on SUCCESS, which is the whole point: 46 successful searches
        # with 46 different queries produce 46 distinct signatures and never fire;
        # 46 successful searches with ONE query fire immediately.
        repeats = self._exact_repeats.get(signature, 0) + 1
        self._exact_repeats[signature] = repeats
        if self.config.warnings_enabled and repeats >= self.config.exact_repeat_warn_after:
            return self._warn(
                "repeated_exact_call_warning", tool_name, repeats, signature,
                f"{tool_name} has been called {repeats} times with identical "
                "arguments. Use the result you already have, or change the arguments.",
            )

        if not idempotent:
            self._no_progress.pop(signature, None)
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        # IDEMPOTENT NO-PROGRESS — same read-only call, same RESULT.
        result_hash = _sha256(result or "")
        previous = self._no_progress.get(signature)
        count = previous[1] + 1 if previous is not None and previous[0] == result_hash else 1
        self._no_progress[signature] = (result_hash, count)
        if self.config.warnings_enabled and count >= self.config.no_progress_warn_after:
            return self._warn(
                "idempotent_no_progress_warning", tool_name, count, signature,
                f"{tool_name} returned the same result {count} times. Use the result "
                "already provided or change the query.",
            )
        return ToolGuardrailDecision(tool_name=tool_name, count=count, signature=signature)

    # ---------------------------------------------------------------- internals
    def _record_failure(
        self, tool_name: str, signature: ToolCallSignature
    ) -> ToolGuardrailDecision:
        exact = self._exact_failure_counts.get(signature, 0) + 1
        self._exact_failure_counts[signature] = exact
        self._no_progress.pop(signature, None)
        self._exact_repeats.pop(signature, None)

        same = self._same_tool_failure_counts.get(tool_name, 0) + 1
        self._same_tool_failure_counts[tool_name] = same

        if self.config.warnings_enabled and exact >= self.config.exact_repeat_warn_after:
            return self._warn(
                "repeated_exact_failure_warning", tool_name, exact, signature,
                f"{tool_name} has failed {exact} times with identical arguments. "
                "Inspect the error and change strategy instead of retrying unchanged.",
            )
        if self.config.warnings_enabled and same >= self.config.same_tool_failure_warn_after:
            return self._warn(
                "same_tool_failure_warning", tool_name, same, signature,
                f"{tool_name} has failed {same} times this turn with different "
                "arguments. Consider a different tool or approach.",
            )
        return ToolGuardrailDecision(tool_name=tool_name, count=exact, signature=signature)

    def _warn(
        self, code: str, tool: str, count: int, sig: ToolCallSignature, message: str
    ) -> ToolGuardrailDecision:
        log.engine.info(
            "[guardrails] tool-loop warning",
            extra={"_fields": {
                "tool": tool, "code": code, "count": count, "args_hash": sig.args_hash,
            }},
        )
        return ToolGuardrailDecision(
            action="warn", code=code, message=message,
            tool_name=tool, count=count, signature=sig,
        )

