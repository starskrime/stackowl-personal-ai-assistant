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
status: open

### DW-17: `journal/retention.py`'s `PROVISIONAL_JOURNAL_RETENTION_DAYS` is a provisional 1-day constant, not the real journal retention setting AD-6 describes
origin: spec-2-6-jobs-heals-and-health-have-a-history.md, Design Notes
location: src/stackowl/journal/retention.py; tests/journal/test_retention_tripwire.py (the only reader)
source_spec: `spec-2-6-jobs-heals-and-health-have-a-history.md`
severity: low
reason: The epic's own Technical Decisions state retention values "stay provisional until Story 2.12's benchmark." Today's REAL production prune windows are `prune_completed_after_days=1` (`config/task_loop_settings.py`, tasks) and `_RUN_HISTORY_RETENTION_DAYS=7` (`scheduler/handlers/db_reclaim.py`, job_runs) -- both owner-authorized and far below AD-6's eventual ~30-day default, so a tripwire compared against 30 would fail immediately against values this story does not own. The constant is set to the tightest existing real window (1 day) so the tripwire is honest without forcing a change to owner-authorized production behaviour. What would settle it: Story 2.11 raises `PROVISIONAL_JOURNAL_RETENTION_DAYS` to the real setting (architecturally ~30 days) and, in the SAME change, reconciles every subsystem prune window this tripwire checks (task/job-run retention) so none is shorter than the new value.
status: open

### DW-18: `job.started` can be left unresolved (no follow-up event) when a job's handler isn't registered at claim time
origin: spec-2-6-jobs-heals-and-health-have-a-history.md, Review Triage Log (adversarial-review)
location: src/stackowl/scheduler/scheduler.py (`_run_job`); src/stackowl/scheduler/scheduler_mutations.py (`run_now`)
source_spec: `spec-2-6-jobs-heals-and-health-have-a-history.md`
severity: low
reason: After the pending->running CAS claim wins and `job.started` commits, both `_run_job` and `run_now` look up the claimed job's handler in the registry; when it is not found (a narrow boot-ordering race), the row is reverted to `pending` via a plain write with no `conn=` and no journal event. A journal reader sees an orphaned `job.started` with no counterpart until the job is next claimed. Confirmed by reading both call sites directly -- not a double-write, not data corruption, and the race window (handler registration ordering at boot) is narrow. What would settle it: wrap the revert-to-pending write in `self._db.transaction()` and record a `job.failed` (or a new, more precise event type) alongside it, mirroring every other state-change-plus-journal-event site this story already wired.
status: open
