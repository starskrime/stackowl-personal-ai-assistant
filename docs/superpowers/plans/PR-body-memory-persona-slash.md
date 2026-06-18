**Title:** fix(v2): cross-turn memory + persona injection + Telegram slash commands (RC-B/C/D)

---

## Summary

Root-caused (systematic-debugging) and fixed why the agent "forgot context on the 2nd request" and why Telegram ignored `/` commands. Four distinct root causes were found; **three are fixed and reviewed here**, the fourth is scaffolded but paused (see below).

### ✅ RC-B — Owl persona/DNA never reached the model
`execute.py` sent only `memory_context` as the system prompt (often `None`); the owl persona + DNA were never injected. New **`assemble` pipeline step** (classify → **assemble** → execute) builds the system prompt via `owls/dna_injector`.

### ✅ RC-C — Prior turns were flat system-text, not real messages
`PipelineState` had no history field; history rode as a text blob the model under-weighted. Now: real `PipelineState.history` (parsed from staged turns), a backward-compatible `history=` param on the 3 providers' `complete_with_tools`, and `execute` threads real user/assistant turns into the messages array. Proven by a full-`AsyncioBackend` e2e test (turn 2 sees turn 1 **and** the secretary persona).

### ✅ RC-D — Telegram showed no `/` command menu
No `set_my_commands` call existed anywhere. Now registers the menu from `CommandRegistry` on bot start (sanitized + deduped `BotCommand`s), plus case-insensitive `/cmd@botname` group-suffix stripping.

### ⏸️ RC-A — Long-term memory (committed_facts empty) — PARTIAL / INERT, paused
`committed_facts` never fills (extraction was never wired; it also read empty tables). This PR includes **inert scaffolding only** — `ConversationMiner`, a distinct `source_type=conversation_fact` for extracted facts (protects RC-C short-term history), and migration `0039`. **The miner is NOT wired into the DreamWorker, so there is zero production behavior change from the RC-A code.** Remaining work (DreamWorker wiring + a delicate `committed_facts` CHECK+FTS rebuild migration) is paused for a focused session.

## Test Plan
- [x] Plan A: 13 tests green incl. full-backend two-turn e2e (persona + history)
- [x] Plan C: 5 tests green (menu sanitize/dedupe, group suffix)
- [x] Plan B (inert): 6 tests green (source_type, miner idempotency)
- [x] Regression: `tests/test_e0_s1_dispatch_gate.py` + `tests/smoke/` (35) green — provider `history` param back-compat verified across ~20 test fakes
- [x] ruff + mypy clean on touched files
- [ ] Manual: `uv run python -m stackowl serve` → in Telegram, "I'm learning AWS" then "what am I learning?" recalls AWS in persona; `/` shows the command menu

Plans + adversarial review notes: `v2/docs/superpowers/plans/2026-05-30-plan-*.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
