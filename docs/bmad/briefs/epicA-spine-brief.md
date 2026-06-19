# Epic A — Dispatch Spine (implementer brief)

You are implementing the foundational "spine" for StackOwl's slash-command overhaul. Repo root:
`/ssd/projects/stackowl-personal-ai-assistant`. Python, `uv run pytest` / `uv run ruff check src/` / `uv run mypy src/`.
Follow TDD. Make THREE atomic commits (one per section below). Keep the tree green after each.

## Background (the bug you are enabling the fix for)

Slash commands route through `gateway/scanner.py` → `orchestrator._dispatch_turn` (route=="command")
→ `_deliver_command_stub` → `CommandRegistry.dispatch`. Today only 15 of ~29 commands are
reachable. Two registration patterns collide: (A) module-level `_CMD = register_command(Cmd())`
at the bottom of 8 `*_command.py` files (help, config, settings, cost, tools, provider, tier,
browser) — picked up by `load_builtin_commands()` (`commands/registry.py:78`); (B) scattered
`create_and_register(...)` calls for 7 DI commands at `startup/orchestrator.py:409` (skill),
`:432` (memory), `:447` (owls) and `notifications/assembly.py:170-173` (focus, urgent, quiet,
notifications). Pattern-B call sites are hand-maintained and easy to forget — 14 more commands
are dead because nobody calls their factory. Epic B will wire those one at a time.

YOUR JOB is the spine so that (1) there is ONE place all DI commands are registered, (2) a guard
test enforces "shipped ⟺ reachable", (3) the scanner never silently drops a command.

DO NOT wire the 14 dead commands in this task — that is Epic B. DO NOT fix the event_bus=None
hot-reload bug (config/settings/provider) — that is a later story (C1). This task is
**behavior-preserving** for the 15 currently-live commands.

## Commit 1 — Command assembler + SHIPPED_COMMANDS manifest (behavior-preserving)

Create `src/stackowl/commands/assembly.py`:
- A frozen dataclass `CommandDeps` carrying every dependency any command needs, each Optional
  with default None: `event_bus`, `db` (DbPool), `router`, `owl_registry`, `tool_registry`,
  `bridge` (memory bridge), `settings`, `preference_store`, `audit_logger`, `scheduler`,
  `skills_store`, `skills_loader`, `skills_root`, `embedding_registry`, `lancedb`, `promoter`,
  `parliament_orchestrator`, `morning_brief_handler`, `plugin_registry`,
  `integration_registry`. (Add fields as needed; keep them Optional so a partial DepS still
  builds.) Use `from __future__ import annotations` + TYPE_CHECKING imports to avoid heavy
  import cost / cycles.
- `def register_all_commands(deps: CommandDeps, registry: CommandRegistry | None = None) -> CommandRegistry:`
  - registry defaults to `CommandRegistry.instance()`.
  - Calls `load_builtin_commands()` to register the 8 dependency-free module-level commands
    (UNCHANGED — keep their existing module-level `_CMD = register_command(...)`).
  - Then constructs + registers the 7 currently-DI commands using deps, MOVING the existing
    construction logic out of orchestrator.py:409/432/447 and notifications/assembly.py:170-173:
    skill (SkillCommand.create_and_register or direct construct+register — match existing
    signature: store/loader/skills_root/embedding_registry), memory (bridge/settings/db/
    event_bus/lancedb/promoter/embedding_registry), owls (owl_registry/db/event_bus/
    tool_registry), focus (router, event_bus), urgent (router), quiet (db),
    notifications-missed (db). Read each command's `create_and_register`/`__init__` for the exact
    signature before calling. If a required dep is None, still register the command (it will
    emit its own "not configured" message at runtime) — but match current behavior: today these
    7 are only registered when their deps exist, so guard them the same way ONLY IF the current
    code does. Prefer: always construct+register (deps are always present in prod); the guard
    test will pass fakes/Nones.
  - Returns the registry. Log a single summary line (count registered) via `log.gateway.info`.
- Create `src/stackowl/commands/manifest.py`:
  - `SHIPPED_COMMANDS: frozenset[str]` = the EXACT `.command` strings of ALL commands the product
    ships, seeded FULL (all ~29). To get the exact strings, read each command class's `command`
    property. The 15 live + the 14 to-be-wired: agents, agent (read agent_create_command.py for
    its exact `.command`), reset, permissions, audit, whoami, why, brief, parliament, staged,
    webhook, connect, disconnect, plugins. NOTE: `audit_export` is being folded into `/audit` in
    Epic B (do NOT list "audit export"; it is unmatchable). Add a module comment that this is the
    contract enforced by the reachability guard.
  - `EXEMPT_COMMANDS: frozenset[str]` = transitional/non-shipped command `.command` values that
    exist as classes but are intentionally NOT in SHIPPED_COMMANDS yet. Seed with `{"audit export"}`
    (AuditExportCommand, to be deleted/folded in Epic B) plus any other SlashCommand subclass in
    `stackowl.commands` whose name you find is genuinely not a shipped command. Comment each.

Rewire `startup/orchestrator.py` and `notifications/assembly.py`:
- Build a `CommandDeps` from the live objects and call `register_all_commands(deps)` ONCE, at a
  point where ALL needed deps exist (notably `router` is created inside
  `NotificationAssembly.build()` — so the single call must happen AFTER that returns; it must
  also happen BEFORE the Telegram adapter's `setMyCommands` / before channel loops accept input).
  Remove the now-duplicated `create_and_register` calls at orchestrator:409/432/447 and
  notifications/assembly:170-173. If `NotificationAssembly.build` needs to return the `router`
  so the orchestrator can put it in CommandDeps, make that change (read how router is currently
  obtained).
- `load_builtin_commands()` is now called inside `register_all_commands` — remove the standalone
  call at orchestrator.py:322 (or keep it idempotent; registering twice just overwrites — but
  prefer single call site).

VERIFY behavior-preserving: after this commit, the live registry must contain EXACTLY the same 15
commands as before (help, config, settings, cost, tools, provider, tier, browser, skill, memory,
owls, focus, urgent, quiet, notifications-missed — confirm the exact 7 DI `.command` strings).
Run the existing command/notification/startup tests; fix any that referenced the moved call
sites. Add a focused test `tests/commands/test_assembly.py` asserting `register_all_commands` on
a fresh registry with fake deps yields exactly the 15 currently-live names.

## Commit 2 — Reachability guard + anti-drift + anti-mock lint

Create `tests/journeys/commands/` (with `__init__.py` if the suite needs it; check how other
journeys are structured).

- `tests/journeys/commands/test_reachability_guard.py`:
  - `test_every_shipped_command_is_reachable`: reset the registry
    (`CommandRegistry.reset()`), call `register_all_commands(CommandDeps())` (all-None/fakes),
    then assert `set(c.command for c in CommandRegistry.instance().list()) == SHIPPED_COMMANDS`.
    Use `==` (NOT `>=`). Mark it `@pytest.mark.xfail(strict=True, reason="burndown:
    slash-command-overhaul Epic B wires the remaining commands; remove marker in final wiring
    commit")` — because only 15 of 29 register today, this is RED and the xfail keeps the suite
    green. (When Epic B finishes, removing the marker turns it into a hard gate; strict means if
    it unexpectedly passes early, CI fails — that's intended.)
  - The test MUST drive the real `register_all_commands` — do NOT hand-build a registry or
    construct command objects directly in this file.
- `tests/journeys/commands/test_command_manifest_drift.py`:
  - `test_no_command_subclass_is_unmanifested`: import every module in the `stackowl.commands`
    package (reuse `load_builtin_commands`' iteration or `pkgutil`), collect all `SlashCommand`
    subclasses whose `__module__` starts with `stackowl.commands`, and assert each class's
    `.command` value is in `SHIPPED_COMMANDS | EXEMPT_COMMANDS`. This catches a NEW command file
    someone forgets to ship. Seed EXEMPT so this test is GREEN now.
  - Instantiating a command to read `.command` may need constructor args; prefer reading the
    property off an instance built with all-None deps, or make `.command` a classfen. If a
    constructor requires args, construct with None/minimal. Handle gracefully.
- `tests/journeys/commands/test_no_mock_only_command_tests.py`:
  - A lightweight guard: grep the `tests/journeys/commands/` dir contents and assert none of the
    gateway command tests (other than this meta-test) call `.handle(` directly or construct
    `EventBus(` — gateway command tests must drive `registry.dispatch`. (Implement by reading
    the dir's `.py` files and asserting the substrings are absent, excluding this file itself.)
    This prevents regression to mock-only tests in the new dir.

## Commit 3 — Scanner hardening (no silent fall-through)

In `src/stackowl/gateway/scanner.py`:
- The slash regex `_SLASH_CMD_RE = re.compile(r"^/(\w+)")` (line 29) fails on leading whitespace,
  so `' /help'` / `'\t/help'` silently route to the secretary LLM (line 255). Make detection
  tolerant of leading whitespace: either `re.compile(r"^\s*/(\w+)")` OR lstrip the normalized
  text before matching. Keep panic/@owl precedence intact and apply the same leading-whitespace
  tolerance consistently (don't break `@owl` or panic).
- Confirm/keep the contract that an unknown `/word` still routes `route="command"` (so dispatch
  returns "Unknown slash command", NOT a silent LLM turn). It already does — add a test locking
  it.
- Add `tests/gateway/test_scanner_slash.py` (or extend the existing scanner test) with the
  battery: `'/help'`, `' /help'`, `'\t/help'`, `'/help@Bot'`, `'/provider list'`, `'/unknowncmd'`,
  `'hello'` → assert routes. All `/...` (after optional whitespace) → route=command; plain text →
  owl/secretary.

## Constraints
- Behavior-preserving for the 15 live commands (Commit 1). No new event-bus wiring for
  config/settings/provider (that's C1, later).
- No silent excepts; log via the structured `log.gateway` namespace (see CLAUDE.md).
- Run `uv run ruff check src/` and `uv run mypy src/` on touched files; fix issues.
- Run tests in BATCHES (the box can't run the full suite unbounded): run
  `tests/commands/`, `tests/gateway/`, `tests/journeys/commands/`, and any startup/notification
  tests you touched. Report exact commands + output.
