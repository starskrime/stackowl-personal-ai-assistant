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
location: src/stackowl/pipeline/durable/store.py (DurableTaskStore.create()); callers at src/stackowl/pipeline/durable/react_runner.py:131, src/stackowl/pipeline/durable/executor.py:150, src/stackowl/pipeline/durable/task_runner.py:178 (via goal_execution.py), src/stackowl/memory/rollover_summary_handler.py:400-404
source_spec: `spec-2-1-the-journal-records-task-events-in-the-same-transaction-as-the-change.md`
severity: medium
reason: Confirmed these call sites pre-date Story 2.1 and construct `DurableTask(status="running", ...)` directly via `store.create(task)`, skipping `enqueue()` entirely -- so a task created this way can go through its whole lifecycle with no `task.enqueued`/`task.claimed` journal row, only picking up `task.finished`/`task.dead_lettered` if it later reaches one of the wired terminal methods. This multi-entry-point shape pre-dates Story 2.1 and is not something it introduced; it is exactly the coverage gap Story 2.10 ("Nothing escapes the journal") exists to close via its coverage tripwire (every migration-created table listed in the registry with its event types, or marked `unjournaled` with a reason). What would settle it: Story 2.10 either wiring `create()`'s callers through the journal too, or explicitly marking this an accepted `unjournaled` path with a stated reason. Story 2.10 finding (2026-09-18): `tasks` IS registry-covered -- `journal/task_events.py` now sets `EventTypeSpec(table="tasks")` on all four `task.*` types (`journal/registry.py::EventTypeSpec.table`, `EventRegistry.tables_covered()`), so `journal/coverage.py`'s table-diff coverage tripwire correctly reports `tasks` as covered and cannot see this gap -- AD-3's coverage tripwire operates at table granularity only, per the epic's own literal text ("a coverage check fails on any migrated table with no registered events or 'unjournaled' excuse"), and this gap is at the CALL-SITE level, not the table level. The real blocker to closing it: reading `pipeline/durable/store.py`'s `enqueue()` and `create()` bodies directly confirms `enqueue()`'s transaction does an additional loop-contract UPDATE (the CAS/lease bookkeeping `enqueue()` performs as part of making a row claimable) that these 4 direct-`create()` callers' already-running tasks never populate -- so blindly switching `react_runner.py:131`, `executor.py:150`, `task_runner.py:178-211` and `rollover_summary_handler.py:400-404` to call `enqueue()` instead would misrepresent their already-running lifecycle, not just add a missing journal row. Story 2.10 deliberately does not patch these 4 call sites (a live task-execution hot path, out of this story's literal AC scope). What would settle it now: a follow-up story that reconciles `enqueue()`'s loop-contract UPDATE against `create()`'s "already running" shape (e.g. a narrower `enqueue()` variant, or a record-only journal call at each of the 4 call sites) before wiring them through.
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

### DW-11: The gateway does not self-restart (`os.execv`) when it is the older side of a gateway/core Hello mismatch, though epics.md's literal AC text ("the side running the older version restarts under supervision") does not textually exempt it
origin: spec-2-3-gateway-and-core-refuse-to-talk-across-versions.md, planning (frontmatter `deferred`, Design Notes), carried through review (intent-alignment)
location: src/stackowl/startup/orchestrator.py (_supervise_core, _phase_gateway's gateway branch); src/stackowl/runtime/gateway_link.py (GatewayLink)
source_spec: `spec-2-3-gateway-and-core-refuse-to-talk-across-versions.md`
severity: medium
reason: `review-adversary.md`'s adopted AD-33 fix (M11/C3) calls for the gateway to "pause its own journal and identity writes until its code matches `schema_head`, then re-exec." Doing that safely requires (a) the gateway surviving its own `os.execv`, which drops its listening UDS socket and the connected core's link to it (both non-inheritable under Python's PEP 446 default), and (b) teaching core's frame loop to wait-and-reconnect across a gateway restart instead of its current, universal assumption that a dropped gateway connection means "tear myself down" (`orchestrator.py`'s `_core_frame_loop` docstring: "Ends when the gateway hangs up (clean EOF) -- that drives the core's graceful teardown"). Confirmed via `orchestrator.py:4686`'s own comment ("Gateway/mono never arm" the restart_event/CodeWatcher/execv triggers core alone has) that no gateway self-restart primitive exists anywhere in this codebase today. Building (b) is an architectural inversion, not a mechanical addition, on a live production gateway serving the owner's real Telegram bot. Instead: the gateway refuses the link, pauses journal writes, and (after repeated core respawns confirm the fault is its own, bounded by `_MAX_CONSECUTIVE_HELLO_MISMATCHES`) reports the link `degraded` with a remedy telling the operator to restart it manually. What would settle it: a follow-up story that first teaches core to tolerate a mid-session gateway disconnect (wait-and-reconnect instead of tearing down), then adds a bounded, counted gateway self-`os.execv` path mirroring core's own.
status: open

### DW-12: `ipc/frames.py`'s frame-direction comment block still never lists `consent_request`/`consent_response`/`send_file`/`send_ephemeral`/`ephemeral_sent`/`delete_message`'s direction pairing
origin: spec-2-3-gateway-and-core-refuse-to-talk-across-versions.md, Review Triage Log (blind-hunter)
location: src/stackowl/ipc/frames.py (module docstring, frame-direction comment block near the top of the file)
source_spec: `spec-2-3-gateway-and-core-refuse-to-talk-across-versions.md`
severity: low
reason: Confirmed this incompleteness pre-dates Story 2.3 -- the baseline comment already omitted these frames before this story, which only touched the lines naming `hello` and the four deleted frames (`SteerFrame`/`StopFrame`/`QueryRunningFrame`/`RunningStateFrame`). Cosmetic only: the comment does not affect behavior, only its own completeness as a map of real frame traffic. What would settle it: a follow-up comment update enumerating every frame's real direction pairing.
status: open

### DW-13: `tests/runtime/test_split_wiring.py`'s docstring claims "the full split round-trip is already proven by tests/runtime/test_split_link.py over a real socket," which is inaccurate for the core side
origin: spec-2-3-gateway-and-core-refuse-to-talk-across-versions.md, Review Triage Log (verification-gap, "Other findings")
location: tests/runtime/test_split_wiring.py (module docstring, lines 6-7)
source_spec: `spec-2-3-gateway-and-core-refuse-to-talk-across-versions.md`
severity: low
reason: Verified directly -- `grep -rn "CoreLink(" src/stackowl/` returns zero production instantiations, confirming `test_split_link.py` drives the core side through the confirmed-dead `CoreLink` class, not the real orchestrator wiring. `test_split_wiring.py` itself is untouched by Story 2.3, so the docstring's inaccuracy pre-dates it. It becomes more consequential now: Story 2.3's new `_core_frame_loop` Hello-exchange logic is exactly the kind of real orchestrator wiring that false claim would lead a future reader to believe is already covered by "a real socket round-trip," when it is not (Story 2.3 closed part of that specific gap with a dedicated AST-based wiring test, `tests/startup/test_core_hello_wiring.py`, rather than fixing this docstring). What would settle it: correcting the docstring to name what `test_split_link.py` actually covers (the gateway-side link + a dead CoreLink-backed core stub), and/or routing more of `_core_frame_loop`'s real wiring through a genuine two-process or equivalent test.
status: open

### DW-14: Windows named-pipe IPC transport for the gateway<->core link does not exist at all -- NFR32's Windows socket-directory-ACL clause is out of scope
origin: spec-2-4-only-the-real-core-can-connect-to-the-gateway.md, Boundaries ("Never")
location: src/stackowl/ipc/server.py (IpcServer.start, asyncio.start_unix_server), src/stackowl/ipc/client.py (asyncio.open_unix_connection)
source_spec: `spec-2-4-only-the-real-core-can-connect-to-the-gateway.md`
severity: low
reason: `asyncio.start_unix_server`/`asyncio.open_unix_connection` are POSIX-only -- the ENTIRE gateway<->core process split cannot run on Windows today, regardless of this story (confirmed: no Windows named-pipe transport exists anywhere in `ipc/`). NFR32 asks for "the IPC socket/pipe is owner-only," with a named-pipe ACL as the Windows equivalent of this story's `0700` directory chmod, but there is no pipe transport to apply an ACL to. Building one is a new IPC transport layer, not a mechanical addition to this story's peer-PID/link-secret identity checks. What would settle it: a follow-up story building a Windows named-pipe `IpcServer`/`IpcClient` implementation (with `win32pipe`/`win32security` ACL restricting the pipe to the current user), gated behind the same `IpcServer`/`IpcClient` interface this story's POSIX implementation already exposes.
status: open

### DW-15: The gateway<->core split is NON-FUNCTIONAL on macOS as of this story -- `peer_pid()` fails CLOSED (returns `None`, every connection refused) because macOS's peer-credential socket options (`LOCAL_PEERCRED`/`LOCAL_PEEREPID`) are not implemented, only Linux's `SO_PEERCRED`
origin: spec-2-4-only-the-real-core-can-connect-to-the-gateway.md, Boundaries ("Never")
location: src/stackowl/runtime/link_auth.py (peer_pid)
source_spec: `spec-2-4-only-the-real-core-can-connect-to-the-gateway.md`
severity: low
reason: This is not merely an unverified code path -- it is a plain functional regression for any macOS gateway/core install from this story forward: EVERY core connection is refused, unconditionally, because `hasattr(socket, "SO_PEERCRED")` is False on macOS, so `peer_pid()` returns `None` and the caller (`authorize_peer`) refuses the connection, exactly like any other unverifiable-peer case (Spec 2.4's I/O matrix, "Peer credentials unverifiable" row). The refusal is SAFE (fail-closed, never trusts an unverified peer) but the split CANNOT ESTABLISH A LINK ON MACOS AT ALL -- there is no degraded-but-working mode, only refusal. The reason this remains unbuilt rather than shipped anyway: macOS exposes peer-credential lookup through a DIFFERENT socket-option pair (`LOCAL_PEERCRED` returning a `struct xucred`, or `LOCAL_PEEREPID` for the PID specifically) with different option numbers and struct layouts than Linux's `SO_PEERCRED` (`struct ucred`), and no macOS host was available to build and validate those raw option numbers/struct-unpack shapes against a real kernel -- shipping unverified platform-specific `getsockopt` calls for a SECURITY-CRITICAL identity check is worse than the current disclosed gap, since a silently-wrong struct unpack could misread a PID rather than fail closed. What would settle it: a macOS host to build and verify `LOCAL_PEERCRED`/`LOCAL_PEEREPID` support, mirroring `peer_pid`'s existing Linux branch behind a `sys.platform == "darwin"` check.
status: open

### DW-16: No schema-version upcaster framework exists -- journal fan-out (and the narrator) treat a row's `attrs`/`schema_version` verbatim, with nothing to upcast an older row's shape for a newer reader
origin: spec-2-5-split-mode-tui-progress-comes-back-from-the-journal.md, Boundaries ("Never")
location: src/stackowl/journal/fanout.py (read_since -- decodes `attrs` as a plain dict, no version-aware transform); src/stackowl/runtime/gateway_link.py (_deliver_journal_row -- `spec.attrs_model.model_validate(attrs)` against the CURRENT registered model only)
source_spec: `spec-2-5-split-mode-tui-progress-comes-back-from-the-journal.md`
severity: low
reason: AD-3 states evolution is additive-only via `schema_version` + upcasters, "kept until their rows are pruned," and names the narrator/fan-out/snapshot/browser as consumers that must all see the latest version through one. That framework does not exist yet: every event type registered anywhere in the tree today (`journal/task_events.py`, the only emitter wired so far) declares `schema_version=1`, so there is nothing to upcast FROM -- confirmed via `grep -rn "schema_version=" src/stackowl/journal/` returning only `schema_version=1` declarations. Building a real upcaster registry/dispatch now would be speculative against a shape nothing has evolved into. What would settle it: when a future story first bumps some event type's `schema_version` past 1, that story must also add the upcaster registry AD-3 describes, wired into `journal/fanout.py`'s `read_since` (or a shared decode path both fan-out and the narrator's own read call) BEFORE any consumer reads a mixed-version table.

Update (2026-09-18, Story 2.10): Story 2.10 added the registry-level foundation this entry describes -- `EventRegistry.register_upcaster`/`versions_for` (`journal/registry.py`) plus a completeness tripwire (`tests/journal/test_upcaster_tripwire.py`) proving no version with live rows loses its registered model/upcaster. The real dispatch wiring this entry's own "what would settle it" names -- an upcaster path wired into `journal/fanout.py`'s `read_since` (or a shared decode path the narrator's own read call also uses) -- remains DW-16's own stated open item, still owned by whichever future story first bumps a real event type's `schema_version` past 1.
status: open

### DW-17: `journal/retention.py`'s `PROVISIONAL_JOURNAL_RETENTION_DAYS` is a provisional 1-day constant, not the real journal retention setting AD-6 describes
origin: spec-2-6-jobs-heals-and-health-have-a-history.md, Design Notes
location: src/stackowl/journal/retention.py; tests/journal/test_retention_tripwire.py (the only reader)
source_spec: `spec-2-6-jobs-heals-and-health-have-a-history.md`
severity: low
reason: The epic's own Technical Decisions state retention values "stay provisional until Story 2.12's benchmark." Today's REAL production prune windows are `prune_completed_after_days=1` (`config/task_loop_settings.py`, tasks) and `_RUN_HISTORY_RETENTION_DAYS=7` (`scheduler/handlers/db_reclaim.py`, job_runs) -- both owner-authorized and far below AD-6's eventual ~30-day default, so a tripwire compared against 30 would fail immediately against values this story does not own. The constant is set to the tightest existing real window (1 day) so the tripwire is honest without forcing a change to owner-authorized production behaviour. What would settle it: Story 2.11 raises `PROVISIONAL_JOURNAL_RETENTION_DAYS` to the real setting (architecturally ~30 days) and, in the SAME change, reconciles every subsystem prune window this tripwire checks (task/job-run retention) so none is shorter than the new value.

Update (2026-09-18, Story 2.11): delivered exactly what this entry's own "what would settle it" asked for. `journal/retention.py`'s constant is renamed `JOURNAL_RETENTION_DAYS`, derived from the new `JournalSettings()`'s real 30-day default (`config/journal_settings.py`) rather than hand-typed. The real setting now lives in `Settings().journal.retention_days`, read live by the new seeded `journal_prune` job (`scheduler/handlers/journal_prune.py`), which deletes `journal_events` rows past retention in bounded batches under `secure_delete`, excludes registered retention holds, and checkpoints the WAL. In the SAME change, both subsystem prune windows `tests/journal/test_retention_tripwire.py` checks were reconciled to 30: `TaskLoopSettings.prune_completed_after_days` (1->30) and `db_reclaim._RUN_HISTORY_RETENTION_DAYS` (7->30). See DW-30 for a caveat this reconciliation surfaced: the `job_runs` raise satisfies the pre-existing tripwire, not a direct AD-4 reference from any journal event.
status: resolved

### DW-18: `job.started` can be left unresolved (no follow-up event) when a job's handler isn't registered at claim time
origin: spec-2-6-jobs-heals-and-health-have-a-history.md, Review Triage Log (adversarial-review)
location: src/stackowl/scheduler/scheduler.py (`_run_job`); src/stackowl/scheduler/scheduler_mutations.py (`run_now`)
source_spec: `spec-2-6-jobs-heals-and-health-have-a-history.md`
severity: low
reason: After the pending->running CAS claim wins and `job.started` commits, both `_run_job` and `run_now` look up the claimed job's handler in the registry; when it is not found (a narrow boot-ordering race), the row is reverted to `pending` via a plain write with no `conn=` and no journal event. A journal reader sees an orphaned `job.started` with no counterpart until the job is next claimed. Confirmed by reading both call sites directly -- not a double-write, not data corruption, and the race window (handler registration ordering at boot) is narrow. What would settle it: wrap the revert-to-pending write in `self._db.transaction()` and record a `job.failed` (or a new, more precise event type) alongside it, mirroring every other state-change-plus-journal-event site this story already wired.
status: open

### DW-19: `journal/turn_budget.py`'s `PROVISIONAL_TURN_EVENT_BUDGET` (100) is a placeholder, not a measured per-turn write budget
origin: spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md, Code Map
location: src/stackowl/journal/turn_budget.py; tests/journal/test_turn_budget.py (the only reader)
source_spec: `spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md`
severity: low
reason: The epic's own Technical Decisions state per-turn/command write budgets "stay provisional until Story 2.12's benchmark." No measured per-turn model/tool/delegation count exists anywhere in this codebase yet -- this story is what FIRST wires the three call sites (`providers/base.py`, `pipeline/steps/execute.py`, `owls/a2a_delegation.py`) that would produce one, so there is nothing to anchor a tighter number to (unlike `journal/retention.py`'s Story 2.6 precedent, which anchored to a real production setting). 100 is a round, deliberately generous ceiling that no known-ordinary turn should cross while still catching a genuine runaway loop. What would settle it: Story 2.12's benchmark sets the real value from measurements on the platform's own hardware.

Cross-reference (2026-09-18, Story 2.11): epics.md's Story 2.11 AC4 (the write-budget WARNING/degrade acceptance criterion) was deliberately left untouched by that story. Its remaining gaps are already chartered here and at DW-21 (no decay/reset path for the budget-exceeded signal) and DW-22 (only one of AD-38's two dimensions/scopes implemented) -- all three explicitly deferred to Story 2.12, which is where the real budget number and its mechanics are decided together. Story 2.11 added an UNRELATED, independent write-budget-adjacent signal of its own (the journal's WAL-file-size budget, `journal/health.py`'s `wal_over_budget_streak`) but did not touch this per-turn event-count budget or its mechanics.
status: open

### DW-20: `journal/turn_events.py`'s record_ref table is named `turn_action_records`, not `turn_decisions` as spec-2-7's own Code Map names it -- a genuine schema-name collision, corrected during implementation
origin: spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md, Code Map (`journal/turn_events.py`, `db/migrations/0146_turn_decisions.sql`)
location: src/stackowl/db/migrations/0146_turn_action_records.sql; src/stackowl/journal/turn_events.py; src/stackowl/health/store_cadence.py
source_spec: `spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md`
severity: medium
reason: A table named `turn_decisions` ALREADY EXISTS -- migration 0071 (`session_id TEXT PRIMARY KEY, trace_id, created_at REAL, decisions_json TEXT`), actively written and read by `pipeline/decision_store.py::TurnDecisionStore` (one row per session, upserted, backing the ADR-7 `/explain` surface). The spec's own Code Map names its NEW table `turn_decisions` too, echoing AD-4's "per-turn decisions" example verbatim, without noticing the collision (confirmed by grep: `turn_decisions` appears in `db/migrations/0071_turn_decisions.sql`, `db/migrations/0093_session_key_rename.sql`, `pipeline/decision_store.py` and `commands/explain_command.py`, all pre-dating this story). `CREATE TABLE IF NOT EXISTS turn_decisions (id TEXT PRIMARY KEY, trace_id, kind, identifier, outcome, duration_ms, error_code, occurred_at)` against that live schema would silently no-op (the existing table already exists, under a completely different column set), and every INSERT this story's three recording helpers make would then fail at runtime with "no such column" -- caught by the helpers' own `except Exception` (B5), so the WHOLE FEATURE would silently never record a single row while looking, on a code read, exactly like it should work. Not a defer -- fixed directly during implementation: the new table is named `turn_action_records` instead, with every other element of the design (columns, index, the `record_ref` it backs, the `store_cadence.py` HOT declaration) unchanged from the spec. Logged here rather than left unstated because the spec document itself (Code Map, migration filename) still names the collided name and was not edited to match. What would settle it: a spec-doc correction pass renaming `turn_decisions`->`turn_action_records` throughout spec-2-7's own text, so a future reader comparing the spec to the code does not see a mismatch and wonder if the code is wrong.
status: resolved -- the spec-doc correction pass ran during this story's own review pass (2026-09-18): every `turn_decisions`/`0146_turn_decisions.sql` reference in spec-2-7's Approach, Boundaries and Code Map/AC text was corrected to `turn_action_records`/`0146_turn_action_records.sql`, leaving only the Review Triage Log's own historical description of the mismatch (accurate as a past-tense record).

### DW-21: The per-turn journal write-budget degrade signal has no decay or reset path
origin: spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md, Review Triage Log (blind-hunter)
location: src/stackowl/journal/health.py; src/stackowl/journal/turn_budget.py
source_spec: `spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md`
severity: medium
reason: Once any `trace_id` crosses the provisional `PROVISIONAL_TURN_EVENT_BUDGET` (100), `JournalHealthState.budget_exceeded_count` is incremented and never cleared -- `note_success()` deliberately does not touch it (confirmed by reading `journal/health.py` directly), and nothing else does either. `JournalHealthContributor.health_check()` reports `degraded` for the rest of the process's life after that single crossing, until a full restart or `reset_for_tests()`. Real but bounded to an operability/observability signal, not data loss: AD-38 already guarantees no event is ever dropped regardless of budget state. Not patched during the review pass that found it: the right fix (decay-on-trace-completion, a TTL, or an explicit reset policy) is a genuine design decision that belongs with Story 2.12's benchmark work, which already owns finalizing this whole provisional budget mechanism (see DW-19 for the budget constant itself). What would settle it: Story 2.12 decides and implements the decay/reset semantics alongside setting the real budget value.
status: open

### DW-22: AD-38's write-budget rule names two dimensions and two scopes; this story implements only one of each
origin: spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md, Review Triage Log (intent-alignment)
location: src/stackowl/journal/turn_budget.py
source_spec: `spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md`
severity: medium
reason: AD-38's rule text reads "each turn and each command has a journal write budget (events per `trace_id` and p95 `journal.record` overhead)" -- two dimensions (event count, p95 latency) at two scopes (per-turn, per-command). `journal/turn_budget.py` implements only a raw per-`trace_id` event count; no p95/latency tracking and no per-command (as distinct from per-turn) scope exist anywhere in this story's diff, confirmed by re-reading AD-38 against the module directly. This narrowing was never explicitly logged as a scope cut until this review pass caught it. Not patched: the epic's own Technical Decisions text already states budget values AND mechanics "stay provisional until Story 2.12's benchmark" / "come from spike B2" -- the natural owner of the remaining dimensions, alongside the real budget number DW-19 already defers to that same story. What would settle it: Story 2.12 either implements the p95/per-command dimensions or explicitly, deliberately narrows AD-38's scope with stated rationale.
status: open

### DW-23: `JournalHealthContributor.health_check()`'s dual-signal message priority has no test covering both signals set at once
origin: spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md, Review Triage Log (verification-gap)
location: src/stackowl/journal/health.py:115-145
source_spec: `spec-2-7-every-turn-s-model-calls-tool-calls-and-delegation-hops-are-recorded.md`
severity: low
reason: `health_check()` correctly checks the `record()` consecutive-failure streak before the budget-overflow count, so the more serious message wins when both are non-zero (verified correct by direct reading). No test exercises both `_state.consecutive_failures` and `_state.budget_exceeded_count` being non-zero at once, so a future edit that reorders the two branches would silently flip which message an operator sees, with nothing catching it. `tests/journal/test_health_contributor.py` and `tests/journal/test_turn_budget.py` each only ever set one of the two fields. What would settle it: one small additional test setting both counters (via the test-only reset/note helpers) and asserting the failure-streak message/remedy wins.
status: open

### DW-24: Consent's audit write stays on its existing synchronous path, parallel to the new `consent_decision_records` table, instead of migrating onto `chain_append_via_pool` for a single shared audit_log record_ref
origin: spec-2-8-memory-writes-and-consent-decisions-are-recorded.md, Design Notes
location: src/stackowl/tools/consent.py::ConsentPolicy._finalize; src/stackowl/journal/consent_events.py; src/stackowl/db/migrations/0147_consent_decision_records.sql
source_spec: `spec-2-8-memory-writes-and-consent-decisions-are-recorded.md`
severity: medium
reason: `chain_append_via_pool` (audit/logger.py) already exists for exactly "join a caller's open db_pool.transaction() so an audit row and a journal event commit atomically" (Story 2.6's own precedent). Using it here instead of a new table would be the more DRY choice, but it requires converting `ConsentPolicy._finalize`'s CURRENT synchronous `audit_logger.append()` call -- exercised by 11+ existing tests -- onto the async path, on a live production consequential-action consent gate. This story follows Story 2.7's own "no existing async DB mutation to join" fallback instead (a fresh transaction into a small dedicated table, `turn_action_records`'s own precedent), deliberately trading one small duplicated table for a materially smaller, lower-risk diff on a production-critical path. Not a gap -- a reasoned, stated alternative. What would settle it: a dedicated follow-up story that converts `audit_logger.append()` itself onto `chain_append_via_pool`, re-verifies all 11+ existing consent tests against the async path, and then retires `consent_decision_records` in favor of a shared audit_log `record_ref`.
status: open

### DW-25: `BatchAuditor.grant`/`reject`/`action` (`tools/interaction/_batch_support.py`) are not journaled by this story
origin: spec-2-8-memory-writes-and-consent-decisions-are-recorded.md, Boundaries & Constraints ("Never")
location: src/stackowl/tools/interaction/_batch_support.py
source_spec: `spec-2-8-memory-writes-and-consent-decisions-are-recorded.md`
severity: low
reason: `BatchAuditor` is a structurally distinct, lower-severity (`write`, not `consequential`) flow, explicitly not the consequential-action consent gate `ConsentPolicy._finalize` decides. The spec's own Boundaries text names it as out of scope for this story rather than an oversight. Left unjournaled, logged here rather than silently dropped. What would settle it: a follow-up story that either registers a distinct `batch.*` journal event type for it or explicitly marks it `unjournaled` with a stated reason once Story 2.10's coverage tripwire exists to enforce that choice.
status: resolved -- Story 2.10's coverage tripwire now exists (`journal/coverage.py`). `BatchAuditor` writes `audit_log` (confirmed by direct reading of `tools/interaction/_batch_support.py`), and `audit_log` is explicitly marked `unjournaled` with a stated reason: `journal/coverage.py::UNJOURNALED_TABLES["audit_log"]` (`_REASON_AUDIT_LOG`, citing this exact DW-25 finding), diffed against the real migrated schema by `tests/journal/test_coverage_tripwire.py`. This is the "explicitly marks it `unjournaled` with a stated reason" branch of what would settle it -- the distinct `batch.*` event type branch remains open as future work if a later story decides `audit_log`'s BatchAuditor flow deserves its own journal type, but the coverage GAP this entry tracked is closed.

### DW-26: `SqliteMemoryBridge.stage()` and `SqliteLessonsStore.publish()` are not instrumented as "the SQLite memory write" this story journals
origin: spec-2-8-memory-writes-and-consent-decisions-are-recorded.md, Boundaries & Constraints ("Never")
location: src/stackowl/memory/sqlite_bridge.py (staged_facts); src/stackowl/learning/lessons_store.py::SqliteLessonsStore.publish
source_spec: `spec-2-8-memory-writes-and-consent-decisions-are-recorded.md`
severity: low
reason: The spec's own Boundaries text is explicit that `ReflectionStore.write()` -- durable, gating, already `conn`-joinable -- is the ONE SQLite memory write this story instruments as `memory.reflection_recorded`, and names these two as the reasons why NOT them: `SqliteMemoryBridge.stage()` writes `staged_facts`, a short-term buffer rather than curated knowledge; `SqliteLessonsStore.publish()` is an explicitly best-effort, non-gating derived search index. Neither matches the durable/gating/joinable shape the story's own Approach text requires. This is a deliberate exclusion argued in the spec, not an oversight -- logged here for the same future-traceability reason DW-24/DW-25 already got entries, since the diff itself never named it. What would settle it: a future story that decides whether either of these deserves its own distinct, lower-severity journal event type (e.g. `memory.staged`/`memory.lesson_published`) once there is a concrete reader that needs it.
status: open

### DW-27: Three awaits added by Story 2.9 can lose the caller's visibility into an already-computed delivery result under asyncio.CancelledError
origin: spec-2-9-deliveries-channels-and-providers-are-recorded.md, Review Triage Log (edge-case-hunter)
location: src/stackowl/notifications/deliverer.py (deliver()'s two return points, transport()'s tail)
source_spec: `spec-2-9-deliveries-channels-and-providers-are-recorded.md`
severity: low
reason: `journal/delivery_events.py::_record_delivery_event` only catches `Exception`, not `BaseException`, so an `asyncio.CancelledError` firing during the newly-added `await self._record_delivery(...)` call -- after `_transport()`/`_maybe_reroute()` already computed the real result -- propagates out of `deliver()`/`transport()` instead of returning that already-decided status, losing the caller's visibility that the message was already sent. Verified real by direct reading. Narrow: an identical exposure already existed before this story (`await self._remember_what_we_said(notification)` sits in the same tail position in `deliver()`), so this story widens an already-present window rather than introducing a new category of risk. The correct fix (`asyncio.shield`, or a broader cancellation-safety pass across `ProactiveDeliverer`) has real shutdown-semantics implications that deserve their own design consideration rather than a blind per-call-site patch. What would settle it: a deliberate cancellation-safety design pass across `ProactiveDeliverer`'s tail awaits (not just this story's three), deciding shield vs. a different mechanism with stated tradeoffs.
status: open

### DW-28: AD-24's `ephemeral_source` recording mechanism is never implemented anywhere in `journal/`, despite being named as one of two mechanisms
origin: spec-2-9-deliveries-channels-and-providers-are-recorded.md, Review Triage Log (intent-alignment)
location: src/stackowl/journal/ (architecture-level; no single file)
source_spec: `spec-2-9-deliveries-channels-and-providers-are-recorded.md`
severity: low
reason: AD-24 names two recording mechanisms -- piggyback on an existing state-change transaction, or register as `ephemeral_source` for an action with no SQLite state change of its own -- but `ephemeral_source` has zero hits anywhere under `src/stackowl/journal/` (confirmed by grep). Every story since 2.6 that hit this exact situation (no existing state mutation to piggyback on) has instead manufactured a small dedicated record_ref table (`turn_action_records` in 2.7, `consent_decision_records` in 2.8, `delivery_records`/`channel_ingress_records` in 2.9) rather than using the documented `ephemeral_source` path. Real architectural tension between the architecture doc's stated mechanism and established codebase practice, but pre-existing and inherited unchanged by Story 2.9 -- not a new deviation this story introduces. What would settle it: either implement `ephemeral_source` properly and migrate 2.7-2.9's manufactured tables onto it, or update ARCHITECTURE-SPINE.md's AD-24 text to describe the "manufacture a small record_ref table" pattern that is the codebase's actual, consistent practice.
status: open

### DW-29: The 9 record readers Story 2.10 registers (`RecordReaderRegistry`, AD-4) have zero live callers outside test code -- no gateway, API, or CLI surface dispatches through them yet
origin: spec-2-10-nothing-escapes-the-journal.md, Review Triage Log (blind-hunter, intent-alignment)
location: src/stackowl/journal/records.py (RecordReaderRegistry); the 9 read_*_record functions across journal/task_events.py, job_events.py, heal_events.py, health_events.py, turn_events.py, consent_events.py, delivery_events.py, channel_events.py, memory_events.py
source_spec: `spec-2-10-nothing-escapes-the-journal.md`
severity: low
reason: Confirmed by search: nothing under `src/stackowl/` outside `tests/journal/test_record_readers.py` calls `RecordReaderRegistry.get()`/`get_record_reader_registry()`. Not a gap Story 2.10 could close on its own: `src/stackowl/bridge/` does not exist yet (confirmed -- no such directory anywhere in the repo), and epic 2 is explicitly the journal-infrastructure epic, not the Bridge-surface epic that would need this registry -- there is no consumer package to wire a caller into today. AD-4/AC3's "runs in the gateway, md read-only" clause is correspondingly unimplementable until a gateway surface exists, and stays unaddressed by design, not oversight. What would settle it: whichever future story first builds a `bridge/` (or equivalent) record-viewing surface wires it to dispatch through `RecordReaderRegistry.get(record_kind, carrier)` rather than reading a table generically (the `tests/journal/test_bridge_never_reads_a_table_generically.py` tripwire Story 2.10 already ships will hold that surface to the rule the moment it exists).
status: open

### DW-30: `db_reclaim._RUN_HISTORY_RETENTION_DAYS`'s 7->30 raise (Story 2.11, DW-17) satisfies a pre-existing tripwire, not a direct AD-4 reference from any journal event
origin: spec-2-11-the-journal-stays-small-on-small-hardware.md, own review pass (self-caught during implementation)
location: src/stackowl/scheduler/handlers/db_reclaim.py (`_RUN_HISTORY_RETENTION_DAYS`); tests/journal/test_retention_tripwire.py
source_spec: `spec-2-11-the-journal-stays-small-on-small-hardware.md`
severity: low
reason: DW-17's own resolution (see its Update note) raised `_RUN_HISTORY_RETENTION_DAYS` from 7 to 30 so it would not be shorter than the journal's own 30-day retention, per AD-4's rule that "a tripwire fails any referenced record kind whose prune window is shorter [than journal retention]." Checked directly: no journal event actually references a `job_runs` row. `journal/coverage.py` explicitly excuses `job_runs` as unjournaled (`_REASON_PRE_EPOCH`, "no journal event type covers it yet"), and `job.*` events' `record_ref` points at the DIFFERENT `jobs` table (`journal/job_events.py`'s `_TABLE = "jobs"`), not `job_runs`. The actual reason for the raise is narrower than AD-4 itself: Story 2.6's pre-existing `tests/journal/test_retention_tripwire.py` already checked this constant against journal retention (applied conservatively, across every subsystem prune window it enumerates, without per-table proof that AD-4 reaches each one), and DW-17 assigned Story 2.11 to reconcile every window that specific tripwire checks -- not to satisfy AD-4 against a table AD-4 does not actually cover. This is a real, deliberate 23-day increase in `job_runs`'s disk footprint (previously owner-authorized at 7 specifically to keep it tight) made to satisfy a test rather than the architecture rule the test was written to proxy for. What would settle it: either narrow `test_retention_tripwire.py`'s scope to tables AD-4 actually reaches (letting `job_runs` retention revert to 7 if the owner wants that disk space back), or journal `job_runs` properly (a `job_run.*` event type referencing it) so the tripwire's check becomes literally true instead of conservatively true.
status: open

### DW-31: `batch_approve`'s "Approve all" executes every listed action directly, bypassing the per-action `ConsequentialActionGate` (and therefore `ConsentPolicy`'s always-ask re-check) for each one
origin: spec-3-4-consent-never-grants-a-channel-nobody-can-answer.md, investigation during implementation
location: src/stackowl/tools/interaction/batch_approve.py (module docstring: "On 'Approve all' it executes each listed action DIRECTLY — PRE-CONSENTED"; `_batch_support`'s direct-execution path)
source_spec: `spec-3-4-consent-never-grants-a-channel-nobody-can-answer.md`
severity: low
reason: This is a separate, PRE-EXISTING, deliberately-designed gap (the module's own docstring names it: "severity `write` and NOT `consequential`... consent simply moves from per-action to per-batch (the J8 outcome)"), explicitly out of Story 3.4's scope per its own Boundaries ("Never touch `batch_approve`'s 'Approve all' bypass of the per-action gate -- a separate, pre-existing, deliberately-designed gap (J8), out of this story's scope; log it to `deferred-work.md`"). Story 3.4 changes ONLY the channel-routing/principal layer beneath `ConsentPolicy.request()` -- it does not touch how `batch_approve` decides to skip that call entirely for its own listed actions. Named here so a future reader does not conflate "the router now fails closed on an unwired channel" with "every consequential action is individually re-gated" -- `batch_approve`'s batch presentation is a DELIBERATE, ALREADY-DECIDED exception to that, not a residual hole this story left open. What would settle it: an explicit design decision on whether an always-ask action (lock/alarm/destructive/prompt_surface/authority_widening/owl_build) listed inside a batch should be re-checked individually even after the batch's own single consent, or whether the batch presentation is trusted to have shown it plainly enough that per-action re-checking is intentionally skipped.
status: open

### DW-32: `skills/authoring.py`'s `_consent_or_refuse`/`SkillWriteRequest` never reads `reply_target` — confirmed scheduled-only today, but would need Story 3.5's fix if a future change ever gives it a live call path
origin: spec-3-5-the-consent-address-survives-the-gateway-core-link.md, own Boundaries (Never section)
location: src/stackowl/skills/authoring.py (`_consent_or_refuse`, `SkillWriteRequest`)
source_spec: `spec-3-5-the-consent-address-survives-the-gateway-core-link.md`
severity: low
reason: This module's own docstring states it is reached only where "a scheduled skill-authoring pass has no ambient Tool/pipeline dispatch" -- i.e. never a live turn. A scheduled caller's `TraceContext` carries `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` (Story 3.4), so `ConsentPolicy.request()` routes straight to `AutonomousPrompter()`, bypassing `self.prompter` (and therefore the socket/frame path this story hardens) entirely -- `reply_target` is never consulted on that branch, so hardening `GatewayLink._handle_consent` to refuse a missing `reply_target` does not regress this module. Explicitly out of Story 3.5's scope per its own Boundaries ("Never touch `skills/authoring.py`'s `_consent_or_refuse` / `SkillWriteRequest`... Log it to `deferred-work.md` as a residual gap if a future live call path is ever added to that module"). What would settle it: if a future change ever lets a live turn reach this module directly (bypassing `ConsequentialActionGate.check()`), thread `reply_target=ctx.get("reply_target")` into its `gate.policy.request(...)` call the same way this story fixed `shell.py`/`tool_build.py`/`owl_build.py`.
status: open

### DW-33: Split-mode clarify (`question`) delivery renders as plain numbered-list text, not real Telegram tap-buttons
origin: spec-3-6-answer-from-telegram-and-watch-it-close.md, own Boundaries (Never section)
location: src/stackowl/runtime/gateway_link.py::_deliver_clarify; src/stackowl/channels/telegram/clarify.py (button resolver, exercised only in mono mode today)
source_spec: `spec-3-6-answer-from-telegram-and-watch-it-close.md`
severity: low
reason: Confirmed pre-existing and orthogonal to this story: `GatewayLink._deliver_clarify` already sent plain numbered-list text (not buttons) in split mode before this story, and `channels/telegram/clarify.py`'s `TelegramClarifyResolver` (the inline-button tap handler) is wired and exercised only for the mono-mode `TelegramChannelAdapter.send_clarify` path. This story explicitly scoped `question` delivery as unchanged ("question via its existing (pre-3.6) delivery path" -- AC2) and only threaded `needs_you_item_id` through the existing text-delivery seam so a later cross-surface resolve could find and edit the message; it deliberately did not build real button delivery for split mode. What would settle it: a follow-up story that gives the split-mode text delivery real inline buttons (mirroring the mono-mode keyboard), wiring the tap back over `ClarifyReplyFrame` the same way `ConsentResponseFrame` already round-trips a button tap today.
status: open

### DW-34: No literal `scripts/dev_ingress.py` subprocess proof for the Telegram approval/incident/alert end-to-end path
origin: spec-3-6-answer-from-telegram-and-watch-it-close.md, own Boundaries (Never section)
location: tests/smoke/test_e0_s1_consent_telegram_smoke.py; tests/runtime/test_split_consent.py
source_spec: `spec-3-6-answer-from-telegram-and-watch-it-close.md`
severity: low
reason: Same structural gap already reviewed and accepted for Story 3.5 (its own triage log, IA-1) and Story 3.3 before it: `scripts/dev_ingress.py` cannot simulate a real `callback_query`/button tap and has no split-mode socket awareness, so it cannot stand in as a literal live-process proof for this story's approval-tap, expiry-edit, or incident/alert-push paths either. This story's own verification instead extends the established in-process real-code/fake-bot-transport pattern (`tests/smoke/test_e0_s1_consent_telegram_smoke.py`) plus the real-socket pattern (`tests/runtime/test_split_consent.py`) for split-mode/group-chat proof -- the same substitution the codebase has used consistently since spec-3-3. What would settle it: a `dev_ingress.py` extension (or a parallel harness) that can drive a real Telegram Bot API sandbox/mock server end-to-end, including a simulated button tap and split-mode socket wiring -- a materially larger investment than any single story in this epic has taken on.
status: open
