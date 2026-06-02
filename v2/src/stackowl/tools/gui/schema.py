"""``GuiAction`` — the OS-neutral, frozen, validated desktop-action model (E12-S1).

ONE consolidated action discriminated by ``action`` keeps the model-facing tool
compact and the per-turn token cost low. The shape is ported (neutrally — no OS
namespace baked in) from the prior-art consolidated computer-use schema.

Dual targeting (the key reliability primitive): an action that needs a screen
target carries EITHER

  * ``element`` — a Set-of-Marks (SOM) element id from the last ``capture`` (the
    preferred, far-more-reliable path: the model clicks an element by number,
    not by guessing pixels), OR
  * ``x`` + ``y`` — raw pixel coordinates (the fallback for models trained on
    coordinates, or when no SOM tree is available).

Exactly one targeting mode must be supplied for a targeted action — supplying
both, or neither, is a validation error. Non-targeted actions (``capture``,
``type``, ``key``) take their own fields and must NOT carry a target.

The model is ``frozen`` + ``extra='forbid'`` (B-frozen idiom): an action is an
immutable, fully-validated value. No real OS work happens here — the adapters
(E12-S2..S4) consume a validated ``GuiAction`` and perform the input.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# Actions that aim at a specific on-screen point/element (dual targeting applies).
_TARGETED: frozenset[str] = frozenset(
    {"click", "double_click", "right_click", "move", "scroll", "set_value"},
)
# ``drag`` is targeted but with a SOURCE and a DESTINATION (two points), so it is
# validated separately rather than via the single-target rule.
_DRAG: str = "drag"

GuiActionName = Literal[
    "capture",
    "click",
    "double_click",
    "right_click",
    "move",
    "drag",
    "scroll",
    "type",
    "key",
    "set_value",
]

CaptureMode = Literal["som", "image", "ax"]
ScrollDirection = Literal["up", "down", "left", "right"]


class GuiAction(BaseModel):
    """One immutable, validated desktop action.

    Discriminated by :attr:`action`. Field relevance per action:

    * ``capture`` — ``mode`` (som|image|ax). No target.
    * ``click`` / ``double_click`` / ``right_click`` / ``move`` — a target
      (``element`` XOR ``x``+``y``); ``modifiers`` optional.
    * ``drag`` — a source (``element`` XOR ``x``+``y``) AND a destination
      (``to_element`` XOR ``to_x``+``to_y``).
    * ``scroll`` — a target plus ``direction`` and ``amount``.
    * ``type`` — ``text`` (no target; types into the focused surface).
    * ``key`` — ``keys`` (a combo string, e.g. ``ctrl+s``; no target).
    * ``set_value`` — a target plus ``value`` (sets a widget value directly).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: GuiActionName

    # ── capture ──────────────────────────────────────────────────────────
    mode: CaptureMode = "som"

    # ── primary target: SOM element id XOR pixel coords (exactly one) ─────
    element: int | None = None
    x: int | None = None
    y: int | None = None

    # ── drag destination: SOM element id XOR pixel coords (exactly one) ──
    to_element: int | None = None
    to_x: int | None = None
    to_y: int | None = None

    # ── scroll ───────────────────────────────────────────────────────────
    direction: ScrollDirection | None = None
    amount: int = 3

    # ── type / key / set_value payloads ──────────────────────────────────
    text: str | None = None
    keys: str | None = None
    value: str | None = None

    # ── modifier keys held during a pointer/key action ───────────────────
    modifiers: tuple[str, ...] = ()

    @staticmethod
    def _has_pixel(x: int | None, y: int | None) -> bool:
        return x is not None and y is not None

    @staticmethod
    def _has_partial_pixel(x: int | None, y: int | None) -> bool:
        return (x is None) != (y is None)

    @model_validator(mode="after")
    def _validate_targeting_and_payload(self) -> GuiAction:
        action = self.action

        # A pixel target must be a COMPLETE pair (never a lone x or y).
        if self._has_partial_pixel(self.x, self.y):
            raise ValueError("pixel target requires BOTH x and y")
        if self._has_partial_pixel(self.to_x, self.to_y):
            raise ValueError("pixel destination requires BOTH to_x and to_y")

        has_element = self.element is not None
        has_pixel = self._has_pixel(self.x, self.y)

        if action in _TARGETED:
            # Exactly one of (element) / (x+y).
            if has_element and has_pixel:
                raise ValueError(
                    f"{action}: supply EITHER element OR x+y, not both",
                )
            if not has_element and not has_pixel:
                raise ValueError(
                    f"{action}: requires a target — element id or x+y pixels",
                )
        elif action == _DRAG:
            self._validate_drag(has_element, has_pixel)
        else:
            # capture / type / key carry NO target.
            if has_element or has_pixel:
                raise ValueError(f"{action}: does not take a screen target")

        self._validate_payload(action)
        return self

    def _validate_drag(self, has_element: bool, has_pixel: bool) -> None:
        if has_element and has_pixel:
            raise ValueError("drag: source is EITHER element OR x+y, not both")
        if not has_element and not has_pixel:
            raise ValueError("drag: requires a source — element id or x+y pixels")
        has_to_element = self.to_element is not None
        has_to_pixel = self._has_pixel(self.to_x, self.to_y)
        if has_to_element and has_to_pixel:
            raise ValueError("drag: destination is EITHER to_element OR to_x+to_y, not both")
        if not has_to_element and not has_to_pixel:
            raise ValueError("drag: requires a destination — to_element or to_x+to_y")

    def _validate_payload(self, action: str) -> None:
        if action == "type" and not self.text:
            raise ValueError("type: requires non-empty text")
        if action == "key" and not self.keys:
            raise ValueError("key: requires a non-empty keys combo")
        if action == "set_value" and self.value is None:
            raise ValueError("set_value: requires a value")
        if action == "scroll" and self.direction is None:
            raise ValueError("scroll: requires a direction")
