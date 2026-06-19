# Defect Audit — Group 2: memory / skill / owls / agents / agent-create / reset / permissions / audit / audit-export

Auditor: Murat (Test Architect). Date: 2026-06-19. Repo: `/ssd/projects/stackowl-personal-ai-assistant`.

Skeptical pass: each command read at the handler level (+ helpers). "Does it do its job?" answered against the
description/docstring, with file:line evidence. Registration verified against the real loader
(`load_builtin_commands()` in `registry.py:78` only imports `*_command.py`; `register_command(...)` decorator OR a
`create_and_register` with an actual call site is the only way onto the singleton). Reachability traced through
`GatewayScanner.scan` (`src/stackowl/gateway/scanner.py`) + the command route in `orchestrator.py:961`.

## How a slash command actually becomes reachable (the spine)

1. `load_builtin_commands()` (`registry.py:78-107`) iterates `commands/` and imports modules whose name **ends in
   `_command`**. Importing a module only registers a command if that module **calls `register_command(...)` at import
   time**. None of the audited modules do that — they all use the **factory pattern** (`create_and_register`), which
   the loader does NOT call. So registration depends entirely on an explicit call site in startup/assembly.
2. Routing: `GatewayScanner.scan` extracts the command name with `_SLASH_CMD_RE = re.compile(r"^/(\w+)")`
   (`scanner.py:29`, used at `scanner.py:242-248`). **It captures only the first `\w+` token.** `decision.target` is
   that single token.
3. `orchestrator.py:976` then computes `cmd_args = input_text.split(" ", 1)[1] if " " in input_text else ""` and calls
   `registry.dispatch(decision.target, cmd_args, state)` (`orchestrator.py:895`). Unknown command name → caught
   `CommandNotFoundError` → `"Unknown slash command…"` (`orchestrator.py:896-897`).

Two structural consequences fall out of this spine and dominate the findings below:

- **Any command whose `.command` contains a space is unreachable by construction.** `_SLASH_CMD_RE` can never produce a
  multi-word target. This kills `AuditExportCommand` (`command == "audit export"`) outright even if it were registered.
- **Registration is the dominant defect class.** 6 of 9 commands in this group are never put on the registry in
  production. (Corroborated by memory observation 4847 "6 Factory Commands Missing Orchestrator Wiring" and 4848
  "Commands Silent Fall-Through to LLM".)

## Summary table

| Command | File | Registered? | Reachable (CLI/TG) | Does its job? | Defects (file:line) | Real side-effect test? | Effort |
|---|---|---|---|---|---|---|---|
| `/memory` | memory_command.py | ✅ `create_and_register` @ orchestrator.py:432 | ✅ | ✅ Mostly solid — real bridge/db/lancedb/promoter calls, honest "not configured" guards | Minor: `delete` skips prefix-resolution (memory_command.py:188); both `delete`+`forget` print short ids that can't be pasted back (245,265); `reindex`/`remember`/`export` rely on optional deps that ARE wired in prod | ✅ Yes — `tests/test_memory_command_registration.py` dispatches through registry, asserts real committed fact + recall | trivial |
| `/skill` | skill_command.py | ✅ `create_and_register` @ orchestrator.py:409 | ✅ | ✅ Genuinely does the work — real disk install (path/git/archive), rmtree, SQLite reindex, snapshot/restore, audit chokepoint | `add` uses `_looks_like_git_repo` heuristic that misclassifies deep repo URLs as archives (skill_command.py:247, helper :526); `edit` only prints a path (by design, doc-accurate but no actual edit) | ✅ Yes — `tests/skills/test_skill_command.py`, `tests/skills/test_skill_restore.py` exercise real install/restore on disk | trivial |
| `/owls` | owls_command.py | ✅ `create_and_register` @ orchestrator.py:447 | ✅ | ✅ Real registry mutation + YAML persistence + DNA capture/reset | `_NO_DB` constant defined but never used (owls_command.py:52, dead); `add`/`edit`/`remove` write YAML even when `_db is None` (DNA silently not persisted — only logged) | ✅ Yes — `tests/test_owls_command_registration.py` dispatches through registry; `tests/commands/test_owls_reset_dna.py`, builder/journey tests assert real state | trivial |
| `/agents` | agents_command.py | ❌ **NO call site** — `create_and_register` defined (agents_command.py:274) but never invoked in src | ❌ **Unreachable in prod** → "Unknown slash command" | Logic is correct IF wired (real scheduler pause/resume/stop + DB writes) but never runs | **P1: not registered** (no caller of `AgentsCommand.create_and_register` anywhere in `src/`) | ⚠️ Mock-only — `tests/test_story_7_2b.py` calls `cmd.handle(...)` directly with fake scheduler; **never through `registry.dispatch`**, so the wiring gap is invisible | small |
| `/agent` | agent_create_command.py | ❌ **NO call site** — `create_and_register` defined (agent_create_command.py:262) but never invoked in src | ❌ **Unreachable in prod** → "Unknown slash command" | Two-step create/confirm/cancel logic correct IF wired (real provider call + `create_job`) but never runs | **P1: not registered**; also `__init__` eagerly loads a Jinja template at construct time (agent_create_command.py:81) — a missing template would crash construction, not handle() | ⚠️ Mock-only — `tests/test_story_7_2.py` / `test_story_7_2b.py` call `cmd.handle(...)` directly; never via registry | small |
| `/reset` | reset.py | ❌ Not `*_command.py` (loader skips) **and** no instantiation anywhere in src | ❌ Unreachable in prod | ❌ **NO-OP that LIES** — returns `"Session history cleared."` and deletes nothing | **P0: hardcoded false success** (reset.py:19). No conversation-delete API exists (see notes). `clarify_pump.py:46` references `_RESET_COMMAND="reset"` for an unrelated pump path — not this command | ❌ None | medium |
| `/permissions` | permissions.py | ❌ Not `*_command.py` (loader skips) **and** no instantiation anywhere in src | ❌ Unreachable in prod | ✅ Read-only view is honest & functional IF constructed (real settings/integration/plugin reads) | **P1: not registered / not reachable.** Module name `permissions.py` is invisible to the loader; no `create_and_register`/`register_command`; uses its own `logging.getLogger` not the structured `log` namespace (permissions.py:16) | ⚠️ Mock-only via direct `.handle()` (per task brief) | small |
| `/audit` | audit.py | ❌ Not `*_command.py` (loader skips) **and** no instantiation anywhere in src | ❌ Unreachable in prod | ✅ Renders tail(50) + chain verify correctly IF constructed | **P1: not registered / not reachable** (audit.py filename invisible to loader; no factory call site); uses bare `logging.getLogger` (audit.py:16) | ⚠️ Mock-only via direct `.handle()` | small |
| `/audit export` | audit_export.py | ❌ Not `*_command.py` (loader skips) AND no instantiation in src | ❌ **Doubly unreachable** | Writes signed JSON + `.sig` correctly IF dispatched — but it never can be | **P0 structural: `command == "audit export"` (audit_export.py:41) can NEVER be matched** by `_SLASH_CMD_RE = r"^/(\w+)"` (single token). Even registered, `/audit export` routes to command name `"audit"` with args `"export"`. Also not registered. Empty `export_key` silently signs with `b""` key (audit_export.py:88) — a "signature" with no secret | ⚠️ Mock-only via direct `.handle()` | medium |

Legend: P0 = lies about success / unreachable two-word command; P1 = real feature silently never fires (not registered).

---

## Per-command notes

### `/memory` — memory_command.py (SOLID, minor gaps)

Claim verified. Subcommands route to real I/O: `_stats`→`collect_stats(db)` (real SQL), `_search`→`bridge.recall`,
`_delete`/`_forget`→`forget_fact`→`bridge.delete` (deletes from BOTH staged and committed stores —
`bridge.py:68-69` contract), `_budget`→real `SUM(length(content))`, `_reindex`→`lancedb.reindex`,
`_remember`→`remember_fact`→`bridge.stage` + `promoter.force_promote`, `_export`→`do_export` (real file write,
`TestModeGuard`-guarded at memory_helpers.py:348). "Not configured" guards are honest (reindex/remember return
`"✗ … not configured"` when `lancedb`/`promoter` is None — memory_command.py:222-223, 241-242). All these deps ARE
injected in prod (orchestrator.py:432-440), so they are non-None at runtime.

Real gaps (all minor):
- **`_delete` does NOT resolve prefixes** (memory_command.py:188 `fact_id = parts[0]` passed raw to `forget_fact`),
  whereas `_forget` DOES (`find_staged_by_id`, memory_command.py:254). So `/memory delete <prefix>` silently deletes
  nothing if the prefix isn't a full id, while `/memory forget <prefix>` works. Inconsistent UX; `delete` can appear to
  "succeed" (`✓ Deleted <prefix>`) having matched no row, since `bridge.delete` returns None regardless of whether a
  row existed. **Borderline P2 false-success.**
- **8-char id round-trip**: `_remember` prints `fact_id[:8]` (memory_command.py:245) and `_forget` prints
  `fact.fact_id[:8]` in the confirm prompt (memory_command.py:260) but then instructs the user to type the FULL
  `fact.fact_id` (line 261) — so the confirm path is fine. The `remember` 8-char echo, however, is not directly
  pasteable into `delete` (which needs a full id). Cosmetic.
- `find_staged_by_id` (staged_helpers.py:83) scans staged→committed→rejected and never silently skips a bucket (logs on
  failure) — good. Empty prefix returns None (guarded).
- Test quality is exemplary: `test_memory_command_registration.py` drives `registry.dispatch("memory", ...)` on a real
  tmp DB + real promoter, asserts a committed fact actually appears and recall finds it, AND has a negative test that
  dispatch raises when NOT registered. This is the model the rest of the group should follow.

### `/skill` — skill_command.py (SOLID)

Genuinely performs disk + index mutations: `_add` installs from local/git/archive (`skill_helpers.py` does real
`shutil.copytree`, `git clone --depth=1`, zip/tar extraction with traversal + size-bomb guards), `_rm` does real
`shutil.rmtree` + `store.delete`, `_restore` rebuilds the tree from a snapshot via atomic staging swap
(`restore_snapshot`, skill_helpers.py:98), all routed through the `record_skill_mutation` provenance chokepoint
(audit + before/after hash + snapshot). `reindex_after_change` re-embeds so new skills are retrievable.

Defects:
- **`_looks_like_git_repo` heuristic is brittle** (skill_command.py:247 → helper :526): it only treats a URL as git
  when the host path has EXACTLY two segments (`owner/repo`). A valid `https://github.com/owner/repo/` with a trailing
  detail, or a self-hosted git host not in the 4-host allowlist, falls through to `install_from_archive_url` and fails
  with "not a recognized archive". Functional for the common case; mis-handles edge URLs. **P2.**
- `_edit` only prints the SKILL.md path and tells the user to edit it manually (skill_command.py:330-334). This is
  doc-accurate ("print path to SKILL.md (open it yourself)") — not a defect, but worth flagging that `/skill edit` does
  not edit anything.
- Tests are real: `tests/skills/test_skill_command.py` and `test_skill_restore.py` install/remove/restore on actual
  temp dirs and assert disk state. Good. (Did not confirm a test drives the `add --url` git-vs-archive branch decision
  specifically — recommend one.)

### `/owls` — owls_command.py (SOLID)

Real registry mutation (`register`/`replace`/`deregister`), YAML persistence (`_upsert_to_yaml`/`_remove_from_yaml`
via `config_helpers`), authored-DNA capture (`capture_one_authored`), and `reset-dna` (reads authored baseline,
upserts `owl_dna`, applies overlay, resets the directive latch). `edit` re-validates the WHOLE manifest
(owls_command.py:201) rather than `model_copy` to avoid silently landing invalid edits — good defensive design.

Defects:
- **`_NO_DB` constant is dead** (owls_command.py:52) — defined, never referenced. When `_db is None`, `add`/`edit`
  proceed to mutate registry + YAML and just skip DNA persistence with a debug log (owls_command.py:161-164,
  `_delete_dna_rows` :377-382). So DNA is silently not persisted in a no-DB config rather than telling the user.
  **P2 (low — prod always wires db).**
- `_dna` and `_reset_dna` correctly gate on `_db is None` with user-visible messages (`_NO_REGISTRY` / "DNA store
  unavailable.").
- Tests: `test_owls_command_registration.py` dispatches through the registry; `test_owls_reset_dna.py` +
  builder/journey tests assert real state. Good coverage.

### `/agents` — agents_command.py (P1: NOT WIRED)

The handler is correct: `_list`→`scheduler.list_jobs`, `_pause`/`_resume`/`_stop`→real scheduler methods,
`_acknowledge`/`_log`→real DB reads/writes (UPDATE jobs, write_audit). BUT **`AgentsCommand.create_and_register`
(agents_command.py:274) has zero call sites in `src/`** — grep confirms only the definition and the import in
`owl_build.py` (which imports `OwlsCommand`, not this). In production the registry never holds `"agents"`, so
`/agents …` → `CommandNotFoundError` → `"Unknown slash command: '/agents'."` (orchestrator.py:896).

- Tests (`test_story_7_2b.py`) call `cmd.handle(...)` on a hand-constructed instance with a fake scheduler — they
  prove the handler logic but **cannot catch the wiring gap** because they never go through `registry.dispatch`. This
  is exactly the "green tests, broken feature" pattern.
- Fix = add `AgentsCommand.create_and_register(scheduler=…, db=db_pool, event_bus=event_bus)` in orchestrator.py
  alongside the other factory calls. Effort: small.

### `/agent` — agent_create_command.py (P1: NOT WIRED)

Two-step create/confirm/cancel. `_create` renders `agent_intent.j2`, calls `provider.get_by_tier("fast").complete(...)`,
parses with `parse_intent_response` (validates handler ∈ {goal_execution, morning_brief, check_in} + non-empty
schedule), stages a per-session proposal. `_confirm` calls real `scheduler.create_job`. Logic is sound.

- **Not registered**: `AgentCreateCommand.create_and_register` (agent_create_command.py:262) has no call site in
  `src/`. `/agent …` is unreachable in prod (same `CommandNotFoundError` fall-through).
- **Construction-time fragility**: `__init__` eagerly does `env.get_template(_TEMPLATE_NAME)` (agent_create_command.py:81).
  If the template is missing/renamed, construction throws — so a future wiring call would crash startup rather than
  degrade. Prefer lazy template load inside `_create`.
- Tests: `test_story_7_2.py` (parser/proposal/render units) and `test_story_7_2b.py` (`cmd.handle` with fake scheduler,
  asserts `scheduler.created`) — all direct `.handle()`, never via registry. Wiring gap invisible.

### `/reset` — reset.py (P0: HARDCODED FALSE SUCCESS)

```python
async def handle(self, args, state) -> str:
    return "Session history cleared."   # reset.py:19
```

It deletes **nothing** and unconditionally claims success. Confirmed there is no conversation-delete API:
`memory/bridge.py` exposes only `delete(fact_id)` (deletes a single FACT, not a session's turns) and
`recent_conversation_turns(session_id, …)` (read-only). To make `/reset` honest you would need a new
`MemoryBridge.clear_session(session_id)` that deletes the session's conversation rows from `staged_facts` (the table
that backs `recent_conversation_turns`) — and the command would need the bridge injected (it currently takes no deps).

- **Doubly broken**: also not reachable — `reset.py` is not a `*_command.py` module (loader skips it) and nothing
  instantiates `ResetCommand` anywhere in `src/`. So today the user gets the LLM fall-through, not even the lie. Once
  someone "fixes registration" by renaming the file, the lie becomes live. Fix BOTH: rename/register AND implement a
  real `clear_session`. (The `_RESET_COMMAND = "reset"` in `clarify_pump.py:46` is an unrelated clarify-pump literal,
  NOT this command.)
- No test. Effort: medium (needs new bridge API + migration-free DELETE + dependency injection + registration).

### `/permissions` — permissions.py (P1: NOT REACHABLE)

`handle` honestly assembles a read-only view: autonomy level, per-owl tool allowlists (consequential tools marked
`!`), connected integrations, active plugins — all from real registries. The plugin list is defensively wrapped
(permissions.py:97-104). Output matches description.

- **Invisible to the loader** (filename `permissions.py`, not `*_command.py`) AND no `create_and_register`/instantiation
  in `src/`. Unreachable in prod.
- Minor: uses `logging.getLogger("stackowl.gateway")` (permissions.py:16) instead of the project's structured `log`
  namespace — inconsistent with CLAUDE.md observability standard.
- Mock-only direct `.handle()` testing per the task brief; never through registry. Effort to wire: small (rename to
  `permissions_command.py` + add `register_command(...)` or a factory call with the three deps).

### `/audit` — audit.py (P1: NOT REACHABLE)

`handle` fetches `tail(50)`, runs `verify_chain()`, renders a table, and appends `✓ Chain intact` / `✗ Chain broken at
record N` (audit.py:48-54). Correct and honest IF constructed.

- Same registration problem: filename not `*_command.py`; no instantiation in `src/`. Unreachable.
- Bare `logging.getLogger` (audit.py:16). Effort to wire: small (needs the already-built `audit_logger`, which exists
  at orchestrator.py:415).

### `/audit export` — audit_export.py (P0: UNREACHABLE TWO-WORD COMMAND)

Two independent show-stoppers:
1. **`command == "audit export"` (audit_export.py:41) is unmatchable.** `_SLASH_CMD_RE = r"^/(\w+)"` (scanner.py:29)
   captures a single token; `decision.target` can never equal `"audit export"`. A user typing `/audit export` is routed
   to command name `"audit"` (args `"export"`). So even the correct design (subcommand on `/audit`) is bypassed — this
   should be a `_audit_export` subcommand of `AuditCommand`, not a separate command with a spaced name.
2. **Not registered** — not `*_command.py`, no instantiation in `src/`.

Functionally (if invoked directly) it does write the JSON + `.sig` correctly. But note **empty-key signing**: when
`export_key` is `""` (the default, audit_export.py:31), it signs with `key_bytes = b""` (audit_export.py:88) and still
prints `"HMAC-SHA256 signature: …"` — a tamper-evidence signature with NO secret is worthless and the output does not
warn the user. **Borderline P1 security overclaim.** Effort: medium (fold into `/audit` as a subcommand + warn/refuse
on empty key).

---

## Cross-cutting recommendations (priority order)

1. **P0 `/reset`**: implement `MemoryBridge.clear_session(session_id)` + inject the bridge + register the command +
   make the success message conditional on rows actually deleted. Add a registry-dispatch test asserting turns are gone.
2. **P0 `/audit export`**: refold into `AuditCommand` as a `export` subcommand (matchable single-token `/audit`); refuse
   or loudly warn when `export_key` is empty.
3. **P1 registration sweep**: wire `AgentsCommand`, `AgentCreateCommand`, `PermissionsCommand`, `AuditCommand` (and
   `/audit export` per #2) onto the registry in `orchestrator.py` next to the existing `create_and_register` block
   (lines 409-452). Rename `reset.py`/`permissions.py`/`audit.py`/`audit_export.py` to `*_command.py` OR keep the names
   and call factories explicitly — but a name that the loader can see is the least-surprise option only if the module
   ALSO self-registers; otherwise the rename is cosmetic.
4. **Merge-gate guard**: add ONE integration test per command that asserts `registry.dispatch(name, …)` does NOT raise
   `CommandNotFoundError` after the real startup wiring path runs. The mock-only `cmd.handle()` tests demonstrably hide
   every wiring gap above. This is the canonical "drive the gateway, mock only the provider" requirement.
5. **P2 polish**: `/memory delete` prefix-resolution parity with `forget`; `/skill add` git-vs-archive heuristic;
   `/owls` dead `_NO_DB` + silent no-DB DNA skip; structured-logger consistency in permissions/audit/audit_export.
