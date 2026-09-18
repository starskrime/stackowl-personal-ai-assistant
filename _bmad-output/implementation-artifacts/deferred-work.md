# Deferred work

- source_spec: `_bmad-output/implementation-artifacts/spec-q29-control-plane-default-credential.md`
  summary: Setup-code (and any owner) delivery resolves only Telegram with exactly one allowed user id; Slack/Discord/WhatsApp-only installs get no remote copy.
  evidence: `notifications/recipient.py` `resolve_owner_addresses` carries a TODO for other channels (pre-existing); review BH2-3.
- source_spec: `_bmad-output/implementation-artifacts/spec-q29-control-plane-default-credential.md`
  summary: `config/provider_tier_migration.py` rewrites stackowl.yaml in place with truncation (not atomic).
  evidence: `provider_tier_migration.py:81` `open("w")` + dump (pre-existing); review BH2-7.
- source_spec: `_bmad-output/implementation-artifacts/spec-q29-control-plane-default-credential.md`
  summary: Possible race if gateway and core both import the legacy control_plane.password at the same moment (UNVERIFIED, medium if true).
  evidence: settle by confirming whether both processes construct Settings() concurrently at boot or during config reload; review EC2-12.

### DW-1: test_ca_private_key_is_dropped_and_collected_before_setup_returns only verifies that ca.setup()'s source contains "del ca_key" and "gc.collect()", not that the CA private key is actually unreachable
origin: spec-deferred 7e00dfa84109
location: spikes/bridge/tests/test_ca.py:88-107 (test), spikes/bridge/bridge_spike/ca.py:132-154 (setup())
source_spec: `spec-1-1-trust-the-bridge-s-own-certificate-on-your-phone-at-home.md`
reason: The cryptography library's Rust-backed EllipticCurvePrivateKey supports neither weakref.ref() (raises TypeError) nor Python's cyclic GC introspection (never appears in gc.get_objects(), alive or not), so there is no available tool to observe that specific object's liveness from outside setup(). If the key were in fact retained (e.g. a future change stashes it in a module-level cache while leaving "del ca_key"/"gc.collect()" textually untouched), no existing test would catch it. What would settle it: a runtime-observable memory-safety check for a Rust-backed key object, or a different library/approach that exposes explicit key-zeroing.
status: open

### DW-2: macOS mDNS advertising via `dns-sd -P` (proxy-record mode) is verified only by the shape of the constructed argv, never against real macOS hardware, so it is unknown whether `<install-name>.local`
origin: spec-deferred 80022a93adee
location: spikes/bridge/bridge_spike/mdns.py:50-58 (build_command, Darwin branch), spikes/bridge/tests/test_mdns.py:34-42 (test)
source_spec: `spec-1-1-trust-the-bridge-s-own-certificate-on-your-phone-at-home.md`
reason: spikes/bridge/bridge_spike/mdns.py's own docstring for the Darwin branch already discloses "Not verified on real macOS hardware -- based on dns-sd's documented -P proxy-record option; Story 1.6 covers real-device verification." tests/test_mdns.py only asserts the argv shape (test_build_command_darwin_uses_proxy_record_mode), never runs dns-sd. What would settle it: run the kit on real macOS hardware and confirm `dns-sd -P ...` makes `<install-name>.local` resolve, e.g. via `dscacheutil -q host -a name <name>.local` or `ping <name>.local`.
status: open

### DW-3: AD-19 says a push-subscription row "is deleted on revocation," but no device-revocation/unenroll route exists anywhere in the kit.
origin: spec-deferred e263d975f876
location: spikes/bridge/bridge_spike/server.py, spikes/bridge/bridge_spike/push.py
source_spec: `spec-1-3-push-microphone-and-the-away-from-home-summary-on-your-devices.md`
severity: low
reason: Confirmed by reading server.py's full route list: no revoke/unenroll endpoint exists for any resource type (passkey device, bearer token, or push subscription). This is a pre-existing gap in the device/token lifecycle dating back to Story 1.2 (which introduced signed-in devices but never a revoke path), not something Story 1.3 introduced or worsened, and it is outside this story's captured intent (the epics.md AC list for Story 1.3 never asks for revocation).
status: open

### DW-4: A signed-request construction (method/path/timestamp/nonce/signature over a SHA-256 body hash) is independently reimplemented in check.py's `_sign_stream_request`, `test_server_stream_routes.py`'s
origin: spec-deferred eb052b5d5ac3
location: spikes/bridge/bridge_spike/check.py, spikes/bridge/tests/test_server_stream_routes.py, spikes/bridge/tests/test_webtransport_server.py
source_spec: `spec-1-4-a-live-stream-over-webtransport-with-automatic-sse-fallback-on-your-phone.md`
severity: low
reason: Confirmed by reading all three call sites: each hand-builds the same method/path/timestamp/nonce/body-hash message and ECDSA signature independently. This duplication convention predates Story 1.4 -- test_server_auth_routes.py and test_server_push_routes.py already duplicate the identical pattern from Stories 1.2/1.3 -- so Story 1.4 only followed the kit's own existing (imperfect) test/check convention rather than introducing it.
status: open

### DW-5: `_handle_stream_sse`'s broadened (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) catch around the SSE write loop has no test exercising any of the three exception types.
origin: spec-deferred 691fdb5c90aa
location: spikes/bridge/bridge_spike/server.py:_handle_stream_sse, spikes/bridge/tests/test_server_stream_routes.py
source_spec: `spec-1-4-a-live-stream-over-webtransport-with-automatic-sse-fallback-on-your-phone.md`
severity: medium
reason: Confirmed by reading spikes/bridge/tests/test_server_stream_routes.py in full: every test either reads a fixed, bounded number of envelopes to completion or asserts a 400/401/503 on malformed/unauthenticated requests -- none aborts the client mid-stream or mocks response.write to raise any of the three caught types, so a regression narrowing the except tuple back to one type (or over-broadening it) would go uncaught. A faithful test needs to exercise the exception path from inside _handle_stream_sse specifically -- proving the connection merely drops is not enough, since aiohttp's own framework-level exception handling already guarantees that regardless of this except clause -- which needs either a hand-built aiohttp.web.Request via make_mocked_request (unlike this file's established TestClient/TestServer pattern) or an intrusive monkeypatch of web.StreamResponse.write.
status: open

### DW-6: `spikes/bridge/results/B1-desktop-chrome.json` scores as the required `desktop-chrome` row (PASS/confirmed) even though its own `note` field reads "Automated build-host Chromium check."
origin: spec-1-6-verdicts-decide-what-gets-built.md, Review Triage Log (Blind Hunter #4)
location: spikes/bridge/results/B1-desktop-chrome.json
source_spec: `spec-1-6-verdicts-decide-what-gets-built.md`
severity: medium
reason: It's build-host automation, not an owner-driven real-device run, despite matching the required-row filename convention exactly. Confirmed on disk: the file is present today (gitignored, pre-existing before this story's diff -- the spec's own Code Map cites it as "present on disk today") and `verdict.py` correctly scores it per its own documented contract (score the file's own recorded fields, match by filename only) -- this is a pre-existing data-provenance gap in the real `results/` directory, not a `verdict.py` defect, and the story's Boundaries explicitly forbid building a new submission/validation mechanism to detect it. What would settle it: the operator replacing this file with a genuine real-device submission before any real `epic-1-verdicts.md` commit to `main`.
status: open

### DW-7: `DurableTaskStore.create()` is called directly (bypassing `enqueue()`) from several production sites, so tasks created that way never get `task.enqueued`/`task.claimed` journal rows
origin: spec-2-1-the-journal-records-task-events-in-the-same-transaction-as-the-change.md, Review Triage Log (verification-gap, "Other findings")
location: src/stackowl/pipeline/durable/store.py (DurableTaskStore.create()); callers at src/stackowl/pipeline/durable/react_runner.py:131, src/stackowl/pipeline/durable/executor.py:150, src/stackowl/pipeline/durable/task_runner.py:178 (via goal_execution.py), src/stackowl/memory/rollover_summary_handler.py:399
source_spec: `spec-2-1-the-journal-records-task-events-in-the-same-transaction-as-the-change.md`
severity: medium
reason: Confirmed these call sites pre-date Story 2.1 and construct `DurableTask(status="running", ...)` directly via `store.create(task)`, skipping `enqueue()` entirely -- so a task created this way can go through its whole lifecycle with no `task.enqueued`/`task.claimed` journal row, only picking up `task.finished`/`task.dead_lettered` if it later reaches one of the wired terminal methods. This multi-entry-point shape pre-dates Story 2.1 and is not something it introduced; it is exactly the coverage gap Story 2.10 ("Nothing escapes the journal") exists to close via its coverage tripwire (every migration-created table listed in the registry with its event types, or marked `unjournaled` with a reason). What would settle it: Story 2.10 either wiring `create()`'s callers through the journal too, or explicitly marking this an accepted `unjournaled` path with a stated reason.
status: open

### DW-8: `pyproject.toml` carries a pre-existing malformed dependency constraint directly adjacent to Story 2.1's new `uuid-utils` line
origin: spec-2-1-the-journal-records-task-events-in-the-same-transaction-as-the-change.md, Review Triage Log (blind-hunter)
location: pyproject.toml (dependencies list, pypdf entry)
source_spec: `spec-2-1-the-journal-records-task-events-in-the-same-transaction-as-the-change.md`
severity: low
reason: `"pypdf>=5.4,<7,<7"` (a duplicated upper-bound constraint) is present at baseline_revision `6d4aed27933dc0f97d77f96de225ca7dd48d6cdd`, unrelated to this story's `uuid-utils` addition on the next line, and fixing an unrelated dependency constraint is out of this story's scope. What would settle it: a trivial follow-up dropping the duplicated `<7`.
status: open

### DW-9: `fail_and_requeue`'s permanent-branch UPDATE still has no compare-and-set status guard, so two genuinely concurrent calls for the same task_id could each record a `task.dead_lettered` row
origin: spec-2-1-the-journal-records-task-events-in-the-same-transaction-as-the-change.md, Review Triage Log (self-reported by the implementation subagent during the patch pass)
location: src/stackowl/pipeline/durable/store.py (DurableTaskStore.fail_and_requeue(), permanent/exhausted branch)
source_spec: `spec-2-1-the-journal-records-task-events-in-the-same-transaction-as-the-change.md`
severity: low
reason: The review pass's rowcount-guard patch (see the story's Review Triage Log) fixed the more severe bug -- an UPDATE matching 0 rows still unconditionally recording a journal event -- by checking `cursor.rowcount == 1` before calling `journal_record`. That patch does not close a narrower, genuinely-concurrent case: the UPDATE's WHERE clause (`task_id=? AND owner_id=?`) carries no status guard, so if two callers both reach this branch for the same task_id (e.g. a stale worker's call finally landing after `reclaim_expired()` already handed the lease to a new worker that also fails the task), each UPDATE still matches exactly 1 row and each would record its own `task.dead_lettered` event -- a real duplicate-recording race, though the task's own final state ('dead_letter') stays correct either way. Confirmed by reading the patched UPDATE's WHERE clause directly. What would settle it: deciding what a "lost the race" `fail_and_requeue` caller should observe (silent no-op, a warning, or an exception) and adding a compare-and-set guard (e.g. `AND status != 'dead_letter'`) accordingly -- a small design decision, not a mechanical fix, which is why it was not patched in this pass.
status: open

### DW-10: `task_events.py`'s comment on `task.dead_lettered` says it is "one of the two NAMED needs_you/high examples", but AD-5 now names three
origin: spec-2-2-every-event-reads-as-a-plain-sentence-and-knows-whether-it-needs-the-owner.md, Review Triage Log (blind-hunter)
location: src/stackowl/journal/task_events.py (comment above the task.dead_lettered registration)
source_spec: `spec-2-2-every-event-reads-as-a-plain-sentence-and-knows-whether-it-needs-the-owner.md`
severity: low
reason: Pre-existing comment from Story 2.1 (unchanged context in Story 2.2's diff, not modified by it). `ARCHITECTURE-SPINE.md`'s current AD-5 text: "Only explicit give-up or unhealed event types (such as `heal.exhausted`, `task.dead_lettered`, `job.parked`)... are `needs_you` at `high`" -- three named examples via "such as", not two, likely because AD-5 gained a third example after Story 2.1's comment was written. Cosmetic only: the comment does not affect behavior, only its own accuracy. What would settle it: a one-line comment fix updating "two" to "three" (or naming all three).
status: open
