# Epic C2-6 Report — Slash-command honesty/contract fixes

Branch: `feat/slash-command-overhaul`
Date: 2026-06-19

---

## Summary

Five atomic commits, one per command. All 26 tests pass (including the 29-command reachability guard). Ruff clean on all touched files.

---

## Commit 1 — `71f02635` — `/browser profile delete` reports real outcome

**Fix chosen: behavior fix.**

`_profile_subcmd` used `contextlib.suppress(OSError)` then returned `"Deleted profile '<x>'"` unconditionally — claimed success even when `shutil.rmtree` raised or silently no-oped. Fix: replace with an explicit `try/except OSError` that logs and returns `"✗ Failed to delete profile"`, plus a post-delete existence check (`target_dir.exists()`) that catches any silent rmtree no-op. Removed unused `contextlib` import.

**Test file:** `tests/journeys/commands/test_browser_command.py` (3 tests)

| Test | Result |
|------|--------|
| `test_browser_profile_delete_success` | PASSED |
| `test_browser_profile_delete_not_found` | PASSED |
| `test_browser_profile_delete_rmtree_failure_honest` — patches `shutil.rmtree` to raise OSError, asserts no `"Deleted"` in result | PASSED |

---

## Commit 2 — `4fec2ea6` — `/urgent` broadcasts to real channel roster

**Fix chosen: real-roster fix.**

`UrgentCommand.__init__` defaulted `channels=["cli"]` and `assembly.py` passed no channel list → only CLI received urgent broadcasts despite the description saying "all channels". Fix: add `_resolve_channels()` that calls `ChannelRegistry.instance().all()` at dispatch time, falling back to `["cli"]` only when the registry is empty. The description is updated to say "all **registered** channels". An explicit `channels` override is still accepted at construction time for tests.

**Test file:** `tests/journeys/commands/test_urgent_command.py` (5 tests)

| Test | Result |
|------|--------|
| `test_urgent_targets_live_registry_channels` — registers cli+telegram adapters, asserts `deliver` called twice with both names | PASSED |
| `test_urgent_fallback_to_cli_when_registry_empty` | PASSED |
| `test_urgent_not_configured_when_router_none` | PASSED |
| `test_urgent_requires_message` | PASSED |
| `test_urgent_description_says_all_registered_channels` — asserts "registered"/"all" in description, "cli" not hard-coded | PASSED |

---

## Commit 3 — `8eb36dd2` — `/quiet` scope matches its wording

**Fix chosen: wording fix.**

The `notification_overrides` table (migration 0019) has no `session_id` column — the override is global, not per-session. The module docstring said "session-scoped row", the class docstring said "per-session", and the `description` property said "Override quiet hours for the current session." Corrected all three to say "global" / "process-wide". No schema or behavioral change.

**Test file:** `tests/journeys/commands/test_quiet_command.py` (6 tests)

| Test | Result |
|------|--------|
| `test_quiet_description_does_not_claim_session_scope` | PASSED |
| `test_quiet_description_indicates_global_scope` | PASSED |
| `test_quiet_inserts_override_row` — real DB, checks row fields | PASSED |
| `test_quiet_override_is_global_not_per_session` — two sessions both write rows; confirms no `session_id` column exists in result rows | PASSED |
| `test_quiet_category_override` | PASSED |
| `test_quiet_invalid_time_format` — asserts no row inserted on bad input | PASSED |

---

## Commit 4 — `13387cb2` — `/tier` docstring matches owner scoping

**Fix chosen: wording fix.**

The module docstring said the tier "propagates across all channels for the same owner" but `_owner_key_for_state` returns `state.session_id` — the preference is session-scoped. Corrected the module docstring, added an explanatory note about the deferred cross-channel owner-threading, and updated the `description` property to include "session-scoped, not cross-channel". Behavior unchanged.

**Test file:** `tests/journeys/commands/test_tier_command.py` (6 tests)

| Test | Result |
|------|--------|
| `test_tier_description_does_not_claim_cross_channel_owner` | PASSED |
| `test_tier_description_indicates_session_scope` | PASSED |
| `test_tier_set_and_read_back_same_session` | PASSED |
| `test_tier_different_sessions_are_independent` — session B has `get_session_tier=None` after session A sets powerful | PASSED |
| `test_tier_unknown_tier_rejected` | PASSED |
| `test_tier_show_current_when_no_arg` | PASSED |

---

## Commit 5 — `d458ecb9` — `/memory delete` resolves prefixes like `forget`

**Fix chosen: behavior fix.**

`_delete()` split the raw arg and passed `parts[0]` directly to `forget_fact(bridge, fact_id)` without prefix resolution. This called `bridge.delete(<raw_prefix>)` which silently did nothing (no fact has that literal ID), yet could still echo `"✓ Deleted <prefix>"` — false success. Meanwhile `_forget()` used `find_staged_by_id` for proper prefix resolution. Fix: `_delete()` now calls `find_staged_by_id` first; returns honest `"✗ /memory delete: no fact matches prefix '<x>'"` when nothing is found; echoes `fact.fact_id` (the resolved full UUID) in success messages. The confirmation prompt also shows the full ID for the follow-up command.

**Test file:** `tests/journeys/commands/test_memory_delete_prefix.py` (4 tests)

| Test | Result |
|------|--------|
| `test_memory_delete_prefix_resolves_and_deletes` — stages fact, uses 8-char prefix, asserts full UUID in result and in `bridge.delete_calls` | PASSED |
| `test_memory_delete_bogus_prefix_returns_not_found` — asserts no `"✓"/"Deleted"` and `bridge.delete_calls == []` | PASSED |
| `test_memory_delete_without_yes_shows_confirmation` — no deletion, prompt contains full UUID | PASSED |
| `test_memory_delete_parity_with_forget` — same prefix via both `delete` and `forget` both call `bridge.delete` with the full fact UUID | PASSED |

---

## Reachability guard

`tests/journeys/commands/test_all_29_reachable.py` and `test_reachability_guard.py` — all 29 commands still register. Confirmed 26/26 total passing in the combined run.

---

## No concerns or skips
