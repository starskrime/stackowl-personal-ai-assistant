# Multi-Provider Catalog & Tier Orchestration — Progress Tracker

Companion to `2026-07-19-multi-provider-catalog-and-tier-orchestration.md`. Update the checkbox and status the moment a task's steps are ALL checked off and committed in the plan — do not batch updates. Status values: `not started` / `in progress` / `blocked (reason)` / `done (commit sha)`.

**Spec:** `docs/superpowers/specs/2026-07-19-multi-provider-catalog-and-tier-orchestration-design.md`
**Plan:** `docs/superpowers/plans/2026-07-19-multi-provider-catalog-and-tier-orchestration.md`

## Phase 1 — Catalog extension
- [ ] Task 1: `ProviderEntry.category` + `ProviderCatalog.search`/`browse` — not started
- [ ] Task 2: Expand bundled catalog (7 new providers) — not started

## Phase 2 — Model discovery
- [ ] Task 3: `ModelDiscovery.list_models` — not started

## Phase 3 — Tier selection engine
- [ ] Task 4: `TierSelector` (round-robin) — not started
- [ ] Task 5: Wire `TierSelector` into `get_with_cascade` — not started

## Phase 4 — Quota-aware cooldown
- [ ] Task 6: `ProviderConfig.cooldown_hours` — not started
- [ ] Task 7: `CircuitBreaker.open_for` + cooldown injection — not started
- [ ] Task 8: Quota-reset parsing + `cooldown_hours` fallback wiring — not started

## Phase 5 — Guided add-flow (command surface)
- [ ] Task 9: `/provider add` browse/search entry point — not started
- [ ] Task 10: `add-pick` / `add-token` → live discovery — not started
- [ ] Task 11: `add-model` / `add-tier` → persist (shared helper) — not started

## Phase 6 — Lifecycle status UX
- [ ] Task 12: Live status badges (`list`/`menu`) + `status [tier]` — not started
- [ ] Task 13: Live model picker on `edit default_model` + `cooldown_hours` editable — not started

## Phase 7 — Telegram button-chain hardening
- [ ] Task 14: Expired-button message (widen `CallbackRouter` contract) — not started

## Phase 8 — End-to-end verification
- [ ] Task 15: Gateway-driven integration test (full add-flow) — not started
- [ ] Task 16: Telegram button-chain integration test — not started
- [ ] Task 17: TUI parity check — not started

## Final verification
- [ ] Full suite (`uv run pytest`) green
- [ ] `ruff check src/` clean
- [ ] `mypy src/` clean

---

**Overall status:** not started (0/17 tasks)
