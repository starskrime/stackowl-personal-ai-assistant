# Plan C — Telegram Slash-Command Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Telegram recognize "/" commands — show the native command menu/autocomplete and parse `/cmd@botname` correctly in groups.

**Architecture:** On bot start, register the `CommandRegistry`'s commands with Telegram via `set_my_commands(...)` so the client shows the slash menu. Keep the existing scanner-regex execution path (it already routes typed commands to `CommandRegistry.dispatch`). Add a small group-chat fix so `/cmd@botname` doesn't leak `@botname` into args. Fixes RC-D. (The dead `TelegramSlashCommandBridge` is left untouched here; a separate cleanup task may delete it.)

**Tech Stack:** python-telegram-bot (PTB), pytest, asyncio.

**BMad boundaries honored:** `channels/telegram/` owns transport/registration; reuses the shared `CommandRegistry` (no command logic duplicated). B5 (every catch logs).

---

## Root-cause recap (confirmed)

- No `set_my_commands`/`BotCommand`/`CommandHandler` anywhere → Telegram client shows no slash menu/autocomplete.
- `TelegramSlashCommandBridge` is dead code (never imported).
- Typed commands DO execute via `gateway/scanner.py` regex `^/(\w+)` → `_deliver_command_stub` → `CommandRegistry.dispatch`, but only if spelled exactly.
- In groups, `/cmd@botname` parses the command but leaks `@botname` into args (`helpers.strip_bot_mention` only strips a *leading* `@bot`).

---

## File Structure

- Create: `src/stackowl/channels/telegram/commands_registration.py` — builds the PTB `BotCommand` list from `CommandRegistry` and registers it. One responsibility, unit-testable without a live bot.
- Modify: `src/stackowl/channels/telegram/adapter.py` — call registration in `start()`.
- Modify: `src/stackowl/channels/telegram/helpers.py` — strip a `@botname` suffix from a leading `/cmd@botname` token.
- Test: `tests/channels/telegram/test_plan_c_command_registration.py`, `tests/channels/telegram/test_plan_c_group_suffix.py`.

---

### Task 0: Confirm `SlashCommand` attribute names

- [ ] **Step 1:** Read `src/stackowl/commands/base.py`. Confirm the public attributes are `.command` (name) and `.description`. If the description attribute has a different name (e.g. `.help`/`.summary`), use that name everywhere `description` appears below. The registry key is `command.command` (registry.py:32), so `.command` is certain.

---

### Task 1: Build the PTB BotCommand list from CommandRegistry

**Files:**
- Create: `src/stackowl/channels/telegram/commands_registration.py`
- Test: `tests/channels/telegram/test_plan_c_command_registration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/telegram/test_plan_c_command_registration.py
from stackowl.channels.telegram.commands_registration import build_bot_commands


class _Cmd:
    def __init__(self, command, description):
        self.command = command
        self.description = description


def test_build_bot_commands_sanitizes_names():
    cmds = [_Cmd("Help", "Show help"), _Cmd("co$t", "Show cost"),
            _Cmd("a_very_long_command_name_exceeding_thirty_two", "x")]
    out = build_bot_commands(cmds)
    names = [c.command for c in out]
    assert "help" in names                    # lowercased
    assert all(all(ch.isalnum() or ch == "_" for ch in n) for n in names)  # [a-z0-9_]
    assert all(1 <= len(n) <= 32 for n in names)  # Telegram length bound
    assert all(c.description for c in out)        # description required, non-empty


def test_build_bot_commands_truncates_description():
    out = build_bot_commands([_Cmd("ok", "d" * 300)])
    assert len(out[0].description) <= 256          # Telegram description bound
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_plan_c_command_registration.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the builder + registrar**

```python
# src/stackowl/channels/telegram/commands_registration.py
"""Register StackOwl slash commands with Telegram so the client shows the menu.

RC-D fix: without set_my_commands, Telegram never learns the bot's command list,
so "/" shows no autocomplete. We translate the shared CommandRegistry into PTB
BotCommand objects (respecting Telegram's name/description constraints) and push
them on bot start.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from telegram import BotCommand

_NAME_RE = re.compile(r"[^a-z0-9_]")
_MAX_NAME = 32
_MAX_DESC = 256


def build_bot_commands(commands: list[Any]) -> list["BotCommand"]:
    """Translate SlashCommand objects to Telegram BotCommand, enforcing TG limits.

    Telegram requires command names to be lowercase 1-32 chars of [a-z0-9_] and
    descriptions 1-256 chars. Invalid names are sanitized; empty results dropped.
    """
    from telegram import BotCommand

    out: list[BotCommand] = []
    for c in commands:
        name = _NAME_RE.sub("", str(c.command).lower())[:_MAX_NAME]
        if not name:
            log.telegram.warning(
                "[telegram] commands: dropped uncoercible command name",
                extra={"_fields": {"raw": str(c.command)}},
            )
            continue
        desc = (str(getattr(c, "description", "")) or name)[:_MAX_DESC]
        out.append(BotCommand(name, desc))
    return out


async def register_commands(bot: Any, commands: list[Any]) -> None:
    """Push the command menu to Telegram. Never raises (best-effort, B5-logged)."""
    bot_commands = build_bot_commands(commands)
    if not bot_commands:
        log.telegram.warning("[telegram] commands: nothing to register")
        return
    try:
        await bot.set_my_commands(bot_commands)
        log.telegram.info(
            "[telegram] commands: registered",
            extra={"_fields": {"count": len(bot_commands)}},
        )
    except Exception as exc:  # B5 — registration failure must not block startup
        log.telegram.error(
            "[telegram] commands: set_my_commands failed",
            exc_info=exc, extra={"_fields": {"count": len(bot_commands)}},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/channels/telegram/test_plan_c_command_registration.py -v`
Expected: PASS (2 tests). (Requires `python-telegram-bot` installed — it already is for the adapter.)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/channels/telegram/commands_registration.py tests/channels/telegram/test_plan_c_command_registration.py
git commit -m "feat(v2): build Telegram BotCommand list from CommandRegistry (RC-D)"
```

---

### Task 2: Register commands on bot start

**Files:**
- Modify: `src/stackowl/channels/telegram/adapter.py`
- Test: `tests/channels/telegram/test_plan_c_command_registration.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/channels/telegram/test_plan_c_command_registration.py
import pytest


@pytest.mark.asyncio
async def test_register_commands_calls_set_my_commands():
    class _Bot:
        def __init__(self): self.pushed = None
        async def set_my_commands(self, cmds): self.pushed = cmds
    from stackowl.channels.telegram.commands_registration import register_commands
    bot = _Bot()
    await register_commands(bot, [_Cmd("help", "Show help")])
    assert bot.pushed and bot.pushed[0].command == "help"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_plan_c_command_registration.py -k set_my_commands -v`
Expected: FAIL initially only if Task 1 incomplete; otherwise this passes and the *integration* in adapter is what we add next. (This pins the registrar contract.)

- [ ] **Step 3: Call registration from `start()`**

In `src/stackowl/channels/telegram/adapter.py` `start()` (after `self.register_with_registry()`, line ~88), obtain the shared command registry the orchestrator already uses and register:

```python
        # RC-D: publish the slash-command menu to Telegram so "/" autocompletes.
        from stackowl.channels.telegram.commands_registration import register_commands
        from stackowl.commands.registry import get_command_registry  # confirm accessor name

        try:
            registry = get_command_registry()
            await register_commands(app.bot, registry.list())
        except Exception as exc:  # B5 — never block startup on menu registration
            log.telegram.error(
                "[telegram] adapter.start: command registration failed",
                exc_info=exc,
            )
```

> Implementer: confirm the registry accessor. The orchestrator dispatches via a `CommandRegistry` instance (grep `registry.dispatch` / `CommandRegistry(` / `get_command_registry` / a module singleton in `commands/registry.py`). Use the SAME instance that `load_builtin_commands()` populates, so the menu matches what `dispatch` can actually run. If the accessor is a module-global, import that; do not construct a fresh empty registry.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/channels/telegram/test_plan_c_command_registration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/channels/telegram/adapter.py tests/channels/telegram/test_plan_c_command_registration.py
git commit -m "feat(v2): register slash-command menu with Telegram on start (RC-D)"
```

---

### Task 3: Strip `@botname` suffix from `/cmd@botname` in groups

**Files:**
- Modify: `src/stackowl/channels/telegram/helpers.py`
- Test: `tests/channels/telegram/test_plan_c_group_suffix.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/channels/telegram/test_plan_c_group_suffix.py
from stackowl.channels.telegram.helpers import strip_command_bot_suffix


def test_strips_bot_suffix_from_command():
    assert strip_command_bot_suffix("/help@StackOwlBot", "StackOwlBot") == "/help"
    assert strip_command_bot_suffix("/cost@StackOwlBot 30d", "StackOwlBot") == "/cost 30d"


def test_leaves_non_command_text_untouched():
    assert strip_command_bot_suffix("email me at a@StackOwlBot", "StackOwlBot") == "email me at a@StackOwlBot"
    assert strip_command_bot_suffix("/help", "StackOwlBot") == "/help"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/channels/telegram/test_plan_c_group_suffix.py -v`
Expected: FAIL — `strip_command_bot_suffix` not defined.

- [ ] **Step 3: Add the helper + apply it before enqueue**

In `src/stackowl/channels/telegram/helpers.py`, add:

```python
def strip_command_bot_suffix(text: str, bot_username: str | None) -> str:
    """Turn a leading "/cmd@BotName" into "/cmd" (Telegram group convention).

    Only touches a command token at the very start of the message; ordinary text
    containing "@BotName" is left alone.
    """
    if not bot_username or not text.startswith("/"):
        return text
    head, sep, rest = text.partition(" ")
    suffix = f"@{bot_username}"
    if head.endswith(suffix):
        head = head[: -len(suffix)]
    return head + sep + rest
```

In `src/stackowl/channels/telegram/adapter.py` `_handle_update` (where `stripped` is computed before building `IngressMessage`, around line 380), apply the suffix strip:

```python
        stripped = strip_command_bot_suffix(stripped, self._bot_username)
```

(Ensure `strip_command_bot_suffix` is imported from `.helpers`, alongside the existing `strip_bot_mention` import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/channels/telegram/test_plan_c_group_suffix.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/channels/telegram/helpers.py src/stackowl/channels/telegram/adapter.py tests/channels/telegram/test_plan_c_group_suffix.py
git commit -m "fix(v2): strip @botname suffix from group slash commands (RC-D)"
```

---

### Task 4: Lint, type-check, smoke

- [ ] **Step 1:** `uv run ruff check src/stackowl/channels/telegram && uv run mypy src/stackowl/channels/telegram/commands_registration.py` → clean.
- [ ] **Step 2:** Targeted tests: `uv run pytest tests/channels/telegram/test_plan_c_command_registration.py tests/channels/telegram/test_plan_c_group_suffix.py -v --timeout=120` → PASS.
- [ ] **Step 3: Manual smoke** — start serve with a real bot token, open the Telegram chat, type "/" → confirm the command menu appears; run `/help` and `/help@YourBot` in a group → both execute. (Per project rule: implement → QA agent → party-mode → smoke.)
- [ ] **Step 4: Optional cleanup task (separate commit)** — delete the dead `src/stackowl/channels/telegram/slash_bridge.py` if no test imports it (`grep -rn TelegramSlashCommandBridge`), per the "never disable features without asking" rule confirm it is genuinely unused first.

---

## Self-Review

- **Spec coverage:** No menu → Tasks 1-2 (`set_my_commands`). Group suffix leak → Task 3. Dead bridge → Task 4 optional cleanup (gated on confirmation). ✓
- **Type consistency:** `build_bot_commands` consumes objects with `.command`/`.description` (confirmed in Task 0); produces PTB `BotCommand(name, desc)`. `strip_command_bot_suffix(text, bot_username)` signature consistent across helper + adapter call. ✓
- **Placeholders:** Two explicit "confirm accessor/attribute" steps (Task 0 attribute names; Task 2 registry accessor) name the exact expected shape and pin contracts with tests — interface confirmations, not deferred work. ✓
