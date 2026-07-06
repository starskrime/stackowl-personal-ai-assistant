# Slash-command platform redesign — design spec

Date: 2026-07-06
Status: approved by user, pending spec review

## 1. Problem

Slash commands are the primary configuration surface for StackOwl (TUI + Telegram + Slack + Discord + WhatsApp, one shared `CommandRegistry`). An audit of all 32 shipped commands (see Appendix A) found:

1. `/webhook register`/`disable` are pseudo-mutations — they print YAML instructions instead of writing config, unlike every other verb command in the app.
2. `/provider add`/`remove`/`set-tier` and `/config set`/`reset` already write real config, but their "reload" emits a `dict` payload that the only two live subscribers (`ProviderRegistry`, identity-alias resolver) explicitly ignore (type-guarded to `Settings` only). The change only actually takes effect once `ConfigWatcher`'s background file-poll (5s interval, 2-tick debounce) picks up the file write independently — up to ~10s later, unrelated to the event the command itself emitted.
3. `/settings autonomy` is a near-duplicate of `/config set autonomy_level` — writes YAML directly (no persisted-verify), emits `"settings_changed"`, an event with zero subscribers anywhere in the codebase.
4. `/focus` (bare, no args) silently **sets** mode to `soft` instead of showing current mode — no read path exists at all.
5. `/memory delete` and `/memory forget` are byte-identical implementations (same helper calls, differ only in an internal actor tag).
6. No `/onboarding` command exists — only a pre-launch CLI wizard (`stackowl setup --minimal`).
7. All responses are plain text. There is no way for a command to offer tap-to-act follow-ups (e.g. tap a provider row to edit/remove it) on any channel.
8. One stale reference: `/browser watch list` help text points at `/agent list`, a command retired in today's `/owl` consolidation.

This spec covers the redesign for all of the above. Everything else in the 32-command inventory (`/help`, `/find`, `/config` list/get, `/tools`, `/tier`, `/browser` (minus the one stale line), `/explain`, `/skill`, `/staged`, `/notifications`, `/bye`, `/reset`, `/permissions`, `/audit`, `/whoami`, `/why`, `/brief`, `/parliament`, `/connect`/`/disconnect`, `/plugins`, `/cost`, `/preferences`, `/urgent`, `/owl`) had no structural defects — they gain the interactive-button layer (§8) mechanically during implementation, no redesign needed.

## 2. Real-time settings reload (foundational fix)

**Root cause:** `ConfigWatcher` (`config/watcher.py`) is the only thing that ever calls `Settings()` to rebuild a real settings object and emit it — and only on its own poll timer. Commands write the YAML file directly then emit a throwaway `dict`/differently-named event that no subscriber acts on.

**Fix:** any command that verifies a successful YAML write (F-81 persisted-check pattern, already used by `/provider`/`/config`) additionally does:

```python
try:
    new_settings = Settings()
    self._bus.emit("settings_reloaded", new_settings)
except Exception as exc:
    log.config.error("<cmd>: immediate reload failed, falling back to background poll", exc_info=exc)
```

This is exactly what `ConfigWatcher._reload()` does, just triggered immediately by the writer instead of waiting on the next poll tick. It is safe to call right after our own completed, verified write (we are the writer — no concurrent mid-write risk the debounce exists to guard against). It flows through the **existing** type-guarded subscribers with zero new subscriber code for `/provider` and `/config`.

**Changes:**
- `commands/provider_command.py::_emit_reloaded` — swap dict payload for real `Settings()`.
- `commands/config_command.py` — same swap in `_set`/`_reset`.
- `commands/settings_command.py` — retired (see §3).
- New: `commands/webhook_command.py` needs this too, but webhook also needs a **new subscriber** (§5) since nothing today updates a running `WebhookReceiver`.

Response text drops the "applies on next reload/restart" caveat wherever the reload is now confirmed immediate; keeps an honest restart-required message only where a real restart is structurally needed (§6, first-ever webhook source).

## 3. `/settings` retirement

`/settings autonomy <level>` is removed. `/config set autonomy_level <level>` and `/config get autonomy_level` are the only path — same schema validation, plus (after §2) immediate live reload, plus a persisted-verify check `/settings` never had. `commands/settings_command.py` is deleted; its registration in `assembly.py` is removed; `manifest.py`'s `SHIPPED_COMMANDS` drops `"settings"`; a reachability test update reflects the new command set (net count unchanged at 32 — see §7).

## 4. `/focus` read path

- `/focus` (bare) → read-only: current mode + how long it's been set. No mutation.
- `/focus soft|hard|off` → sets mode (unchanged behavior), same as `/focus --hard` alias today.
- Requires `NotificationRouter` to expose current mode + set-timestamp (check `router.py` for an existing field or add one — in-memory only, no persistence needed, this state is intentionally ephemeral/process-lifetime).

## 5. `/memory` dedup

`_delete` is removed; `_forget` is the sole implementation (already correct — no behavior change beyond removing the duplicate method and its `SubCommand` entry). `remember`/`forget` becomes the natural verb pair, matching `/memory remember <text>`.

## 6. `/webhook` real CRUD

### Grammar
```
/webhook register <source> [timestamp_header=<H>] [delivery_id_header=<H>] [secret=<RAW>] [replay_tolerance_s=<N>]
/webhook list
/webhook disable <source>
```

At least one of `timestamp_header`/`delivery_id_header` is required (mirrors `WebhookSourceConfig._require_anti_replay_mechanism`'s own fail-closed rule) — reject with a clear message listing both options if neither is given. Never guess a vendor-specific header name (Stripe/GitHub/etc.) — the user must state it, since the whole point of this pattern is protocol-agnostic config.

### Secret handling
- `secret=<RAW>` given → stored via `store_secret(f"stackowl-webhook-{source}", secret)` (existing helper, same one `/provider add` uses), never echoed back.
- Omitted → auto-generate via `secrets.token_urlsafe(32)`, store the same way, **show it once** in the success response (the user must paste it into the sending service) with a clear "won't be shown again" note.

### Write + reload
- Reuses `config_helpers.load_yaml`/`save_yaml` + F-81 verified-persist re-read, same pattern as `/provider add`.
- Flips top-level `webhook.enabled: true` if this is the first-ever source.
- After verified write: real-Settings emit per §2.

### New subscriber
`startup/webhook_reload.py::make_webhook_reload_handler(receiver: WebhookReceiver)` — mirrors `startup/provider_reload.py` exactly: type-guards on `Settings`, calls `receiver.apply_settings(new_settings)`. New tiny method on `WebhookReceiver`:
```python
def apply_settings(self, settings: Settings) -> None:
    self._settings = settings
    log.webhook.info("[webhook] receiver.apply_settings: sources refreshed", extra={"_fields": {"sources": len(settings.webhook.sources)}})
```
Wired in `startup/orchestrator.py` next to the other two `settings_reloaded` subscriptions, only if a `WebhookReceiver` instance exists (mirrors the existing defensive pattern for provider/identity).

### Response honesty (schema already declares this — `webhook_settings.py` `hot_reload` flags)
- First source ever (webhook was disabled) → `webhook.enabled` flip is `hot_reload=False` (binding a brand-new listener needs `WebhookReceiver.run()`, a real process action) → response says restart is required to start listening.
- Adding to an already-running receiver → `sources` dict is `hot_reload=True` → response says live now, no restart.
- `disable` is always live (same dict, always hot-reload-capable).

### Docstring
Drops the "intentionally never writes config or secrets at runtime" claim — no longer true.

## 7. `/onboarding` (new command)

Full first-run wizard, reachable mid-session (not just pre-launch CLI), button-driven (§8):

1. **Provider setup** — offer to add first AI provider: protocol, model, tier, token — reuses `/provider add` under the hood (already correct, now live-reloading per §2).
2. **Autonomy level** — `/config set autonomy_level <level>` under the hood.
3. **Channels** — which channels to enable (Telegram/Slack/Discord/WhatsApp), delegates to each channel's existing connect flow (`/connect <service>`, already has a real post-condition check).
4. **First owl** — creates the user's first owl persona via the existing `/owl create` engine (already correct, consolidated today).
5. **Scheduler/notification preferences** — check-in cadence, proactive-delivery channel choice.

Each step is a `CommandResponse` with buttons for the available choices (§8) and a text-input fallback for free-form values (provider name, model id, etc.) — buttons pick from enumerable choices, text covers open-ended ones. Re-running `/onboarding` restarts the wizard from step 1 (idempotent — steps that detect existing config skip forward, e.g. skip provider setup if one is already configured, with an explicit "already have a provider — add another or skip" choice rather than silently blocking re-entry).

`commands/onboarding_command.py` — new file, registered in `assembly.py`, added to `manifest.py::SHIPPED_COMMANDS`.

## 8. Interactive response layer (buttons)

### Scope
TUI + Telegram only this pass. Slack/Discord/WhatsApp keep plain text (native button equivalents — Block Kit, message components, interactive lists — are a near-identical follow-up, not blocking this).

### Data model
```python
@dataclass(frozen=True)
class Action:
    label: str
    command: str          # the exact slash-command string a tap replays
    destructive: bool = False   # drives the confirm-step (see below)

@dataclass(frozen=True)
class CommandResponse:
    text: str
    actions: tuple[Action, ...] = ()
```
`SlashCommand.handle()` return type becomes `str | CommandResponse`. `CommandRegistry.dispatch()` normalizes: a bare `str` becomes `CommandResponse(text=str, actions=())` — every existing command keeps working untouched. Commands gain `actions=(...)` incrementally as each is touched (this redesign's queue first: `/webhook`, `/provider`, `/onboarding`; the rest mechanically over time, not a flag-day rewrite).

### Tap mechanism
A tapped button re-dispatches its `command` string through the exact same `CommandRegistry.dispatch()` path as if the user had typed it — no new execution path, no new bug class. Buttons are purely a picker for text the user could type anyway.

### Drill-down
A list-type response (e.g. `/provider list`) attaches one action per row that replays a scoped sub-view (e.g. `/provider menu openai`), which itself returns a fresh `CommandResponse` with `[Edit][Remove][Set-tier]` actions for that one provider. Ordinary recursion through the same mechanism — no special-casing.

### Destructive confirm
`Action.destructive=True` is a simple bool, deliberately not a full port of the tool-manifest's multi-level `action_severity`/`consent_category` scheme — the concept it borrows from, not the exact type. A command author sets it `True` for anything that deletes/disables/retires. Setting it `True` means tapping it doesn't execute immediately. It replaces the button row in-place with `[Yes, <label>][Cancel]`; only the second tap replays the real command. Non-destructive actions execute on first tap.

### Telegram rendering
`actions` → `InlineKeyboardMarkup`. Telegram's `callback_data` is capped at 64 bytes. For a `command` string over that limit: a short opaque id (8 chars, random) maps to the full string in an in-memory dict, TTL 15 minutes (same shape as the existing quiet-hours-override TTL pattern in `quiet_command.py`, no new subsystem). In-memory only — a bot restart drops any pending mapping, so a very old unused button (rare, since the process is long-running and buttons are typically tapped soon after they render) fails with a clear "this button expired, re-run the command" message rather than a silent no-op. New `callback_query` handler in `channels/telegram/adapter.py` (or `_bot.py`) resolves id → string, re-dispatches through the identical turn path used for a typed message (`_dispatch_turn` in `orchestrator.py`).

### TUI rendering
`actions` → real Textual `Button` widgets. `on_press` submits the `command` string through the same input path the text box already uses. No length constraint, no id-mapping.

## 9. Testing

- `tests/journeys/commands/test_all_29_reachable.py` — update the `SHIPPED_COMMANDS` membership check to reflect `/settings` dropped, `/onboarding` added.
- Unit tests per changed command: `webhook_command`, `provider_command`, `config_command`, `focus_command`, `memory_command` (dedup), new `onboarding_command`.
- New: `startup/webhook_reload.py` gets the same test shape as `startup/provider_reload.py` (type-guard behavior, `apply_settings` called on `Settings` payload, ignored on dict payload).
- New: `CommandResponse`/`Action` — dispatch-normalization test (bare `str` wraps correctly), destructive-confirm two-tap test, Telegram id-mapping TTL-expiry test, TUI button → same-path-as-text-submit test.
- `/browser` stale-reference fix — trivial text change, covered by existing help-text snapshot test if one exists, else a one-line assertion added.

## 10. Non-goals (this pass)

- Slack/Discord/WhatsApp native button rendering (text fallback only, for now).
- Any change to the ~24 commands found structurally sound in the audit, beyond receiving `actions=()` mechanically when their turn comes in implementation, and the one `/browser` text fix.
- Persisting `/focus` mode across restarts (intentionally ephemeral, unchanged).

## Appendix A: full 32-command audit

See the 2026-07-06 conversation for the complete command-by-command table (file:line, CRUD completeness, status) that this spec is based on — not reproduced here to keep this spec focused on the changes being made.
