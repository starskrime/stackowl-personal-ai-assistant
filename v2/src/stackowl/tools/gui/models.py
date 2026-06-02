"""Frozen result models for the GUI substrate (E12-S1).

``UIElement`` / ``CaptureResult`` / ``ActionResult`` are the OS-neutral shapes
every :class:`~stackowl.tools.gui.base.GuiAdapter` returns, ported (neutrally)
from the prior-art computer-use dataclasses. They are pydantic ``frozen`` +
``extra='forbid'`` — an immutable, fully-typed value an adapter produces and the
tool (E12-S6) consumes.

Sensitive-data contract (CLAUDE.md): a :class:`CaptureResult` carries the raw
frame as bytes but those bytes are NEVER logged — only ``width``/``height`` and
``redacted`` status. A frame is non-persistable (the vision-routing layer marks
and enforces this); it must never reach logs, pellets, memory, or transcripts.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ElementBounds(BaseModel):
    """Logical-pixel bounding box of a UI element (x, y, width, height)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: int
    y: int
    width: int
    height: int

    def center(self) -> tuple[int, int]:
        """Return the centre pixel of the box (handy for coordinate fallback)."""
        return self.x + self.width // 2, self.y + self.height // 2


class UIElement(BaseModel):
    """One interactable element discovered on the current screen (a SOM entry).

    ``id`` is the Set-of-Marks index the model targets via
    ``GuiAction(action='click', element=id)``. ``role`` is the accessibility
    role (e.g. ``button``, ``text_field``); ``label`` the visible/AX label;
    ``value`` the current value where the element exposes one (a field's text,
    a slider's number) — ``None`` when not applicable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    role: str
    label: str = ""
    bounds: ElementBounds
    value: str | None = None


class CaptureResult(BaseModel):
    """The outcome of a single screen capture.

    ``frame`` holds the raw image bytes (an opaque handle the adapter produced).
    ``elements`` is the SOM tree (empty on a non-SOM / no-AX capture).
    ``redacted`` asserts the redaction pass ran and succeeded — capture is gated
    on this being ``True`` (capture-is-not-free; a frame that could not be
    redacted must never be surfaced). ``is_local_vision`` records that the frame
    was (or will be) described by an on-box vision model only — desktop pixels
    never leave the machine.

    NEVER log ``frame``; log only ``width``/``height``/``redacted``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    frame: bytes
    width: int
    height: int
    elements: tuple[UIElement, ...] = ()
    redacted: bool = False
    is_local_vision: bool = False


class ActionResult(BaseModel):
    """The outcome of performing one :class:`GuiAction`.

    ``ok`` is the success flag; ``action`` echoes the action name; ``detail`` is
    a SHORT human-readable summary (never the typed text content — sensitive);
    ``duration_ms`` times the perform.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    action: str
    detail: str = ""
    duration_ms: float = 0.0
