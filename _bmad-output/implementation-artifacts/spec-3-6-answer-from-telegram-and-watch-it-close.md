---
title: 'Answer from Telegram and watch it close'
type: 'feature'
created: '2026-09-19'
status: 'in-progress'
review_loop_iteration: 0
followup_review_recommended: false
baseline_revision: '3d0ec5ad1d418ca159965117643f1ff0be2332a1'
context: [
  '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md',
  '{project-root}/_bmad-output/implementation-artifacts/spec-3-5-the-consent-address-survives-the-gateway-core-link.md',
]
warnings: [oversized]
deferred: []
---

<intent-contract>

## Intent

**Problem:** `approval`/`question` needs_you items already deliver to Telegram and resolve through `needs_you.resolve()` (Story 3.3), but only while a live turn is blocked waiting: the approval button carries a raw, unmapped `rid` (never validated against the item's version/digest at resolve time), `incident`/`alert` items (no live waiter -- `consent.channel_unreachable`, `budget.warning`, `heal.exhausted`, `job.parked`) are never delivered to Telegram at all, and a Telegram message is only ever edited when TELEGRAM ITSELF resolved the item -- a local prompter timeout, the periodic expiry sweep, or a future non-Telegram surface leaves a stale, still-tappable keyboard forever.

**Approach:** Thread the already-open `item_id` through the existing consent (and clarify) round trip so the gateway-side prompter can register its own sent message against it; make the version/digest check in `needs_you.resolve()` (built in 3.2, never called with it) real by passing `expected_version`/`expected_digest` from `ConsentPolicy.request()`/`ClarifyGateway.wait_for_answer()`; add a small gateway-side `incident`/`alert` pusher that sends the narrator's `full` text on `needs_you.opened`; and add one generic, gateway-side `needs_you.resolved` hook (in `GatewayLink._deliver_journal_row`, which already sees every committed row) that edits ANY tracked item's Telegram message to the outcome and drops its keyboard, regardless of which surface resolved it.

## Boundaries & Constraints

**Always:**
- `ConsentRequest`/`ConsentRequestFrame` gain `item_id: str | None = None` (mirrors Story 3.5's `reply_target` addition exactly); `PROTOCOL_VERSION` bumps 5->6. `ClarifyAskFrame` gains `needs_you_item_id: str | None = None` (no version bump needed for it — same frame variant already exists, only a new optional field).
- A new bounded, in-memory, gateway-side `NeedsYouMessageRegistry` (module-level singleton, `remember`/`forget`, LRU-bounded like `TelegramConsentPrompter._decided`) maps `item_id -> (chat_id, message_id)`. `TelegramConsentPrompter.prompt()` registers into it (when `req.item_id` is set); the split-mode clarify text delivery (`GatewayLink._deliver_clarify`) and the new incident/alert pusher register into it too.
- `TelegramConsentPrompter.handle_callback`'s existing immediate local edit (`_edit_to_decision`) stays, and now ALSO pops the shared registry for that `item_id` first, so the later cross-surface hook never re-edits the same message with worse (generic) text.
- `GatewayLink._deliver_journal_row` gains one new branch: on `event_type == "needs_you.resolved"`, pop the registry for `event.attrs.item_id`; if found, edit that message to `"{✅|⏳} {narration.full}"` (✅ for `Outcome.OK`, ⏳ for `Outcome.EXPIRED`) with no keyboard. Best-effort — a missing adapter/edit failure is logged and swallowed (matches `_edit_to_decision`'s own fail-open convention).
- `ConsentPolicy.request()` computes `expected_version`/`expected_digest` once, right after opening the item, and passes both into its existing `needs_you.resolve(...)` call. `ClarifyGateway.wait_for_answer()` does the same for symmetry.
- `incident`/`alert` items only: a new gateway-side handler reacts to `needs_you.opened` for those two kinds (approval/question are already delivered by their own live prompters — this handler must not double-deliver them) and sends `narration.full` as plain text to the owner's resolved Telegram chat (`resolve_owner_addresses`, reused, never a new lookup).
- Document the residual risk verbatim in this spec's Design Notes: a hijacked Telegram account can approve irreversible actions (already stated in `docs/agentic-os-dashboard/full-picture.md`/`ARCHITECTURE-SPINE.md`/`epics.md` FR20/A8/NFR49 -- this story's own record cites, not duplicates, that existing decision).
- `device` items: no code change -- already declared as a `NeedsYouKind` member (Story 3.1); confirm in this spec that Telegram delivery ships in Epic 5, per the AC's own text.

**Never:**
- Never build a second opaque-token scheme for approval buttons -- `rid` (already a `uuid4().hex` in `_pending`, already well under Telegram's 64-byte limit) IS the opaque token; it now maps (server-side, via the registry) to `item_id` too. No new callback_data shape, no new `CallbackRouter` prefix.
- Never drive `scripts/dev_ingress.py` as a literal subprocess for the end-to-end proof -- confirmed (this story's own investigation) it cannot simulate a callback_query/button tap and has no split-mode socket awareness; same class of gap spec-3-5's own triage log (IA-1) already reviewed and accepted for the identical reason. Proof instead extends the established in-process real-code/fake-bot-transport pattern (`tests/smoke/test_e0_s1_consent_telegram_smoke.py`) plus the real-socket pattern (`tests/runtime/test_split_consent.py`) for split-mode/group-chat proof.
- Never build real Telegram BUTTON delivery for clarify/question in split mode -- confirmed pre-existing: `GatewayLink._deliver_clarify` already sends plain numbered-list text, not buttons, in split mode; `channels/telegram/clarify.py`'s button resolver is exercised only in mono mode today. Orthogonal, pre-existing gap; log to `deferred-work.md`.
- Never invent a `needs_you.version` bump caller -- confirmed nothing in the codebase bumps it yet (`journal/needs_you.py`'s own docstring). The version/digest CHECK is implemented and tested (via a manufactured stale row), not a new production bump path.
- Never touch `sprint-status.yaml`, never `git push`, never mark this spec `status: done` -- dispatcher-owned per the run's own constraints.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Approval delivered and tapped | live turn opens an `approval` item, Telegram tap = approve | keyboard sent with narrator-consistent text; tap resolves through `needs_you.resolve(expected_version=1, expected_digest=...)`; item closes; message edited locally, registry popped | No error expected |
| Approval expires with no tap | item opens, owner never taps, local timeout + durable expiry both fire | `needs_you.resolved` (outcome=expired) reaches the gateway via `JournalEventFrame`; registry still holds the entry (no tap popped it); message is edited to show "⏳ ..." with no keyboard | No error expected |
| Incident opens with no waiter | `consent.channel_unreachable` opens an `incident` item | gateway sends `narration.full` as plain text to the owner chat; registry remembers `(item_id -> chat_id, message_id)` | Adapter/send failure: logged, swallowed, item stays open until its own expiry |
| Stale digest on tap | item's row digest manually advanced between prompt-build and tap (test-manufactured) | `needs_you.resolve()` returns `refused`; `ConsentPolicy.request()` fails closed (`not_approved`), never silently grants | No exception raised |
| Group/thread delivery | `reply_target=-100123456789` (negative chat id) crosses the real socket | approval buttons and any incident/alert push land on the exact group chat id; edit-on-resolve targets the same chat id | No error expected |

</intent-contract>

## Code Map

- `src/stackowl/ipc/frames.py:33` -- `PROTOCOL_VERSION` 5->6; `ConsentRequestFrame` (line ~250) gains `item_id: str | None = None`; `ClarifyAskFrame` (line ~224) gains `needs_you_item_id: str | None = None`.
- `src/stackowl/tools/consent.py` -- `ConsentRequest` gains `item_id`; `ConsentPolicy.request()` (~line 930-1045) threads `item_id=item_id` into `ConsentRequest(...)`, computes `expected_version=1`/`expected_digest=needs_you.compute_item_digest(NeedsYouKind.APPROVAL, None, 1)` once record_consent_requested returns a real `item_id`, and passes both into its existing `needs_you.resolve(...)` call.
- `src/stackowl/runtime/socket_consent.py:59-69` -- `SocketConsentPrompter.prompt()` adds `item_id=req.item_id,` to the outgoing frame.
- `src/stackowl/runtime/gateway_link.py` -- `_handle_consent` (~line 917) rebuilds `ConsentRequest` with `item_id=frame.item_id`; `_deliver_clarify` (~line 866) registers `(needs_you_item_id -> chat_id, message_id)` into the new registry when `frame.needs_you_item_id` is set and the send returns a message identity; `_deliver_journal_row` (~line 756-864) gains the new `needs_you.resolved` cross-surface edit branch, and a new `needs_you.opened`-for-incident/alert branch that calls the new pusher.
- `src/stackowl/channels/telegram/needs_you_registry.py` -- NEW: `NeedsYouMessageRegistry` (bounded LRU `remember`/`forget`/`peek`), module-level singleton + `get_registry()`/`set_registry_for_tests()`, mirrors `TelegramConsentPrompter._decided`'s bound.
- `src/stackowl/channels/telegram/needs_you_notifier.py` -- NEW: `TelegramIncidentAlertNotifier.deliver_opened(item_id, kind, chat_id, text)` -- plain `send_text`, captures the returned message id, calls `registry.remember(...)`.
- `src/stackowl/channels/telegram/consent.py` -- `TelegramConsentPrompter.prompt()` registers into the shared registry after a successful send (only when `req.item_id` is set); `handle_callback`'s `_edit_to_decision` path pops the registry entry for the resolved `item_id` before its own local edit.
- `src/stackowl/interaction/clarify_gateway.py` -- `ask()` (blocking path) threads `needs_you_item_id` to whatever the adapter call needs to register with (frame field, for the split-mode path); `wait_for_answer()` passes `expected_version`/`expected_digest` into its `needs_you.resolve(...)` call, computed the same way as consent's.
- `src/stackowl/journal/needs_you.py` -- no signature change; `compute_item_digest` and `resolve(expected_version=..., expected_digest=...)` already exist and are reused as-is (Story 3.2).
- `src/stackowl/startup/orchestrator.py` -- `_phase_gateway`: construct/wire the new registry + incident/alert notifier once (mirrors `needs_you.set_db_pool`/`TelegramConsentPrompter` construction sites already in this function); pass the settings-resolved owner chat id lazily via `resolve_owner_addresses`.
- **Tests to extend:** `tests/smoke/test_e0_s1_consent_telegram_smoke.py` (new: expiry-edit case), `tests/runtime/test_split_consent.py` (new: `item_id` survives the frame round trip; group chat id), `tests/ipc/test_codec.py` (`ConsentRequestFrame`/`ClarifyAskFrame` round trip with the new fields), `tests/channels/telegram/test_a_late_approval_click_is_answered.py`-adjacent new tests for stale-digest refusal, a new `tests/channels/telegram/test_needs_you_incident_alert_delivery.py` for incident/alert push + resolve-edit.

## Tasks & Acceptance

**Execution:**
- `src/stackowl/ipc/frames.py` -- add `item_id`/`needs_you_item_id` fields, bump `PROTOCOL_VERSION` -- AC1.
- `src/stackowl/tools/consent.py`, `src/stackowl/runtime/socket_consent.py`, `src/stackowl/runtime/gateway_link.py` -- thread `item_id`; add `expected_version`/`expected_digest` to the resolve call -- AC1, AC3.
- `src/stackowl/channels/telegram/needs_you_registry.py`, `needs_you_notifier.py` -- new bounded registry + incident/alert pusher -- AC2.
- `src/stackowl/channels/telegram/consent.py`, `interaction/clarify_gateway.py` -- register sent messages; thread version/digest -- AC1, AC3, AC4.
- `src/stackowl/runtime/gateway_link.py::_deliver_journal_row` -- generic cross-surface edit-on-resolve branch -- AC4.
- `src/stackowl/startup/orchestrator.py` -- wire the new registry/notifier in `_phase_gateway` -- AC2.
- This spec -- state the residual risk (hijacked Telegram account) -- AC5.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- log: split-mode clarify buttons (pre-existing gap), literal `dev_ingress.py` proof (structural gap, mirrors spec-3-5 IA-1).

**Acceptance Criteria:**
- Given an `approval` item, when delivered to Telegram, then the buttons message carries the deterministic, never-model-written text already built by `TelegramConsentPrompter`, and each button's opaque `rid` maps server-side (via the registry, and via `item_id` now threaded end-to-end) to the item id; a tap resolves only through `needs_you.resolve()`, now checked against version/digest.
- Given a `question`, `incident` or `alert` item, when delivered, then Telegram receives the narrator's `full` text for it (FR22) -- `incident`/`alert` via the new pusher, `question` via its existing (pre-3.6) delivery path.
- Given a stale/unknown/expired token or a changed request, when a tap arrives, then it is refused via `needs_you.resolve()`'s own conditional check (never an application-level guess), and the item is left/shown at its current true state.
- Given an item resolved from any surface, including expiry, when the resolution commits, then the Telegram message for that item is edited to show the outcome and loses its buttons -- proven for a tap (existing, local) AND for a non-tap resolution (new, cross-surface hook).
- Given the documentation of Telegram approvals, when this story lands, then it states the residual risk: a hijacked Telegram account can approve irreversible actions.
- Given `device` items, when this story lands, then they remain declared as a kind with no Telegram delivery required yet (Epic 5).

## Design Notes

**Why no wire change was needed for the version/digest CHECK itself, but `item_id` still had to cross the wire.** The check's authority (`needs_you.resolve()`'s conditional `UPDATE ... AND version = ?`) always runs CORE-side, where `item_id`/`version` are already known locally (core minted them) -- no round trip needed for correctness. `item_id` crosses the wire anyway because the GATEWAY-side `TelegramConsentPrompter` is what sends the message and must know which `item_id` to register into the shared registry, so a later cross-surface `needs_you.resolved` push can find and edit that exact message.

**Why the registry, not a DB column.** `needs_you`'s column list is closed by design (AD-28, migration 0150's own comment) -- no `message_id` column. A gateway-local, bounded, in-memory map mirrors the existing precedent (`TelegramConsentPrompter._pending`/`_decided`) and the epic's own stated scope: only the in-memory-turn case is guaranteed durable in this epic; a gateway restart losing unsent edits is consistent with that boundary, not a regression.

**Residual risk (verbatim, cited from existing architecture docs, not newly invented here):** a hijacked Telegram account can approve irreversible actions, since a Telegram answer from the owner's allowlisted chat may resolve any approval item, irreversible and consequential ones included, from anywhere (`docs/agentic-os-dashboard/architecture/ARCHITECTURE-SPINE.md` AD-27/AD-28 section; `docs/agentic-os-dashboard/epics.md` FR20/NFR49; `docs/agentic-os-dashboard/full-picture.md` A8).

## Verification

**Commands:**
- `uv run pytest tests/ipc/test_codec.py tests/runtime/test_split_consent.py -x` -- expected: all pass, including the `item_id`/`needs_you_item_id` round-trip tests.
- `uv run pytest tests/channels/telegram/ -x` -- expected: all pass, including new incident/alert + stale-digest + cross-surface-edit tests.
- `uv run pytest tests/smoke/ -x` -- expected: all pass, including the new expiry-edit smoke case.
- `uv run pytest tests/tools/ tests/journal/ tests/interaction/ tests/runtime/ tests/ipc/ tests/startup/ -x` -- expected: all pass (no regression).
- `./scripts/tripwires.sh` -- expected: clean, before every commit.
