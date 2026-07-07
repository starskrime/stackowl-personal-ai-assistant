"""CommandResponse/Action — the interactive-button data model shared by every
channel renderer (Telegram inline keyboards, TUI Button widgets).

A command opts in by returning ``CommandResponse(text, actions=(...))``
instead of a bare ``str``. ``CommandRegistry.dispatch`` normalizes a bare
``str`` return so every existing command keeps working with zero changes.

Tapping an ``Action`` replays its ``command`` string through the EXACT same
``CommandRegistry.dispatch`` path a typed slash command uses — no new
execution path, no new bug class. ``destructive=True`` actions are never
dispatched directly by a renderer on first tap: :func:`make_confirm_response`
builds a second-tap [Yes][Cancel] prompt first (see channel renderers in
Plan C Tasks 3/4 for where this gets called).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

CANCEL_SENTINEL = "__cancel__"


class Action(BaseModel, frozen=True):
    """One tappable button. ``command`` is the exact slash-command string a
    tap replays — never a structured payload, so there is nothing to parse
    beyond ordinary command dispatch."""

    model_config = ConfigDict(extra="forbid")

    label: str
    command: str
    destructive: bool = False


class CommandResponse(BaseModel, frozen=True):
    """A command's full response: display text plus zero or more actions."""

    model_config = ConfigDict(extra="forbid")

    text: str
    actions: tuple[Action, ...] = ()


def make_confirm_response(action: Action) -> CommandResponse:
    """Build the two-tap confirm prompt for a destructive action.

    The ``Yes`` button replays the SAME command with ``destructive`` cleared
    (so the second tap actually dispatches instead of re-confirming forever).
    ``Cancel`` carries the well-known :data:`CANCEL_SENTINEL` — channel
    renderers special-case this instead of dispatching it as a command.
    """
    return CommandResponse(
        text=f"Confirm: {action.label}?",
        actions=(
            Action(label=f"Yes, {action.label}", command=action.command, destructive=False),
            Action(label="Cancel", command=CANCEL_SENTINEL, destructive=False),
        ),
    )
