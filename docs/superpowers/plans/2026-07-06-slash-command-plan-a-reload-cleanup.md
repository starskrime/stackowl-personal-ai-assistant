# Slash-command Plan A: Live Reload + Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/provider` and `/config` writes reload live instead of waiting on a background poll, retire the duplicate `/settings` command, fix `/focus`'s missing read path, remove the `/memory delete`/`forget` duplicate, and fix one dead help-text reference in `/browser`.

**Architecture:** `ConfigWatcher` is today the only thing that ever rebuilds a real `Settings()` object and emits it on `"settings_reloaded"` — on its own 5s poll timer. Commands that already write YAML (`/provider`, `/config`) instead emit a throwaway `dict` payload that the two live subscribers (`ProviderRegistry`, identity resolver) explicitly ignore (type-guarded to `Settings` only). The fix: after a command's own verified write, it builds a fresh `Settings()` itself and emits that as the `"settings_reloaded"` payload — same effect as `ConfigWatcher`, just immediate. This flows through the *existing* subscribers with zero new subscriber code.

**Tech Stack:** Python 3.13, pydantic-settings, pytest + pytest-asyncio, ruamel.yaml.

## Global Constraints

- Run tests with `uv run pytest <path>` — never the full suite (hangs on this box). Scope to the files touched.
- `uv run ruff check src/` and `uv run mypy src/` must stay clean on touched files.
- Never remove or weaken an existing capability without it being an exact, verified duplicate (per this plan's own audit) — `/settings` and `/memory delete` qualify; nothing else in this plan removes anything.
- 4-point logging (entry/decision/step/exit) on every modified `execute()`/`handle()`-style method — this repo's `CLAUDE.md` standard.
- Every new/modified log call uses the existing named logger for its module (`log.config`, `log.notifications`, `log.memory`, `log.gateway`), never a bare `logging.getLogger`.

---

### Task 1: Real-Settings-emit fix — `/provider`

**Files:**
- Modify: `src/stackowl/commands/provider_command.py:1-30` (imports), `:179-181` (`_emit_reloaded`)
- Test: `tests/commands/test_provider_command.py`

**Interfaces:**
- Consumes: `stackowl.config.settings.Settings` (constructor takes no args, reads `stackowl.yaml` + env — same call `ConfigWatcher._reload()` already makes via `settings_factory=lambda: Settings()`).
- Produces: `ProviderCommand._emit_reloaded(name: str) -> None` now emits a real `Settings` instance instead of `{"provider": name}` — every caller (`_add`, `_remove`, `_set_tier`) is unaffected, they just call `self._emit_reloaded(name)` as before.

- [ ] **Step 1: Write the failing test**

Add to `tests/commands/test_provider_command.py` (uses the existing `_SpyBus` fixture already defined at the top of that file):

```python
def test_provider_add_emits_real_settings_not_dict(tmp_path, monkeypatch):
    """After a verified /provider add write, settings_reloaded must carry a real
    Settings object (so the existing type-guarded subscribers actually apply it),
    not the old {"provider": name} dict which every subscriber ignores."""
    from stackowl.config.settings import Settings

    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setattr(
        "stackowl.commands.config_helpers.config_path", lambda: config_file
    )

    bus = _SpyBus()
    cmd = ProviderCommand(event_bus=bus)
    result = cmd._add("acme openai gpt-4o powerful")

    assert "✓" in result
    reload_events = [payload for event, payload in bus.events if event == "settings_reloaded"]
    assert len(reload_events) == 1
    assert isinstance(reload_events[0], Settings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/commands/test_provider_command.py::test_provider_add_emits_real_settings_not_dict -v`
Expected: FAIL — `assert isinstance({"provider": "acme"}, Settings)` is False.

- [ ] **Step 3: Implement**

In `provider_command.py`, add the import near the top (alongside the existing `from stackowl.config.provider import ProviderConfig`):

```python
from stackowl.config.settings import Settings
```

Replace `_emit_reloaded`:

```python
def _emit_reloaded(self, name: str) -> None:
    if self._bus is None:
        return
    try:
        new_settings = Settings()
    except Exception as exc:
        log.config.error(
            "[commands] provider._emit_reloaded: immediate reload failed — "
            "falling back to background ConfigWatcher poll",
            exc_info=exc,
            extra={"_fields": {"name": name}},
        )
        return
    self._bus.emit("settings_reloaded", new_settings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/commands/test_provider_command.py::test_provider_add_emits_real_settings_not_dict -v`
Expected: PASS

- [ ] **Step 5: Update the 3 existing assertions that check the old dict shape**

`tests/commands/test_provider_command.py` lines ~130, ~216, ~232, ~259 assert either `("settings_reloaded", {"provider": "acme"}) in [...]` or `any(e == "settings_reloaded" for e, _ in bus.events)`. The `any(e == ...)` forms need no change. The one exact-tuple assertion (`("settings_reloaded", {"provider": "acme"}) in [...]`) must become:

```python
from stackowl.config.settings import Settings
reload_events = [p for e, p in bus.events if e == "settings_reloaded"]
assert any(isinstance(p, Settings) for p in reload_events)
```

- [ ] **Step 6: Run the full file, drop the "next reload/restart" caveat from response text**

Run: `uv run pytest tests/commands/test_provider_command.py -v`
Expected: all PASS.

Then in `provider_command.py`, update the 3 response strings that say "applies on the next reload/restart" (in `_add`, `_remove`, `_set_tier`) to say `"— applied immediately"` instead, and remove the now-inaccurate docstring line `"NOTE: changes take effect on the next reload/restart..."` at the top of the file, replacing it with: `"NOTE: changes are applied immediately via an in-process settings_reloaded emit — see stackowl/startup/provider_reload.py for the consumer."`

- [ ] **Step 7: Re-run and commit**

Run: `uv run pytest tests/commands/test_provider_command.py tests/journeys/commands/test_provider_command_journey.py -v`
Expected: all PASS.

```bash
git add src/stackowl/commands/provider_command.py tests/commands/test_provider_command.py
git commit -m "fix(commands): /provider emits real Settings for immediate live reload"
```

---

### Task 2: Real-Settings-emit fix — `/config`

**Files:**
- Modify: `src/stackowl/commands/config_command.py` (the `_set` method's emit call, the `_reset` method's emit call)
- Test: `tests/journeys/commands/test_config_command.py`

**Interfaces:**
- Consumes: same `Settings()` constructor as Task 1.
- Produces: no signature change — `_set`/`_reset` still return `str`, only the emitted payload type changes.

- [ ] **Step 1: Write the failing test**

Add to `tests/journeys/commands/test_config_command.py` (check the top of that file for its existing bus test-double — reuse it; if it uses a plain `EventBus` with no spy, add a local `_SpyBus` identical in shape to the one in `tests/commands/test_provider_command.py`):

```python
async def test_config_set_emits_real_settings(tmp_path, monkeypatch):
    from stackowl.config.settings import Settings

    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setattr(
        "stackowl.commands.config_helpers.config_path", lambda: config_file
    )
    bus = _SpyBus()
    cmd = ConfigCommand(event_bus=bus)

    result = await cmd.handle("set autonomy_level high", make_state())

    assert "✓" in result
    reload_events = [p for e, p in bus.events if e == "settings_reloaded"]
    assert len(reload_events) == 1
    assert isinstance(reload_events[0], Settings)
```

(Use whatever `make_state()` helper the rest of that test file already imports — check its existing imports before writing this, they follow the same `tests/_story_6_7_helpers` pattern used in `test_memory_delete_prefix.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/journeys/commands/test_config_command.py::test_config_set_emits_real_settings -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `config_command.py::_set`, replace:

```python
        if self._bus is not None:
            self._bus.emit("settings_reloaded", {"key": key})
```

with:

```python
        if self._bus is not None:
            try:
                self._bus.emit("settings_reloaded", Settings())
            except Exception as exc:
                log.config.error(
                    "[commands] config.set: immediate reload failed — falling "
                    "back to background ConfigWatcher poll",
                    exc_info=exc,
                    extra={"_fields": {"key": key}},
                )
```

And in `_reset`, replace:

```python
        if self._bus is not None:
            self._bus.emit("settings_reloaded", {"key": key, "reset": True})
```

with the same pattern:

```python
        if self._bus is not None:
            try:
                self._bus.emit("settings_reloaded", Settings())
            except Exception as exc:
                log.config.error(
                    "[commands] config.reset: immediate reload failed — falling "
                    "back to background ConfigWatcher poll",
                    exc_info=exc,
                    extra={"_fields": {"key": key}},
                )
```

`Settings` is already imported at the top of this file (used for `Settings.model_validate(data)`), no new import needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/journeys/commands/test_config_command.py::test_config_set_emits_real_settings -v`
Expected: PASS.

- [ ] **Step 5: Run the full file, confirm the hot_reload suffix logic is untouched**

The existing line `suffix = "" if hot else " — restart required"` in `_set` stays — it's already correct per-field (a field marked `hot_reload=False` genuinely needs a restart even with an immediate `Settings()` rebuild, since some consumers only apply certain fields at process start). No change needed there.

Run: `uv run pytest tests/journeys/commands/test_config_command.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/config_command.py tests/journeys/commands/test_config_command.py
git commit -m "fix(commands): /config emits real Settings for immediate live reload"
```

---

### Task 3: Retire `/settings`

**Files:**
- Delete: `src/stackowl/commands/settings_command.py`
- Delete: `tests/journeys/commands/test_settings_command.py`
- Modify: `src/stackowl/commands/assembly.py:343-344` (remove registration)
- Modify: `src/stackowl/commands/manifest.py:26` (remove `"settings"` from `SHIPPED_COMMANDS`)
- Test: `tests/journeys/commands/test_all_29_reachable.py`, `tests/journeys/commands/test_reachability_guard.py`, `tests/journeys/commands/test_command_manifest_drift.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SHIPPED_COMMANDS` no longer contains `"settings"`. No other task in this plan depends on `/settings` existing.

- [ ] **Step 1: Confirm the reachability tests currently pass (baseline)**

Run: `uv run pytest tests/journeys/commands/test_all_29_reachable.py tests/journeys/commands/test_reachability_guard.py tests/journeys/commands/test_command_manifest_drift.py -v`
Expected: all PASS (current baseline, before removal).

- [ ] **Step 2: Remove the command file and its registration**

Delete `src/stackowl/commands/settings_command.py`.

In `src/stackowl/commands/assembly.py`, remove:

```python
    from stackowl.commands.settings_command import SettingsCommand
    _safe_register(registry, "settings", lambda: SettingsCommand(event_bus=deps.event_bus))
```

In `src/stackowl/commands/manifest.py`, remove the `"settings",` line from `SHIPPED_COMMANDS`.

Delete `tests/journeys/commands/test_settings_command.py` (it exclusively tests the retired command).

- [ ] **Step 3: Run the reachability + drift tests, expect PASS (they auto-adjust to the new set)**

Run: `uv run pytest tests/journeys/commands/test_all_29_reachable.py tests/journeys/commands/test_reachability_guard.py tests/journeys/commands/test_command_manifest_drift.py -v`
Expected: all PASS — these tests assert `registry.list() == SHIPPED_COMMANDS`, so removing both the registration and the manifest entry together keeps them in sync with no test-code changes needed.

- [ ] **Step 4: Grep for any other reference to `SettingsCommand` or the retired command string**

Run: `grep -rn "SettingsCommand\|/settings autonomy" src/ tests/ docs/ --include="*.py" --include="*.md"`
Expected: no hits outside this plan's own doc and the design spec. If any test/doc references survive, remove or update them (e.g. a `/help` snapshot test listing all commands).

- [ ] **Step 5: Commit**

```bash
git add -A src/stackowl/commands/settings_command.py src/stackowl/commands/assembly.py src/stackowl/commands/manifest.py tests/journeys/commands/test_settings_command.py
git commit -m "refactor(commands): retire /settings, fold into /config (exact duplicate)"
```

---

### Task 4: `/focus` bare-args read path

**Files:**
- Modify: `src/stackowl/commands/focus_command.py:67-106` (`handle`)
- Test: new test in `tests/commands/test_focus_meta.py` (existing file — check its current imports/fixtures before adding, follow its existing pattern)

**Interfaces:**
- Consumes: `NotificationRouter.get_focus_mode() -> FocusMode` (already exists at `notifications/router.py:140-141`, no change needed there).
- Produces: `FocusCommand.handle(args, state)` — same signature, but `args.strip() == ""` now returns a read-only status string instead of silently setting mode to `"soft"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/commands/test_focus_meta.py`:

```python
async def test_focus_bare_shows_status_without_mutating():
    """Bare /focus must NOT change the mode — it only reports the current one."""
    from stackowl.notifications.router import NotificationRouter
    from stackowl.events.bus import EventBus

    router = NotificationRouter()
    router.set_focus_mode("hard")  # pre-set to something other than the old default
    bus = EventBus()
    cmd = FocusCommand(router=router, event_bus=bus)

    result = await cmd.handle("", make_state())

    assert "hard" in result.lower()
    assert router.get_focus_mode() == "hard"  # unchanged — bare call must not mutate
```

(Use whatever `make_state()` helper this test file's existing tests already use.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/commands/test_focus_meta.py::test_focus_bare_shows_status_without_mutating -v`
Expected: FAIL — today bare `/focus` sets mode to `"soft"`, so `router.get_focus_mode() == "hard"` fails.

- [ ] **Step 3: Implement**

In `focus_command.py::handle`, replace:

```python
        stripped = args.strip()
        mode: FocusMode
        if stripped in ("--hard", "hard"):
            mode = "hard"
        elif stripped == "off":
            mode = "off"
        elif stripped in ("", "soft"):
            mode = "soft"
        else:
```

with:

```python
        stripped = args.strip()
        if stripped == "":
            current = self._router.get_focus_mode()
            log.notifications.debug(
                "[notifications] focus.handle: exit — status read, no mutation",
                extra={"_fields": {"mode": current}},
            )
            return f"focus_mode:{current} (read-only — pass soft|hard|off to change it)"
        mode: FocusMode
        if stripped in ("--hard", "hard"):
            mode = "hard"
        elif stripped == "off":
            mode = "off"
        elif stripped == "soft":
            mode = "soft"
        else:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/commands/test_focus_meta.py::test_focus_bare_shows_status_without_mutating -v`
Expected: PASS.

- [ ] **Step 5: Run the full file + update the CommandMeta doc**

Run: `uv run pytest tests/commands/test_focus_meta.py -v`
Expected: all PASS (check no other existing test in this file asserted bare-`/focus` sets soft — if one does, update it to match the new read-only behavior instead of deleting it, since it's testing real prior behavior that intentionally changed).

Update `_FOCUS_META`'s `Arg` description in `focus_command.py` (currently says the arg is `required=False` with no note on bare behavior) to add a one-line description: `"Mode to set. Omit to just show the current mode (no change)."`

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/focus_command.py tests/commands/test_focus_meta.py
git commit -m "fix(commands): bare /focus reads current mode instead of silently setting soft"
```

---

### Task 5: `/memory` — remove `delete`, keep `forget`

**Files:**
- Modify: `src/stackowl/commands/memory_command.py:72-87` (remove `delete` `SubCommand`), `:223-224` (remove `delete` dispatch branch), `:283-312` (remove `_delete` method), `:197` (description text)
- Delete: `tests/journeys/commands/test_memory_delete_prefix.py` (exclusively tests the retired `delete` verb and its parity with `forget` — once `delete` is gone there's nothing left to test)

**Interfaces:**
- Consumes: nothing new — `_forget` is untouched, already correct.
- Produces: `MemoryCommand.handle()` no longer accepts `"delete"` as a subcommand; unknown-subcommand path (`render_usage`) handles it same as any other invalid verb.

- [ ] **Step 1: Write the failing test (confirms `delete` is gone, `forget` still works)**

Add to `tests/journeys/commands/test_memory_command_registration.py` (or create a new small test in the same directory if that file doesn't fit — check its existing shape first):

```python
async def test_memory_delete_no_longer_a_subcommand(db):
    bridge = FakeBridge()
    deps = _make_deps(bridge, db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "memory", "delete somefactid YES", make_state()
    )

    # No longer a real subcommand — falls through to usage, same as any typo
    assert "delete" not in result.lower() or "usage" in result.lower()


async def test_memory_forget_still_works(db):
    bridge = FakeBridge()
    fact = make_staged(fact_id="aabbccdd-0000-0000-0000-000000000009", content="still works")
    bridge.seed("staged", fact)
    deps = _make_deps(bridge, db)
    register_all_commands(deps, registry=CommandRegistry.instance())

    result = await CommandRegistry.instance().dispatch(
        "memory", "forget aabbccdd YES", make_state()
    )

    assert "✓" in result
    assert fact.fact_id in bridge.delete_calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/journeys/commands/test_memory_command_registration.py::test_memory_delete_no_longer_a_subcommand -v`
Expected: FAIL — `delete` still works today, so the response contains a `✓ Deleted` line, not usage text.

- [ ] **Step 3: Implement — remove `delete`**

In `memory_command.py`:
1. Remove the `SubCommand(name="delete", ...)` block (lines 72-87).
2. Remove the `elif sub == "delete": result = await self._delete(rest.strip())` branch (line 223-224) from `handle()`.
3. Remove the entire `_delete` method (lines 283-312).
4. Update the class `description` property (line 197) from `"Memory management commands (stats, search, delete, budget, reindex)."` to `"Memory management commands (stats, search, forget, budget, reindex)."`.

- [ ] **Step 4: Delete the now-obsolete test file**

```bash
git rm tests/journeys/commands/test_memory_delete_prefix.py
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/journeys/commands/test_memory_command_registration.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full memory command test surface**

Run: `uv run pytest tests/journeys/commands/test_memory_command_registration.py tests/tools/knowledge/test_memory.py -v`
Expected: all PASS (confirm with `grep -rn "memory.*delete\b" tests/ --include="*.py"` first and update any stray reference found).

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/commands/memory_command.py tests/journeys/commands/test_memory_command_registration.py
git commit -m "refactor(commands): drop /memory delete, byte-identical duplicate of /memory forget"
```

---

### Task 6: `/browser` dead reference fix

**Files:**
- Modify: `src/stackowl/commands/browser_command.py:264-272` (`_watch_subcmd`)

**Interfaces:**
- Consumes: nothing.
- Produces: no signature change, text-only fix.

- [ ] **Step 1: Write the failing test**

Add to whatever existing browser-command test file covers `_watch_subcmd` (find it with `grep -rln "watch_subcmd\|browser.*watch" tests/ --include="*.py"`; if none exists, add one to a new `tests/commands/test_browser_watch.py`):

```python
def test_browser_watch_list_has_no_dead_agent_reference():
    cmd = BrowserCommand()
    result = cmd._watch_subcmd(["list"])
    assert "/agent" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/commands/test_browser_watch.py::test_browser_watch_list_has_no_dead_agent_reference -v`
Expected: FAIL — the text currently contains `"Use /agent list to see scheduler activity."`.

- [ ] **Step 3: Implement**

In `browser_command.py::_watch_subcmd`, replace:

```python
            return (
                "Website watches are persisted as scheduler jobs. "
                "Ask an owl to 'watch <url> daily' to register one. "
                "Use /agent list to see scheduler activity."
            )
```

with:

```python
            return (
                "Website watches are persisted as scheduler jobs. "
                "Ask an owl to 'watch <url> daily' to register one."
            )
```

(No replacement command pointer — `/agent` was retired with no direct scheduler-activity equivalent; asserting one that doesn't exist would repeat the same class of bug.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/commands/test_browser_watch.py::test_browser_watch_list_has_no_dead_agent_reference -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/commands/browser_command.py tests/commands/test_browser_watch.py
git commit -m "fix(commands): remove dead /agent reference from /browser watch list"
```

---

### Task 7: Full-plan verification

- [ ] **Step 1: Run every test file touched in this plan together**

Run:
```bash
uv run pytest tests/commands/test_provider_command.py tests/journeys/commands/test_config_command.py tests/journeys/commands/test_all_29_reachable.py tests/journeys/commands/test_reachability_guard.py tests/journeys/commands/test_command_manifest_drift.py tests/commands/test_focus_meta.py tests/journeys/commands/test_memory_command_registration.py tests/commands/test_browser_watch.py -v
```
Expected: all PASS.

- [ ] **Step 2: Lint + type-check the touched files**

Run:
```bash
uv run ruff check src/stackowl/commands/provider_command.py src/stackowl/commands/config_command.py src/stackowl/commands/focus_command.py src/stackowl/commands/memory_command.py src/stackowl/commands/browser_command.py src/stackowl/commands/assembly.py src/stackowl/commands/manifest.py
uv run mypy src/stackowl/commands/provider_command.py src/stackowl/commands/config_command.py src/stackowl/commands/focus_command.py src/stackowl/commands/memory_command.py src/stackowl/commands/browser_command.py src/stackowl/commands/assembly.py src/stackowl/commands/manifest.py
```
Expected: clean on both.

- [ ] **Step 3: Grep-sweep for stragglers**

```bash
grep -rn "SettingsCommand\|memory\.delete\|/agent list\|_delete(" src/ tests/ --include="*.py" | grep -v "\.pyc"
```
Expected: no hits (beyond intentional survivors you've already reviewed in Task 3 Step 4).
