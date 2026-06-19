# Slash-Command Overhaul — Epics & Stories (Phase 1)

Source of truth: `slash-commands-defect-matrix.md`. Architecture decided in party-mode
(Winston/Amelia/Murat synthesis). Every story is atomic (one commit, bisectable, tree green),
subagent-driven, TDD failing-first, with a gateway integration test driving
`registry.dispatch` and mocking only the provider.

## Architectural decision (the spine)

- **One canonical registration path:** `commands/assembly.py::build_command_registry(deps)` —
  the single place every command is constructed *with its deps* (`CommandDeps` frozen
  dataclass: event_bus, db, scheduler, bridge, registries, audit_logger, ...) and registered.
  No new module-level `_CMD = register_command(...)`; existing dependency-free self-registers
  may remain during migration but the assembler is the single public entry.
- **`SHIPPED_COMMANDS: frozenset[str]`** in `commands/manifest.py` = the contract, seeded
  FULL. Reachability guard asserts `set(registry names after real assembly) == SHIPPED_COMMANDS`
  (`==`, not `>=`) + an anti-drift test (`SlashCommand.__subclasses__()` ⊆ SHIPPED ∪ EXEMPT).
- **`event_bus=None` is killed structurally** — config/settings/provider constructed in the
  assembler with the real bus.
- **Green strategy:** guard carries a single `@pytest.mark.xfail(strict=True)` with a burndown
  comment; removed in the final wiring commit. Each story's own per-command real-dispatch test
  is the green gate for that story.
- **Anti-mock lint:** gateway tests live in `tests/journeys/commands/`; a guard test bans
  `.handle(` / `EventBus(` there (dispatch-only).

## Epic A — Dispatch spine (lands first, enables everything)

- **A1 — Reachability guard + manifest.** Add `commands/manifest.py` (`SHIPPED_COMMANDS` full)
  + `tests/journeys/commands/test_reachability_guard.py` (`==` through real assembler,
  `xfail(strict)` burndown) + anti-drift subclass test + anti-mock lint test. Failing-first.
- **A2 — Assembler consolidation.** Create `commands/assembly.py::build_command_registry(deps)`;
  move existing scattered `create_and_register` calls (orchestrator.py:409/432/447,
  notifications/assembly.py:170-173) into it; call sites call the assembler. No behavior change,
  tree green.
- **A3 — Scanner hardening.** `gateway/scanner.py`: tolerate leading whitespace (`^\s*/`);
  confirm unknown `/word` stays `route=command` (→ "Unknown slash command", never silent LLM
  fall-through). Test the fall-through battery. Global fix, not example-tuned.
- **A4 — Telegram menu + TUI autocomplete completeness.** Ensure `setMyCommands`
  (`channels/telegram/commands_registration.py:57`) runs AFTER assembly and reflects the full
  registry; verify TUI autocomplete shows the same set.
- **A5 — Dead-bridge cleanup.** Remove unused `channels/{telegram,discord,whatsapp}/slash_bridge.py`
  (Slack's stays — it's the only live one and needed for native Slack slash payloads).
- **A6 — (final) Remove guard xfail marker; optional loader-collapse.** After all wiring lands,
  delete the `xfail` so the guard is a hard `==` gate.

## Epic B — Reachability + honesty for the 15 dead commands (atomic, one per commit)

Each story = wire into assembler + add name to SHIPPED_COMMANDS (already seeded) + bundle the
correctness/honesty fix so no reachable lie ships + real dispatch side-effect test.

- **B1 — /reset (P0).** Add `MemoryBridge.clear_session(session_id)` deleting the session's
  conversation rows; inject bridge; success message conditional on rows deleted. Wire + register.
- **B2 — /audit + /audit export (P0).** Fold `audit export` into `/audit` as an `export`
  subcommand (single-token, matchable); refuse/loud-warn on empty `export_key`; wire `/audit`
  with the real `audit_logger` (orchestrator.py:415).
- **B3 — /staged.** Wire; `_reject` existence-check via `find_staged_by_id` before claiming
  success.
- **B4 — /plugins.** Add registration; `enable/disable` existence-check (or `set_enabled`
  returns bool) before claiming success.
- **B5 — /agents.** Wire `create_and_register` with scheduler/db/bus.
- **B6 — /agent (agent_create).** Wire; make Jinja template load lazy (move out of `__init__`).
- **B7 — /brief.** Wire with the live `MorningBriefHandler`.
- **B8 — /parliament.** Wire with a constructed `ParliamentOrchestrator` + store + registry
  (else subcommands degrade to honest "not configured"; document if orchestrator unavailable).
- **B9 — /webhook.** Wire; soften description to match print-only/instruct behavior.
- **B10 — /connect + /disconnect.** Add registration; add a public `delete_credentials()` on
  the adapter protocol (replace private `_oauth.delete()`); `/disconnect` only claims removal
  when it happened.
- **B11 — /whoami.** Rename to `whoami_command.py` (or assembler-register); fix output to
  include the advertised role/tier/provider.
- **B12 — /why.** Rename/register (handler already correct).
- **B13 — /permissions.** Register; switch bare logger → structured `log` namespace.

## Epic C — Honesty for already-live defective commands

- **C1 — Dead-bus hot-reload (config/settings/provider).** Construct in the assembler with the
  real bus; make `/config set` either actually hot-reload or honestly say "restart required";
  emit real `Settings` payload (or call registry reload) so the provider/settings reload handler
  (`provider_reload.py:37`) actually fires. Test asserts the live registry/config changed.
- **C2 — /browser profile delete.** Stop swallowing OSError + claiming "Deleted"; verify
  removal.
- **C3 — /urgent.** Inject the real channel roster (not `channels=["cli"]`) or derive from
  ChannelRegistry; match the "all channels" contract.
- **C4 — /quiet.** Either scope to session (write session_id) or correct the docstring/description.
- **C5 — /tier.** Correct docstring (owner_key=session_id today) or implement owner scoping.
- **C6 — /memory delete.** Prefix-resolution parity with `/memory forget`; no false success.

## Epic D — Robustness/polish (low priority)

- D1 `/owls` dead `_NO_DB` + surface no-DB DNA skip; D2 `/skill add` git-vs-archive heuristic;
  D3 structured-logger consistency sweep; D4 `/notifications` description breadth.

## Execution notes

- Order: Epic A (A1→A5) → Epic B (B1…B13) → Epic C → Epic D → A6 (remove xfail) → retro/merge.
- Each story via subagent-driven-development: fresh implementer → QA + dev review → gateway
  smoke → commit. Commit small (one story per commit). Stage from repo root.
- Run tests in batches (Jetson constraint). `uv run ruff check src/` + `uv run mypy src/`
  clean for touched files.
