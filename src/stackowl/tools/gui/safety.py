"""The GUI safety gate (E12-S1) — pure, testable, never-raises.

This module defines the SAFETY CONTRACT every adapter and the tool (E12-S6)
uphold before any input touches the user's real desktop. It is the most
security-critical surface in the GUI epic; the adversarial review made several
controls non-negotiable, encoded here:

* **Capture is NOT silently free.** The prior art classed ``capture`` as a free
  "safe" action requiring no consent — the review's #1 incident chain. Here
  ``capture`` is gated on redaction success (see :func:`evaluate_action`): a
  capture whose frame was not redacted is REFUSED. Every mutating action
  (click / type / key / drag / set_value / move / scroll) is ``destructive`` and
  requires approval.
* **Blocked key combos are refused HARD** (never reach the adapter), per OS key
  namespace (data in :mod:`stackowl.tools.gui.safety_blocks`).
* **Blocked type patterns** refuse typing a detected destructive/credential
  payload into the focused surface.
* **Emergency stop** — a process-global async cancel flag the tool checks
  before and between actions (the review's #1 invariant). The contract lives
  here; the tool wires it.

Nothing in this module performs OS I/O or raises: a denied action returns a
structured :class:`SafetyDecision` (B5 — fail closed, never crash).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from stackowl.infra.observability import log
from stackowl.tools.gui.safety_blocks import (
    BLOCKED_KEY_COMBOS,
    BLOCKED_TYPE_PATTERNS,
    canon_key_combo,
)
from stackowl.tools.gui.schema import GuiAction

__all__ = [
    "BLOCKED_KEY_COMBOS",
    "BLOCKED_TYPE_PATTERNS",
    "ActionClass",
    "EmergencyStop",
    "SafetyDecision",
    "classify_action",
    "evaluate_action",
]

ActionClass = Literal["safe", "destructive"]

# Mutating actions — drive real input / change visible state → require approval.
_DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset(
    {
        "click",
        "double_click",
        "right_click",
        "move",
        "drag",
        "scroll",
        "type",
        "key",
        "set_value",
    },
)


def classify_action(action: GuiAction) -> ActionClass:
    """Classify an action as ``safe`` (non-mutating) or ``destructive``.

    Note: ``safe`` means "does not mutate user-visible state", NOT "consent-free".
    ``capture`` is the only ``safe`` action and is still gated on redaction by
    :func:`evaluate_action`. Pure; never raises.
    """
    if action.action in _DESTRUCTIVE_ACTIONS:
        return "destructive"
    return "safe"


@dataclass(frozen=True)
class SafetyDecision:
    """The outcome of the safety gate for one action — a value, never an exception.

    * ``allowed`` — the action may proceed (subject to ``requires_approval``).
    * ``requires_approval`` — a destructive action that must be consented before
      the adapter performs it.
    * ``refused`` — a hard block (blocked combo / blocked pattern / capture that
      could not be redacted / emergency stop engaged). The action MUST NOT reach
      the adapter.
    """

    action_class: ActionClass
    allowed: bool
    requires_approval: bool
    refused: bool
    reason: str | None = None

    @classmethod
    def refuse(cls, action_class: ActionClass, reason: str) -> SafetyDecision:
        return cls(action_class, allowed=False, requires_approval=False, refused=True, reason=reason)

    @classmethod
    def needs_approval(cls, reason: str | None = None) -> SafetyDecision:
        return cls("destructive", allowed=True, requires_approval=True, refused=False, reason=reason)

    @classmethod
    def allow(cls) -> SafetyDecision:
        return cls("safe", allowed=True, requires_approval=False, refused=False, reason=None)


class EmergencyStop:
    """Process-global async kill switch for desktop input (the review's #1 invariant).

    The tool (E12-S6) checks :meth:`engaged` BEFORE and BETWEEN every action; an
    engaged stop turns every subsequent :func:`evaluate_action` into a hard
    refusal, so a hung/runaway agent cannot continue injecting input. The
    contract is defined here; the wiring (a global hotkey / panic corner /
    channel command that calls :meth:`engage`) lives in the tool/channel layer.

    Self-healing: :meth:`reset` clears the flag so a session can resume after the
    human releases the stop. The :class:`asyncio.Event` lets a waiting perform
    loop wake immediately on engage.
    """

    def __init__(self) -> None:
        self._engaged = False
        self._event = asyncio.Event()

    def engage(self, reason: str = "emergency stop engaged") -> None:
        """Engage the stop — all input injection halts immediately."""
        self._engaged = True
        self._event.set()
        log.security.warning(
            "[gui.safety] EMERGENCY STOP engaged — halting all desktop input",
            extra={"_fields": {"reason": reason}},
        )

    def reset(self) -> None:
        """Release the stop so a new session may resume (self-heal)."""
        self._engaged = False
        self._event.clear()
        log.security.info("[gui.safety] emergency stop reset")

    def engaged(self) -> bool:
        return self._engaged

    async def wait_engaged(self) -> None:
        """Await until the stop is engaged (lets a perform loop race on it)."""
        await self._event.wait()


def _check_blocked_type(action: GuiAction) -> SafetyDecision | None:
    if action.action != "type" or not action.text:
        return None
    for pattern in BLOCKED_TYPE_PATTERNS:
        if pattern.search(action.text):
            # Sensitive-data: never log the typed text; log the pattern only.
            log.security.warning(
                "[gui.safety] refused type — blocked destructive pattern",
                extra={"_fields": {"pattern": pattern.pattern}},
            )
            return SafetyDecision.refuse(
                "destructive",
                "typing this content is blocked (destructive shell/credential pattern)",
            )
    return None


def _check_blocked_combo(action: GuiAction, os_name: str) -> SafetyDecision | None:
    if action.action != "key" or not action.keys:
        return None
    combo = canon_key_combo(action.keys)
    blocked = BLOCKED_KEY_COMBOS.get(os_name.lower(), frozenset())
    for entry in blocked:
        if entry and entry.issubset(combo):
            log.security.warning(
                "[gui.safety] refused key — blocked system combo",
                extra={"_fields": {"os": os_name, "combo": sorted(combo)}},
            )
            return SafetyDecision.refuse(
                "destructive",
                f"key combo {sorted(combo)} is hard-blocked on {os_name}",
            )
    return None


def evaluate_action(
    action: GuiAction,
    *,
    os_name: str,
    emergency_stop: EmergencyStop | None = None,
    capture_redacted: bool | None = None,
) -> SafetyDecision:
    """The single gate entry point — pure, fails closed, never raises.

    Decision order (each fails closed):

    1. **Emergency stop** — if engaged, refuse everything.
    2. **Blocked key combo** — a per-OS system shortcut is refused hard.
    3. **Blocked type pattern** — a destructive/credential payload is refused.
    4. **Capture redaction** — ``capture`` is allowed ONLY when
       ``capture_redacted is True`` (capture-is-not-free). Unknown/false → refuse.
    5. **Destructive** actions → ``requires_approval``.
    6. Otherwise → allow.

    ``capture_redacted`` is supplied by the adapter's redaction pass for a
    ``capture`` action; it is ignored for other actions.
    """
    log.tool.debug(
        "[gui.safety] evaluate: entry",
        extra={"_fields": {"action": action.action, "os": os_name}},
    )

    if emergency_stop is not None and emergency_stop.engaged():
        log.security.warning("[gui.safety] refused — emergency stop engaged")
        return SafetyDecision.refuse(classify_action(action), "emergency stop engaged — input halted")

    combo_block = _check_blocked_combo(action, os_name)
    if combo_block is not None:
        return combo_block

    type_block = _check_blocked_type(action)
    if type_block is not None:
        return type_block

    action_class = classify_action(action)

    if action.action == "capture":
        # Capture is NOT free: gate on redaction success (fail closed).
        if capture_redacted is not True:
            log.security.warning(
                "[gui.safety] refused capture — frame not redacted (capture is not free)",
            )
            return SafetyDecision.refuse(
                action_class,
                "capture refused: the frame could not be redacted (capture is gated on redaction)",
            )
        log.tool.debug("[gui.safety] evaluate: exit — capture allowed (redacted)")
        return SafetyDecision.allow()

    if action_class == "destructive":
        log.tool.debug(
            "[gui.safety] evaluate: exit — destructive, requires approval",
            extra={"_fields": {"action": action.action}},
        )
        return SafetyDecision.needs_approval(f"{action.action} drives real desktop input")

    log.tool.debug("[gui.safety] evaluate: exit — allowed")
    return SafetyDecision.allow()
