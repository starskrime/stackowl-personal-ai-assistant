"""GUI automation foundation (E12-S1) — the OS-neutral ``computer_use`` substrate.

This package holds the *contract* for desktop GUI automation: the action schema,
the result models, the :class:`GuiAdapter` ABC, the safety gate, and local-first
vision routing. It deliberately performs NO real OS input and NO screen capture
of its own — the per-OS adapters (E12-S2..S4: xdo / quartz / win32 / pyautogui)
implement the abstract methods, and the tool (E12-S6) wires everything to the
pipeline. This is the foundation only.

Placement note: per the operator's vote the tool, the adapters, and the safety
gate all live together under ``stackowl/tools/gui/`` (the original plan named
``automation/gui/``).

Every adapter that subclasses :class:`GuiAdapter` MUST uphold the invariants
documented on it: probe a REAL attended display, redact-or-refuse on capture,
and route every mutating action through the safety gate before acting.
"""

from __future__ import annotations

from stackowl.tools.gui.base import GuiAdapter, GuiAvailability, GuiPlatform
from stackowl.tools.gui.models import ActionResult, CaptureResult, ElementBounds, UIElement
from stackowl.tools.gui.safety import (
    BLOCKED_KEY_COMBOS,
    BLOCKED_TYPE_PATTERNS,
    ActionClass,
    EmergencyStop,
    SafetyDecision,
    classify_action,
    evaluate_action,
)
from stackowl.tools.gui.schema import GuiAction
from stackowl.tools.gui.vision_routing import DesktopVisionRouter, DesktopVisionRouting
from stackowl.tools.gui.xdo import XdoAdapter

__all__ = [
    "BLOCKED_KEY_COMBOS",
    "BLOCKED_TYPE_PATTERNS",
    "ActionClass",
    "ActionResult",
    "CaptureResult",
    "DesktopVisionRouter",
    "DesktopVisionRouting",
    "ElementBounds",
    "EmergencyStop",
    "GuiAction",
    "GuiAdapter",
    "GuiAvailability",
    "GuiPlatform",
    "SafetyDecision",
    "UIElement",
    "XdoAdapter",
    "classify_action",
    "evaluate_action",
]
