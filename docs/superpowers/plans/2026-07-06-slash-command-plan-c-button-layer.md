# Slash-command Plan C: Interactive Button Layer (TUI + Telegram) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any slash command attach tap-to-act buttons to its response — rendered as real Textual buttons in the TUI and Telegram inline keyboards — where tapping replays the exact command string through the existing dispatch path. No new execution path.

**Architecture:** Telegram already has a complete, working callback-button subsystem (`InlineKeyboardBuilder`, `CallbackRouter` prefix-dispatch, `TelegramClarifyResolver` as a working example, all wired in `orchestrator.py`) — built for the clarify/consent/voice-confirm flows. This plan adds ONE new prefix handler (`TelegramCommandButtonResolver`, prefix `"cmd:"`) to the SAME router, rather than building new plumbing. `CommandRegistry.dispatch()` starts returning a `CommandResponse` (text + actions) instead of a bare `str`; the shared `_deliver_command_stub` closure in `orchestrator.py` (the one chokepoint every channel already dispatches slash commands through) carries `.actions` through the existing `ResponseChunk` model to both TUI and Telegram. Telegram's chat_id IS its session_id for private chats (`session_id == str(chat_id)`, confirmed in `adapter.py`'s `resolve_target` docstring) — button taps need no separate session-resolution mechanism.

**Tech Stack:** Python 3.13, Textual (TUI), python-telegram-bot (Telegram), pydantic (all new types are frozen `BaseModel`s to match `ResponseChunk`'s existing style).

**Depends on:** none of Plan A/B's specific changes, but commands gain `actions=` incrementally starting with `/webhook`/`/provider` (Plan B) — this plan can land before or after B; either order works since `CommandResponse` normalizes a bare `str` automatically.

## Global Constraints

- Run tests with `uv run pytest <path>` — never the full suite.
- `uv run ruff check src/` and `uv run mypy src/` clean on touched files.
- Every existing `SlashCommand` subclass must keep working with ZERO changes — `str` returns auto-wrap into `CommandResponse(text=str, actions=())`.
- No secret/token ever appears in a button `label` or `command` string (buttons replay visible slash-command text, same rule as any logged command string).
- 4-point logging on every new `execute()`/`handle()`-style method.
- Telegram `callback_data` never exceeds 64 bytes — enforced by always going through the id-map (see Task 3), never embedding a command string directly.

---

### Task 1: `CommandResponse`/`Action` types + dispatch normalization

**Files:**
- Create: `src/stackowl/commands/response.py`
- Modify: `src/stackowl/commands/base.py` (`SlashCommand.handle` return-type annotation)
- Modify: `src/stackowl/commands/registry.py` (`CommandRegistry.dispatch` normalizes)
- Test: new `tests/commands/test_response.py`, `tests/commands/test_registry_response_normalization.py`

**Interfaces:**
- Produces: `stackowl.commands.response.Action(label: str, command: str, destructive: bool = False)` (frozen `BaseModel`). `stackowl.commands.response.CommandResponse(text: str, actions: tuple[Action, ...] = ())` (frozen `BaseModel`). `stackowl.commands.response.CANCEL_SENTINEL: str = "__cancel__"`. `stackowl.commands.response.make_confirm_response(action: Action) -> CommandResponse` — builds the `[Yes, <label>][Cancel]` two-button prompt for a destructive action, reused identically by both channel renderers in Tasks 3/4.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# tests/commands/test_response.py
from __future__ import annotations

from stackowl.commands.response import CANCEL_SENTINEL, Action, CommandResponse, make_confirm_response


def test_command_response_defaults_to_no_actions():
    resp = CommandResponse(text="hello")
    assert resp.actions == ()


def test_action_destructive_defaults_false():
    action = Action(label="Remove", command="/provider remove acme")
    assert action.destructive is False


def test_make_confirm_response_builds_yes_cancel():
    action = Action(label="Remove", command="/provider remove acme", destructive=True)
    confirm = make_confirm_response(action)

    assert len(confirm.actions) == 2
    yes, cancel = confirm.actions
    assert yes.command == "/provider remove acme"
    assert yes.destructive is False  # tapping Yes must execute, not re-confirm
    assert cancel.command == CANCEL_SENTINEL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/commands/test_response.py -v`
Expected: FAIL — `stackowl.commands.response` doesn't exist yet.

- [ ] **Step 3: Implement `response.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/commands/test_response.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing dispatch-normalization test**

```python
# tests/commands/test_registry_response_normalization.py
from __future__ import annotations

import pytest

from stackowl.commands.base import SlashCommand
from stackowl.commands.metadata import CommandMeta
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import Action, CommandResponse
from tests._story_6_7_helpers import make_state


class _PlainStringCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "plainstr"

    @property
    def description(self) -> str:
        return "returns a bare str"

    async def handle(self, args: str, state) -> str:
        return "hello world"


class _ButtonCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "withbuttons"

    @property
    def description(self) -> str:
        return "returns a CommandResponse"

    async def handle(self, args: str, state) -> CommandResponse:
        return CommandResponse(
            text="pick one",
            actions=(Action(label="Go", command="/plainstr"),),
        )


@pytest.fixture(autouse=True)
def _reset_registry():
    CommandRegistry.reset()


async def test_dispatch_normalizes_bare_str_to_command_response():
    registry = CommandRegistry.instance()
    registry.register(_PlainStringCommand())

    result = await registry.dispatch("plainstr", "", make_state())

    assert isinstance(result, CommandResponse)
    assert result.text == "hello world"
    assert result.actions == ()


async def test_dispatch_passes_through_command_response_untouched():
    registry = CommandRegistry.instance()
    registry.register(_ButtonCommand())

    result = await registry.dispatch("withbuttons", "", make_state())

    assert isinstance(result, CommandResponse)
    assert result.text == "pick one"
    assert len(result.actions) == 1
    assert result.actions[0].command == "/plainstr"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/commands/test_registry_response_normalization.py -v`
Expected: FAIL — `dispatch` still returns a bare `str`/whatever the handler returned, `isinstance(result, CommandResponse)` fails for the plain-string case.

- [ ] **Step 7: Implement**

In `src/stackowl/commands/base.py`, change the abstract method signature:

```python
    @abstractmethod
    async def handle(self, args: str, state: PipelineState) -> str | CommandResponse:
        """Execute and return a response string, or a CommandResponse with
        tappable follow-up actions."""
        ...
```

Add the import at the top: `from stackowl.commands.response import CommandResponse`.

In `src/stackowl/commands/registry.py`, change `dispatch`'s return type and add normalization at the end:

```python
    async def dispatch(self, name: str, args: str, state: PipelineState) -> CommandResponse:
        if name not in self._commands:
            raise CommandNotFoundError(name)
        cmd = self._commands[name]

        is_dry_run, cleaned = strip_sigil(args)
        if is_dry_run:
            log.gateway.debug(
                "[commands] registry.dispatch: dry-run preview (handler NOT run)",
                extra={"_fields": {"command": name}},
            )
            return CommandResponse(text=build_preview(name, cmd, cleaned))

        log.gateway.debug(
            "[commands] registry.dispatch: dispatching",
            extra={"_fields": {"command": name, "args_len": len(args)}},
        )
        result = await cmd.handle(args, state)
        if isinstance(result, CommandResponse):
            return result
        return CommandResponse(text=result)
```

Add the import: `from stackowl.commands.response import CommandResponse`.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_registry_response_normalization.py tests/commands/test_response.py -v`
Expected: all PASS.

- [ ] **Step 9: Run the FULL existing command test surface to confirm zero breakage**

Run: `uv run pytest tests/commands/ tests/journeys/commands/ -v`
Expected: every existing test either already asserted a `str` (now needs `.text` — see Step 10) or asserted membership/substring on the dispatch result directly. This is the one place this plan touches every existing command test file.

- [ ] **Step 10: Fix the fallout — existing tests calling `registry.dispatch(...)` and asserting on the string result**

Every test asserting e.g. `"✓" in result` or `result == "..."` against `await registry.dispatch(...)` now receives a `CommandResponse`, not a `str`. Grep to find every call site:

```bash
grep -rln "await.*registry.*dispatch\|await.*\.dispatch(" tests/ --include="*.py" | grep -v pycache
```

For each hit, change `result = await registry.dispatch(...)` to `result = (await registry.dispatch(...)).text` — a single-line fix per call site, since none of the pre-existing tests care about `.actions` (this plan is what introduces actions in the first place). Run the full `tests/commands/` + `tests/journeys/commands/` suite again after each batch of fixes.

Also check every place `CommandRegistry.get(name).handle(...)` is called DIRECTLY in a test (bypassing `dispatch`) — those calls still return whatever the handler itself returns (`str` unless the command was touched by Plan B/D to return `CommandResponse`), so they do NOT need the `.text` fix — only fix `dispatch(...)` call sites.

- [ ] **Step 11: Re-run full command test surface**

Run: `uv run pytest tests/commands/ tests/journeys/commands/ -v`
Expected: all PASS.

- [ ] **Step 12: Commit**

```bash
git add src/stackowl/commands/response.py src/stackowl/commands/base.py src/stackowl/commands/registry.py tests/commands/test_response.py tests/commands/test_registry_response_normalization.py
git commit -m "feat(commands): CommandResponse/Action types, dispatch normalizes bare str"
```

---

### Task 2: Carry `actions` through `ResponseChunk` and `_deliver_command_stub`

**Files:**
- Modify: `src/stackowl/pipeline/streaming.py` (`ResponseChunk` — add `actions` field)
- Modify: `src/stackowl/startup/orchestrator.py` (`_deliver_command_stub`, ~line 1529-1573)
- Test: `tests/pipeline/test_streaming.py` (or wherever `ResponseChunk` is already tested — check with `find tests -iname "*streaming*"`), new assertions in whatever test currently covers `_deliver_command_stub` (search `grep -rln "_deliver_command_stub\|deliver_command" tests/`)

**Interfaces:**
- Consumes: `stackowl.commands.response.{Action, CommandResponse}` from Task 1.
- Produces: `ResponseChunk.actions: tuple[Action, ...] = ()` — new optional field, every existing `ResponseChunk(...)` construction site keeps working unchanged (default empty tuple).

- [ ] **Step 1: Write the failing test**

```python
def test_response_chunk_actions_defaults_empty():
    from stackowl.pipeline.streaming import ResponseChunk

    chunk = ResponseChunk(content="hi", is_final=True, chunk_index=0, trace_id="t1", owl_name="system")
    assert chunk.actions == ()


def test_response_chunk_carries_actions():
    from stackowl.commands.response import Action
    from stackowl.pipeline.streaming import ResponseChunk

    chunk = ResponseChunk(
        content="pick one", is_final=True, chunk_index=0, trace_id="t1", owl_name="system",
        actions=(Action(label="Go", command="/help"),),
    )
    assert len(chunk.actions) == 1
```

Add these to whichever existing streaming test file already covers `ResponseChunk` construction.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_streaming.py -v -k actions` (adjust path to the actual file found)
Expected: FAIL — `ResponseChunk` has no `actions` field yet.

- [ ] **Step 3: Implement — add the field**

In `src/stackowl/pipeline/streaming.py`, add the import and field:

```python
from stackowl.commands.response import Action
```

```python
    # Tappable follow-up actions from a slash-command CommandResponse. Empty
    # for every ordinary LLM-answer chunk — only slash-command replies ever
    # populate this (see startup/orchestrator.py::_deliver_command_stub).
    actions: tuple[Action, ...] = ()
```

(Add this field after `is_floor` at the end of the class.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_streaming.py -v -k actions`
Expected: PASS.

- [ ] **Step 5: Update `_deliver_command_stub` to carry `.text`/`.actions`**

In `src/stackowl/startup/orchestrator.py`, `_deliver_command_stub` currently does:

```python
            try:
                reply = await registry.dispatch(cmd, args, state)
                dispatched_ok = True
            except CommandNotFoundError:
                reply = f"Unknown slash command: '/{cmd}'. Try /help to see what's available."
                ...
                    if hits:
                        reply += "\n\nDid you mean:\n" + "\n".join(f"  {h}" for h in hits)
                ...
            except Exception as exc:
                log.error("[startup] gateway: slash command failed", exc_info=exc)
                reply = f"Command '/{cmd}' failed: {exc}"
```

and later:

```python
            if writer is not None:
                await writer.write(ResponseChunk(
                    content=reply, is_final=False, chunk_index=0,
                    trace_id=trace_id, owl_name="system",
                ))
                await writer.close()
```

Change so `reply` is always a `CommandResponse` (import `CommandResponse` from `stackowl.commands.response`):

```python
            try:
                reply = await registry.dispatch(cmd, args, state)
                dispatched_ok = True
            except CommandNotFoundError:
                text = f"Unknown slash command: '/{cmd}'. Try /help to see what's available."
                try:
                    from stackowl.commands.resolver import suggest_invocations
                    hits = await suggest_invocations(
                        f"{cmd} {args}".strip(), registry.list(), limit=3
                    )
                    if hits:
                        text += "\n\nDid you mean:\n" + "\n".join(f"  {h}" for h in hits)
                except Exception as exc:
                    log.debug("[startup] gateway: command suggestion failed", exc_info=exc)
                reply = CommandResponse(text=text)
            except Exception as exc:
                log.error("[startup] gateway: slash command failed", exc_info=exc)
                reply = CommandResponse(text=f"Command '/{cmd}' failed: {exc}")
```

and:

```python
            if writer is not None:
                await writer.write(ResponseChunk(
                    content=reply.text, is_final=False, chunk_index=0,
                    trace_id=trace_id, owl_name="system",
                    actions=reply.actions,
                ))
                await writer.close()
```

The `sequence_store` learning block a few lines below references `cmd_obj.meta` and `args`, not `reply` — unaffected, no change needed there.

- [ ] **Step 6: Run the startup test surface covering command dispatch**

Run: `grep -rln "_deliver_command_stub\|deliver_command" tests/ --include="*.py" | grep -v pycache` to find the covering test file(s), then:

```bash
uv run pytest <that file> -v
```
Expected: all PASS (fix any assertion still expecting a bare `str` from the writer's chunk, same `.text` fix pattern as Task 1 Step 10).

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/pipeline/streaming.py src/stackowl/startup/orchestrator.py
git commit -m "feat(commands): carry CommandResponse.actions through ResponseChunk"
```

---

### Task 3: Telegram — render buttons, resolve taps

**Files:**
- Create: `src/stackowl/channels/telegram/command_buttons.py`
- Modify: `src/stackowl/channels/telegram/adapter.py` (`send()` — build a keyboard when a chunk carries `actions`)
- Modify: `src/stackowl/startup/orchestrator.py` (register the new resolver on `tg_callback_router`, near line 2484)
- Test: new `tests/channels/telegram/test_command_buttons.py`

**Interfaces:**
- Consumes: `Action`/`CommandResponse` (Task 1), `InlineKeyboardBuilder` (existing, `channels/telegram/keyboard.py`), `CallbackRouter.register(prefix, handler)` (existing, `channels/telegram/callbacks.py`), `TelegramChannelAdapter.{send_inline_keyboard, edit_message, send_text}` (all already exist).
- Produces: `TelegramCommandButtonResolver` class with `handle_callback(callback_id: str, callback_data: str) -> None` — same shape as `TelegramClarifyResolver.handle_callback`, registered under prefix `"cmd:"`. `register_command_button(chat_id: int, action: Action) -> str` — stores `(chat_id, action)` in an in-memory TTL map, returns the short `callback_data` string (`"cmd:{short_id}"`) to embed in the keyboard.

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/telegram/test_command_buttons.py
from __future__ import annotations

import pytest

from stackowl.channels.telegram.command_buttons import (
    TelegramCommandButtonResolver,
    register_command_button,
)
from stackowl.commands.response import CANCEL_SENTINEL, Action


def test_register_command_button_returns_short_prefixed_id():
    action = Action(label="Remove", command="/provider remove acme")
    data = register_command_button(chat_id=123, action=action)

    assert data.startswith("cmd:")
    assert len(data.encode()) <= 64


async def test_resolver_dispatches_non_destructive_action(monkeypatch):
    from stackowl.commands.response import CommandResponse

    dispatched = {}

    class _FakeRegistry:
        async def dispatch(self, name, args, state):
            dispatched["name"] = name
            dispatched["args"] = args
            dispatched["session_id"] = state.session_id
            return CommandResponse(text="✓ removed")

    class _FakeAdapter:
        sent = []

        async def send_text(self, text, *, chat_id=None):
            self.sent.append((chat_id, text))

    adapter = _FakeAdapter()
    registry = _FakeRegistry()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=registry)

    action = Action(label="Remove", command="/provider remove acme", destructive=False)
    data = register_command_button(chat_id=555, action=action)

    await resolver.handle_callback("cbid1", data)

    assert dispatched["name"] == "provider"
    assert dispatched["args"] == "remove acme"
    assert dispatched["session_id"] == "555"
    assert adapter.sent == [(555, "✓ removed")]


async def test_resolver_shows_confirm_prompt_for_destructive_action_first_tap():
    class _FakeAdapter:
        edited = []

        async def edit_message(self, *, chat_id, message_id, text, reply_markup=None):
            self.edited.append((chat_id, text))

    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=None)

    action = Action(label="Remove", command="/provider remove acme", destructive=True)
    data = register_command_button(chat_id=555, action=action)

    await resolver.handle_callback("cbid2", data)

    assert any("Confirm" in text for _cid, text in adapter.edited)


async def test_resolver_cancel_sentinel_shows_cancelled_text():
    class _FakeAdapter:
        edited = []

        async def edit_message(self, *, chat_id, message_id, text, reply_markup=None):
            self.edited.append((chat_id, text))

    adapter = _FakeAdapter()
    resolver = TelegramCommandButtonResolver(adapter=adapter, registry=None)

    action = Action(label="Cancel", command=CANCEL_SENTINEL)
    data = register_command_button(chat_id=555, action=action)

    await resolver.handle_callback("cbid3", data)

    assert any("Cancel" in text or "cancelled" in text.lower() for _cid, text in adapter.edited)
```

(This test asserts a signature for `TelegramChannelAdapter.edit_message` — confirm its actual keyword names in `adapter.py:887` before finalizing; adjust the fake's signature and the resolver's call to match exactly rather than guessing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_command_buttons.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `command_buttons.py`**

```python
"""Telegram command-button registry + callback resolver.

Mirrors ``channels/telegram/clarify.py``'s shape (a dedicated resolver class
registered on the shared CallbackRouter by prefix) but for CommandResponse
actions rather than parked clarify choices.

Telegram's chat_id IS the session_id for private chats (confirmed in
TelegramChannelAdapter.resolve_target's docstring: session_id == str(chat_id)
by convention) — no separate session lookup is needed.

callback_data is ALWAYS routed through the short-id map below, even when a
command string would fit under Telegram's 64-byte limit directly — the map
is also how chat_id travels with the tap (the router's handler signature is
(callback_id, callback_data) only, it does not carry the originating chat).
"""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Any

from stackowl.commands.response import CANCEL_SENTINEL, Action, make_confirm_response
from stackowl.infra.observability import log
from stackowl.pipeline.state import PipelineState

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.commands.registry import CommandRegistry

__all__ = ["TelegramCommandButtonResolver", "register_command_button"]

_CALLBACK_PREFIX = "cmd:"
_TTL_SECONDS = 15 * 60

# In-memory only (module-level) — a process restart drops any pending
# mapping; a very old unused button fails with a clear expired-message
# response rather than a silent no-op (see handle_callback).
_button_map: dict[str, tuple[int, Action, float]] = {}


def register_command_button(chat_id: int, action: Action) -> str:
    """Store (chat_id, action) under a fresh short id, return the callback_data."""
    short_id = secrets.token_urlsafe(6)
    _button_map[short_id] = (chat_id, action, time.monotonic() + _TTL_SECONDS)
    return f"{_CALLBACK_PREFIX}{short_id}"


def _pop_valid(short_id: str) -> tuple[int, Action] | None:
    entry = _button_map.pop(short_id, None)
    if entry is None:
        return None
    chat_id, action, expires_at = entry
    if time.monotonic() > expires_at:
        return None
    return chat_id, action


class TelegramCommandButtonResolver:
    """Resolves a tapped command-replay button (prefix ``cmd:``)."""

    def __init__(self, adapter: TelegramChannelAdapter, registry: CommandRegistry | None) -> None:
        self._adapter = adapter
        self._registry = registry

    async def handle_callback(self, callback_id: str, callback_data: str) -> None:
        log.telegram.debug(
            "[telegram] command_buttons.handle_callback: entry",
            extra={"_fields": {"data_len": len(callback_data)}},
        )
        if not callback_data.startswith(_CALLBACK_PREFIX):
            return
        short_id = callback_data[len(_CALLBACK_PREFIX):]
        resolved = _pop_valid(short_id)
        if resolved is None:
            log.telegram.info(
                "[telegram] command_buttons.handle_callback: expired or unknown button",
                extra={"_fields": {"short_id": short_id}},
            )
            return
        chat_id, action = resolved

        if action.command == CANCEL_SENTINEL:
            await self._adapter.edit_message(
                chat_id=chat_id, message_id=0, text="Cancelled."
            )
            return

        if action.destructive:
            confirm = make_confirm_response(action)
            keyboard = _build_keyboard(chat_id, confirm.actions)
            await self._adapter.edit_message(
                chat_id=chat_id, message_id=0, text=confirm.text, reply_markup=keyboard
            )
            return

        # Non-destructive (or already-confirmed) — actually dispatch.
        parts = action.command.lstrip("/").split(maxsplit=1)
        name = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        state = PipelineState(session_id=str(chat_id))
        assert self._registry is not None
        reply = await self._registry.dispatch(name, args, state)
        await self._adapter.send_text(reply.text, chat_id=chat_id)
        if reply.actions:
            keyboard = _build_keyboard(chat_id, reply.actions)
            await self._adapter.send_inline_keyboard(chat_id=chat_id, text="​", keyboard=keyboard)
        log.telegram.debug(
            "[telegram] command_buttons.handle_callback: exit",
            extra={"_fields": {"command": name}},
        )


def _build_keyboard(chat_id: int, actions: tuple[Action, ...]) -> dict[str, object]:
    from stackowl.channels.telegram.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    for action in actions:
        builder.add_button(action.label, register_command_button(chat_id, action))
    return builder.build()
```

Check `PipelineState`'s actual required fields (`pipeline/state.py`) before finalizing the `PipelineState(session_id=str(chat_id))` construction — if other fields are non-optional, use whatever minimal-construction helper the rest of the codebase already uses for a synthetic state (e.g. `tests/_story_6_7_helpers.py::make_state` shows the shape; production code needs its own equivalent — check if one already exists, e.g. in `interaction/` for building a `PipelineState` from a bare session_id, before hand-rolling one here).

Also confirm `TelegramChannelAdapter.send_inline_keyboard`'s and `edit_message`'s actual parameter names in `adapter.py` (lines 585 and 887 respectively, read during planning but not fully — match the real signatures exactly, this pseudocode's kwargs are best-effort from the method list, not a verified read of the full body).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/channels/telegram/test_command_buttons.py -v`
Expected: PASS (after reconciling the exact adapter method signatures per the notes above).

- [ ] **Step 5: Wire into `send()` — build a keyboard when a chunk carries actions**

In `adapter.py::send()` (the chunk consumer, ~line 321), find where a chunk's `content` is turned into an outgoing message (`_deliver`/`_send_part`) and add: when `chunk.actions` is non-empty, after sending the text, call `register_command_button` for each action and send an inline keyboard the same way `send_clarify` already does. Match the EXACT structure `send_clarify` uses (read `adapter.py:645-733` in full before writing this step's diff — it is the closest existing precedent for "send text + attach a keyboard, with a text-only fallback if keyboard construction fails").

- [ ] **Step 6: Wire the resolver into orchestrator.py**

Near the existing:

```python
                telegram_adapter.attach_callback_router(tg_callback_router)
```

(line ~2484), find where other resolvers (e.g. the clarify one) are `.register(...)`ed onto `tg_callback_router` and add:

```python
                from stackowl.channels.telegram.command_buttons import TelegramCommandButtonResolver

                command_button_resolver = TelegramCommandButtonResolver(
                    adapter=telegram_adapter, registry=CommandRegistry.instance()
                )
                tg_callback_router.register("cmd:", command_button_resolver.handle_callback)
```

placed alongside whatever line registers `TelegramClarifyResolver` (read the surrounding ~30 lines before `attach_callback_router` at 2484 to find the exact existing `.register("clarify", ...)` call and mirror its placement).

- [ ] **Step 7: Run the channel test surface**

Run: `uv run pytest tests/channels/telegram/ -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/channels/telegram/command_buttons.py src/stackowl/channels/telegram/adapter.py src/stackowl/startup/orchestrator.py tests/channels/telegram/test_command_buttons.py
git commit -m "feat(telegram): inline-keyboard buttons for CommandResponse actions"
```

---

### Task 4: TUI — render buttons, replay on press

**Files:**
- Modify: whichever widget currently renders a slash-command's reply in the transcript (locate via `grep -rn "ResponseChunk\|SystemMessage" src/stackowl/tui/ --include="*.py"` — read the full file before editing, this plan's research did not trace this widget in detail)
- Modify: `src/stackowl/tui/app.py` (if the button's replay needs to go through `on_compose_submitted_message` — confirm by reading `app.py` in full around that method first)
- Test: new test in whatever existing test file covers the transcript/system-message widget

**Interfaces:**
- Consumes: `stackowl.commands.response.Action`, `stackowl.tui.messages.compose.ComposeSubmittedMessage` (existing — confirmed at `tui/messages/compose.py:16`).
- Produces: for each `Action` in a rendered response, a Textual `Button` widget whose `on_press` posts `ComposeSubmittedMessage(text=action.command)` — reusing the EXACT existing path a typed command already goes through (`StackOwlApp.on_compose_submitted_message` at `app.py:253`, which republishes on the EventBus and echoes into the transcript). Destructive actions render via `make_confirm_response` first, same two-tap rule as Telegram (Task 3) — the confirm/cancel buttons are ordinary buttons in the same widget, no special-casing needed beyond what `make_confirm_response` already encodes.

- [ ] **Step 1: Locate the exact rendering site (read before writing any code)**

```bash
grep -rn "ResponseChunk\|class.*SystemMessage\|class.*TranscriptView\|class.*MessageWidget" src/stackowl/tui/ --include="*.py"
```

Read the full widget file(s) this turns up. Confirm: where does a `ResponseChunk`'s `.content` currently get turned into rendered text in the transcript? That is the exact spot needing a companion "if `.actions` is non-empty, mount a row of Button widgets below this message" branch.

- [ ] **Step 2: Write the failing test**

Once the widget is identified (call it `<Widget>` — replace with the real class name found in Step 1):

```python
async def test_command_response_actions_render_as_buttons():
    from stackowl.commands.response import Action
    from textual.widgets import Button

    widget = <Widget>(text="pick one", actions=(Action(label="Go", command="/help"),))
    # Mount in a minimal Textual App test harness (follow whatever pattern
    # existing TUI widget tests in this same directory already use for
    # mounting + querying children — do not invent a new harness).
    buttons = widget.query(Button)
    assert len(buttons) == 1
    assert buttons[0].label == "Go"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest <the test file> -v -k actions`
Expected: FAIL — no button rendering exists yet.

- [ ] **Step 4: Implement — add Button rendering + press handler**

In the identified widget, add a `Button` per `Action` (mounted below the message text), and an `on_press` (or `on_button_pressed`, per Textual's actual event name — confirm against how this codebase's OTHER buttons, if any exist elsewhere in the TUI, wire presses) that does:

```python
    def _on_action_button_pressed(self, action: Action) -> None:
        if action.destructive:
            from stackowl.commands.response import make_confirm_response
            confirm = make_confirm_response(action)
            self._replace_actions(confirm.actions)  # re-render this widget's button row in place
            return
        if action.command == CANCEL_SENTINEL:
            self._replace_actions(())
            return
        from stackowl.tui.messages.compose import ComposeSubmittedMessage
        self.post_message(ComposeSubmittedMessage(text=action.command))
```

(`_replace_actions` is a small helper this task adds to the widget — remove the current button row's children and mount a fresh one built from the new `actions` tuple, reusing the same button-construction code from Step 1/4 rather than duplicating it.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest <the test file> -v -k actions`
Expected: PASS.

- [ ] **Step 6: Manual verification (documented, not automated)**

Note for the human reviewer: run the TUI (`uv run python -m stackowl` with a TUI-capable terminal), dispatch a command that returns actions (once Plan B lands, `/webhook register` or `/provider list` after it gains actions), confirm buttons render, confirm tapping a non-destructive one submits the replayed command visibly in the transcript, confirm a destructive one shows the confirm row first.

- [ ] **Step 7: Commit**

```bash
git add -A src/stackowl/tui/
git commit -m "feat(tui): render CommandResponse actions as Button widgets"
```

---

### Task 5: Full-plan verification

- [ ] **Step 1: Run every test file touched in this plan**

```bash
uv run pytest tests/commands/ tests/journeys/commands/ tests/pipeline/test_streaming.py tests/channels/telegram/ tests/tui/ -v
```
Expected: all PASS.

- [ ] **Step 2: Lint + type-check**

```bash
uv run ruff check src/stackowl/commands/response.py src/stackowl/commands/base.py src/stackowl/commands/registry.py src/stackowl/pipeline/streaming.py src/stackowl/startup/orchestrator.py src/stackowl/channels/telegram/command_buttons.py src/stackowl/channels/telegram/adapter.py src/stackowl/tui/
uv run mypy src/stackowl/commands/response.py src/stackowl/commands/base.py src/stackowl/commands/registry.py src/stackowl/pipeline/streaming.py src/stackowl/startup/orchestrator.py src/stackowl/channels/telegram/command_buttons.py src/stackowl/channels/telegram/adapter.py src/stackowl/tui/
```
Expected: clean on both.

- [ ] **Step 3: Confirm no command lost its capability**

```bash
grep -rln "async def handle" src/stackowl/commands/*.py | xargs grep -l "-> str:"
```
Expected: every hit still returns `str` (unchanged existing commands) — none should now error at runtime, since `dispatch()` normalizes either return type. Spot-check 2-3 of these still dispatch correctly end to end via the existing per-command test files (already covered by Step 1's full run).
