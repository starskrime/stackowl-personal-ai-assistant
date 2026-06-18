"""``GuiAdapter`` — the OS-neutral GUI automation ABC (E12-S1).

This is the spine the per-OS adapters (E12-S2..S4: xdo / quartz / win32 /
pyautogui) implement. It is ported (neutrally) from the prior-art
computer-use backend ABC, which was explicitly built to be ported to new OS
backends. The abstract methods below perform NO work in this story — they are
the contract; the adapters supply the real capture + input.

INVARIANTS every concrete adapter MUST uphold (the contract this story
establishes; the adapters/tool enforce them):

1. **Real attended-display probe.** :meth:`is_available` probes for a REAL,
   attended display session — NOT merely that a driver library imports. No
   display (headless / CI / no ``$DISPLAY`` / no Wayland socket) → an
   ``unavailable`` :class:`GuiAvailability`, never a crash and never a phantom
   display that clicks at (0, 0).
2. **Redact-or-refuse on capture.** :meth:`capture` redacts the frame BEFORE it
   exists in any loggable memory and returns ``CaptureResult(redacted=True)``
   only when redaction succeeded; if it cannot redact, it must NOT surface the
   frame. The frame is non-persistable (never logged / stored / sent to memory).
3. **Gate before act.** :meth:`perform` NEVER drives input that did not pass the
   safety gate (:func:`~stackowl.tools.gui.safety.evaluate_action`) and the
   emergency-stop check. A refused or unconsented action must not reach the OS.
4. **Per-OS key translation.** Blocked combos and key names are expressed in the
   adapter's own native key namespace (BUILD per OS, not a verbatim port).

Self-healing (B5): probe / capture / perform fail closed — a missing driver, a
dropped display connection, or a denied permission becomes a structured
unavailable/failed result, never an unhandled exception.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from stackowl.tools.gui.models import ActionResult, CaptureResult
from stackowl.tools.gui.schema import CaptureMode, GuiAction

GuiPlatform = Literal["linux", "macos", "windows"]


@dataclass(frozen=True)
class GuiAvailability:
    """Structured result of an adapter availability probe (never an exception).

    ``available`` is the real-attended-display verdict. ``reason`` explains an
    unavailable verdict (no display, missing driver, denied permission) so the
    tool can surface an actionable message and degrade — never crash.
    """

    available: bool
    reason: str | None = None

    @classmethod
    def up(cls) -> GuiAvailability:
        return cls(available=True, reason=None)

    @classmethod
    def down(cls, reason: str) -> GuiAvailability:
        return cls(available=False, reason=reason)


class GuiAdapter(ABC):
    """Abstract per-OS desktop automation backend.

    Concrete adapters (S2-S4) implement :meth:`is_available`, :meth:`capture`,
    and :meth:`perform`, and declare their capabilities. They MUST uphold the
    four module-level invariants (real-display probe, redact-or-refuse,
    gate-before-act, per-OS key translation).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short adapter name (e.g. the driver family), for logs/diagnostics."""
        ...

    @property
    @abstractmethod
    def platform(self) -> GuiPlatform:
        """The OS this adapter targets."""
        ...

    @property
    @abstractmethod
    def supports_som(self) -> bool:
        """Whether this adapter can build a Set-of-Marks element tree.

        When ``False`` the model-facing schema must drop element-targeting and
        coordinate-only autonomous clicking is off by default (per the review):
        the no-SOM path is both the least reliable and least safe mode.
        """
        ...

    @property
    @abstractmethod
    def supports_input(self) -> bool:
        """Whether this adapter can inject real input (vs capture-only).

        A capture-only tier (``False``) is a distinct, lower-risk capability than
        full input control — the agent can *see* but not *act*.
        """
        ...

    @abstractmethod
    async def is_available(self) -> GuiAvailability:
        """Probe for a REAL attended display. Structured result, never raises.

        Must verify an actual interactive session exists — not merely that a
        driver library imports — so headless/CI/no-display hosts report
        unavailable instead of acting against a phantom display.
        """
        ...

    @abstractmethod
    async def capture(self, *, mode: CaptureMode = "som") -> CaptureResult:
        """Capture the screen, REDACTED, into a non-persistable frame.

        Must redact before the frame enters loggable memory and set
        ``redacted=True`` only on success; if redaction cannot run, it must not
        surface the frame (capture-is-not-free). Never logs frame bytes.
        """
        ...

    @abstractmethod
    async def perform(self, action: GuiAction) -> ActionResult:
        """Perform one already-gated action against the real desktop.

        MUST be reached only after the safety gate allowed (and, for destructive
        actions, the user consented to) ``action``, and only while the emergency
        stop is not engaged. Implements the dual-targeting (SOM element or pixel
        coords) the action carries. Fails closed to a non-``ok`` ActionResult.
        """
        ...
