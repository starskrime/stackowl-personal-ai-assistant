---
title: 'Dashboard ownership proven by a one-time setup code, not a published password'
type: 'bugfix'
created: '2026-09-12'
status: 'done'
route: 'dispatch'
review_loop_iteration: 1
baseline_commit: '7f53ef09ab27f1b870cfdee247c9191de75de435'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The dashboard binds every interface by design (ESC-172) and ships `admin/admin`; a default-credential login receives the real, permanent bearer token and every data route serves it — the page merely shows a banner. Anyone on the network owns the dashboard of every fresh clone. Review of the first attempt (iteration 0) showed that any design treating a publicly known password as proof of ownership still lets the first network visitor claim a fresh install, leaks a token, and reopens whenever the secret store cannot be read.

**Approach:** Ownership is proven by a one-time SETUP CODE, never by a password anyone can look up. An install with no stored password is in setup mode: no token is issued, every data route is refused, and the page shows a setup form taking the setup code plus a new password entered twice. The code is shown on the platform's terminal and sent to the owner's existing Telegram. The password lives only as a salted hash in the secret store; `control_plane.password` is retired from settings, with an existing custom YAML password imported once on upgrade. `stackowl control-plane reset-password` on the host clears the password and issues a fresh code without a restart.

**Decisions (human, 2026-09-12, L1–L4):** L1 setup code proves ownership, no admin/admin, no token for a publicly known credential. L2 password removed from stackowl.yaml, stored once as a hash, custom YAML password imported once. L3 an unreadable store is never "no password": lookup retried on each sign-in, sign-in refused with a remedy, reported to self-healing as an incident; a damaged record returns the install to setup mode. L4 host CLI `stackowl control-plane reset-password` clears the password and issues a fresh code, effective without restart.

## Boundaries & Constraints

**Always:**
- Three states, re-read from the store on every request (no permanent cache): SETUP (no stored hash), READY (hash readable), UNAVAILABLE (backend error or unreadable file). Absent and unreadable are distinguished by the lookup itself, not by `SecretResolver` (which conflates them).
- SETUP: login issues no token; every `_guard` data route refuses; only `POST /api/v1/setup` with the current setup code and an acceptable password leaves it.
- Setup code: ≥ 50 bits of entropy, human-typable, single use, expires 24 h after issue, kept in the secret store, compared in constant time. An unexpired code is reused across restarts and not re-sent.
- On issue, the code goes to the owner's Telegram through `ProactiveDeliverer` when exactly one owner address resolves, and to the operator's terminal (the `cli` channel when the platform issues it; printed when the CLI issues it). Delivery failure is logged and never blocks setup.
- UNAVAILABLE: login and data routes answer 503 with a remedy; a health contributor reports the store down with that remedy so self-healing retries and pages. A damaged record is reported, then treated as SETUP.
- Password: salted scrypt hash only; ≥ 12 characters; not the username; entered twice on the page. Setting, changing or resetting it rotates the bearer token.
- Attempts count in `LoginAttempts` BEFORE any hash derivation. Setup, change and reset are serialised. A failure part-way leaves password, code and token as they were.
- `control_plane.password` and `credentials_are_default` are deleted (settings, example YAML, descriptions, tests). On config load a legacy `control_plane.password` key is removed from stackowl.yaml in place, comment-preserving; a custom value (not `admin`, not a `keychain:`/`file:` reference) is imported once as the hash when none is stored, with a WARNING if shorter than 12 characters; `admin`/reference values are dropped. Idempotent; if the file write fails the key is still ignored and a WARNING gives the remedy.
- Server-side enforcement in `_guard`; per-handler auth, no middleware; uniform 401; token, code, password and hash never logged; stdlib crypto; 4-point logging.

**Never:** accept `admin/admin` or any published value as proof of ownership; issue a token in SETUP or UNAVAILABLE; keep the YAML password; change the `0.0.0.0` bind; add middleware or an unauthenticated status route; redesign the page beyond setup/change/confirm forms and stale-token handling; change the `ControlPlaneServer(` call text; change bare `stackowl control-plane` output.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh install | SETUP | login → 409 `setup_required`, no token; data → 403 `setup_required`; a code is issued and delivered | delivery failure logged; CLI can issue a code |
| Setup succeeds | valid code, acceptable password | 200 with rotated token; hash stored; code consumed; data → 200 | N/A |
| Bad code | wrong, expired or used code | 401 uniform, attempt counted, nothing stored | 429 after `MAX_FAILURES` |
| Weak password at setup | valid code, < 12 chars or = username | 400 `weak_password`; code NOT consumed | N/A |
| Restart in setup | unexpired code stored | same code still valid; not re-sent | N/A |
| Normal login | READY | right → 200 token; wrong → 401 uniform, counted | N/A |
| Change password | READY, right current, acceptable new | 200 rotated token; old token → 401 | write fails → 500, nothing changed |
| Store unreadable | keyring raises or file unreadable | login and data → 503 `password_store_unavailable` + remedy; contributor down; next request re-reads | incident through health sweep |
| Damaged record | unparsable hash | reported down, then SETUP | ERROR logged |
| Legacy custom YAML password | key present, no hash | imported, key removed from file, boot succeeds, it signs in | file write fails → WARNING, key ignored |
| Legacy `admin` or reference | key present | key removed, nothing imported → SETUP | N/A |
| Reset CLI | any state | hash deleted, token rotated, new code printed and sent; dashboard in SETUP on next request, no restart | store failure → exit 1 with remedy |
| Concurrent setup/change | two requests at once | exactly one succeeds; token and hash consistent | N/A |
| Stale tab | token rotated elsewhere | page clears the token and shows sign-in | N/A |

</frozen-after-approval>

## Code Map

- `src/stackowl/control_plane/server.py` -- routes :168-188; `__init__` :102 (inject deliverer); `_guard` :374-416; login :858-1015; `_warn_if_default_credentials` :216-255 (replace with setup-mode code issue); iteration-0 `_handle_password` (reference).
- `src/stackowl/control_plane/password.py` -- iteration-0 reference: scrypt record format, service `stackowl-control-plane-password-hash`. Its `_loaded` cache and `SecretResolver`-based lookup must go.
- `src/stackowl/control_plane/auth.py:86-153` -- `ensure_credential` (leave); iteration-0 `rotate_credential` read-back calls the minting lookup — replace with a non-minting read.
- `src/stackowl/config/secret_writer.py:24` -- `store_secret`; add `delete_secret` (inline pattern at `cli/providers_cli.py:170-190`).
- `src/stackowl/config/control_plane_settings.py:17,50-87` -- `extra="forbid"`; `username` stays, `password` + `credentials_are_default` deleted.
- `src/stackowl/config/settings.py:77` + `src/stackowl/config/provider_tier_migration.py:32` -- in-place ruamel legacy-key migration run before parsing; copy the pattern.
- `src/stackowl/notifications/router.py:66` `Notification`, `deliverer.py:201` `ProactiveDeliverer.deliver`, `recipient.py:28` `resolve_owner_addresses`; template `scheduler/assembly.py:1244-1257`; `cli` channel proxy `orchestrator.py:2301`.
- `src/stackowl/health/status.py:62-92` `HealthContributor`/`HealthStatus(remedy)`; registration pattern `scheduler/assembly.py:649`; sweep `health_sweep.py:501`.
- `src/stackowl/orchestrator.py:1308,4563-4566` -- deliverer exists; server construction (add arguments, keep call text).
- `src/stackowl/cli/app.py:1404-1470` -- `control-plane` command; Typer group with `invoke_without_command=True` like `serve_app` (:19,31,259).
- `src/stackowl/control_plane/page.py` -- sign-in form :347-354, change block :356-373, `get()` :833-850, `needPassword` :862, handlers :903-980.
- Tests: `tests/control_plane/test_the_dashboard_has_a_login.py` (structural guards :149-250, :304, :449; iteration-0 class), `test_one_dead_route_does_not_blank_the_dashboard.py` (node harness), `test_the_control_plane_is_locked_before_it_is_built.py` (`_SECRET_NAMES`, route-auth exemption), `tests/test_the_example_config_cannot_go_stale.py:154` (credential-key floor 13 → 12), seven data-route files' `_Cfg` stubs.

## Tasks & Acceptance

**Execution:**
- [x] `tests/control_plane/test_the_dashboard_has_a_login.py` (+ harness, config, CLI tests) -- failing tests first for every matrix row, plus: page setup/change submit posts the right keys and stores the returned token; non-ASCII credentials; no code/hash names in logs; the route-auth exemption requires the password or setup-code check, not a username compare.
- [x] `src/stackowl/config/secret_writer.py` -- `delete_secret` -- reset needs it.
- [x] `src/stackowl/control_plane/password.py` -- `ControlPlanePassword` (state, verify, set, clear) and setup-code issue/reuse/verify-and-consume, absent vs unreadable distinguished -- one owner of both facts.
- [x] `src/stackowl/control_plane/auth.py` -- `rotate_credential` with non-minting read-back; on mismatch remove what was written.
- [x] `src/stackowl/config/control_plane_settings.py`, new `src/stackowl/config/control_plane_password_migration.py`, `src/stackowl/config/settings.py` -- delete the field; legacy key import/removal before parsing.
- [x] `src/stackowl/control_plane/server.py` -- states in login and `_guard`; `POST /api/v1/setup`; change route with serialisation and count-before-hash; one shared credential-proof helper for login and change; code issue + delivery at start.
- [x] `src/stackowl/orchestrator.py` -- pass the deliverer; register the password-store health contributor.
- [x] `src/stackowl/cli/app.py` -- `control-plane` group; `reset-password` subcommand.
- [x] `src/stackowl/control_plane/page.py` -- setup form (code + password twice), confirm field on change, 409/403/503 handling, 401 clears the token and shows sign-in.
- [x] `docs/stackowl.yaml.example` + tests stubs -- regenerate example; drop `password`/`credentials_are_default` from stubs; lower the credential-key floor.

**Acceptance Criteria:**
- Given the live platform with no stored password after restart, when `admin/admin` is posted to `/api/v1/login`, then no token is returned and a setup code has reached the terminal and (if one owner address resolves) Telegram.
- Given that code, when setup is posted over HTTP to port 8787, then the rotated token reads `/api/v1/health`, and after `stackowl control-plane reset-password` the next request is in setup mode without a restart.
- Given a stackowl.yaml still containing `control_plane.password`, when the platform boots, then it starts and the key is gone from the file.

## Implementation Notes

- Iteration 0 implemented all five tasks; 190 tests green, ruff/mypy clean, live 8787 curl flow passed (default login 200 + `must_change_password`, data 403, change → rotated token, old token 401, restart keeps new password; test hash deleted afterwards so the live box is gated again).
- Review found intent gaps (see triage log, groups IG-1..IG-4) → loopback to the human. The iteration-0 diff is preserved at `/tmp/claude-1000/-ssd-projects-stackowl-personal-ai-assistant/a85044f9-7591-426c-8323-23c3ff14267c/scratchpad/q29-diff-1789238579.patch`. Revert is performed immediately before re-derivation, not while waiting for the human, so the running platform keeps the gate meanwhile (stated to the owner).
- KEEP for re-derivation: `ControlPlanePassword` as the single owner (scrypt n=2^14 r=8 p=1, 16-byte salt, `scrypt$…` record, service `stackowl-control-plane-password-hash` distinct from the example's YAML reference name); gate placed in `_guard` after the token check so unauthenticated callers keep the uniform 401; rotate-then-store with token restore on hash-write failure; read-back after every secret write; scrypt off the event loop via `asyncio.to_thread`; UTF-8 byte comparison for non-ASCII credentials; page form in a `<div hidden>` wrapper (a hidden `<form>` is outranked by `form{display:flex}`); tests derived from handlers (`_guarded_handlers`), `_no_os_keyring` stub; 4-point logging with remedies.
- Iteration 1 (2026-09-12): human answered L1–L4; intent and boundaries renegotiated; BS/P findings from the iteration-0 review folded into Boundaries and Tasks (serialisation, count-before-hash, non-minting read-back, confirm field, stale-tab handling, shared proof helper, untested branches). Deferred: login 500 on a JSON array body (pre-existing, BH7); `control_plane.password` never resolved secret references (VG6) — moot once the field is deleted.

## Spec Change Log

## Review Triage Log

Layers: BH = blind hunter, VG = verification gap, EC = edge-case hunter. Routes: IG = intent_gap, BS = bad_spec, P = patch, D = defer, R = rejected.

| # | Finding | Verdict | Evidence | Group / route |
|---|---|---|---|---|
| BH1 | Concurrent password changes interleave verify/rotate/set/assign with no lock; a failed restore can reinstate the pre-change token | medium | `_handle_password` awaits `to_thread` between every step; no lock on the server | BS (lock + ordering) |
| BH2 | No reset/recovery path; `_load` caches forever so deleting the record needs a restart | medium | only way out is deleting `…-password-hash` from keyring/`.secrets` and restarting | IG-3 |
| BH3 | Stored hash silently shadows a later YAML `password` edit although the field is `hot_reload: True` | medium | `verify` ignores `cfg.password` once `_load()` is non-None; no log line | BS (boot/reload warning) |
| BH4 | Failure paths mutate the store while reporting "nothing changed": `rotate_credential` read-back mismatch keeps the new token; its read-back via `ensure_credential()` can mint a third token; `set()` mismatch leaves the new hash lingering | medium | `auth.py` read-back calls the minting lookup; neither mismatch branch removes the written record | BS |
| BH5 | scrypt runs before `record_failure`; parallel guesses from one source all pass `is_refused`; no concurrency or length cap | medium | `is_refused` → `await to_thread(verify)` → `record_failure`; 16 MiB per derivation | BS |
| BH6 | Route-auth exemption (`_origin_ok(` + `compare_digest(`) is now satisfied by the username compare alone | medium | password half moved to `self._password.verify`; structural test not updated | P |
| BH7 | Credential proof duplicated between login and password routes and already diverged; login 500s on a JSON array body | medium | password route checks `isinstance(body, dict)`, login does `(body or {}).get` | BS (shared proof method); array-500 pre-existing → D |
| BH8 | Tests outside the new class hit the developer's real OS keyring via `verify → _load` | medium | `_no_os_keyring` is class-scoped; conftest isolates only `STACKOWL_HOME` | P |
| BH9 | Gate looser than the new-password rule: `username≠admin` + `password: admin` not gated; short YAML passwords ungated; bare env-var reference form not covered | medium | `credentials_are_default` requires both halves; `weakness()` rejects `admin` under any username. Env-var part: false — a bare variable name is not a published value | IG-4 |
| BH10 | Stale texts: `credentials_are_default` docstring mentions a banner; out-of-box test docstring; `_warn_if_default_credentials` name/docstring; CLI prints a `curl …/health` that now returns 403 | low | read in diff context | P |
| BH11 | No confirm-new-password field; raw error codes shown | medium | one masked `pwnew` input; with no reset path a typo locks the owner out | BS |
| BH12 | Other tabs/devices dead-end at "HTTP 401" after rotation, keeping the dead token | medium | `get()` throws on 401 without clearing sessionStorage or showing sign-in | BS |
| BH13 | Untested branches: form submit, accented username, unreadable record, rotate mismatch, concurrency | medium | grep of tests (confirmed by VG1–VG3) | P |
| BH14 | Corrupt hash record silently reopens the first-claim race | high | `_load` treats `ValueError` as absent → admin/admin gated again → anyone can set a password | IG-1/IG-2 |
| BH15 | `_SECRET_NAMES` misses `restore`, `raw`, `record`, `read_back`, `presented_user` | low | names hold plaintext token/hash in new code | P |
| BH16 | `_guarded_handlers` floor `>=3` vs 9 routes; `_Cfg` block pasted into 8 files | low | real but unlikely to bite; fix is a refactor | R |
| VG1 | Page change-password submit never executed by any test | medium | pre-verified: harness fires only `forget` | P |
| VG2 | Unreadable stored record path untested | medium | pre-verified | P |
| VG3 | Non-ASCII byte comparison untested | medium | pre-verified | P |
| VG4 | Login returns the real token while gated; lifting the gate via YAML never rotates it, so a token collected during the window reads data later | high | `_handle_login` returns `self._token` with `must_change_password: true`; rotation only in `_handle_password` | IG-1 |
| VG5 | Gate and `weakness()` disagree about `admin` | medium | duplicate of BH9 | IG-4 |
| VG6 | `control_plane.password` never passes through `SecretResolver`, so the example's `keychain:` value can never work as documented | medium | no resolution of `cfg.password` in `src/` | D (pre-existing) |
| EC1 | `set()` caches the hash in a worker thread before `self._token = minted`; pre-change token passes the gate in that window, permanently if the coroutine is cancelled | medium | ordering in `_handle_password` | BS |
| EC2 | Two concurrent changes both verify `admin` | medium | duplicate of BH1 | BS |
| EC3 | Concurrent guesses bypass the brake; scrypt starves the executor | medium | duplicate of BH5 | BS |
| EC4 | First network visitor with admin/admin claims a fresh clone | high | change route accepts the published password as proof of ownership | IG-1 |
| EC5 | Transient keyring failure on first `_load` is cached as "no password"; admin/admin signs in and can overwrite | high | `except Exception: continue` then `_loaded = True` | IG-2 |
| EC6 | Malformed record makes YAML admin/admin valid again | high | duplicate of BH14 | IG-2 |
| EC7 | `password: admin` with a changed username is not gated | medium | duplicate of BH9 | IG-4 |
| EC8 | Forgotten password → lockout; deleting the record has no effect until restart | medium | duplicate of BH2 | IG-3 |
| EC9 | YAML hot-reload silently shadowed | medium | duplicate of BH3 | BS |
| EC10 | Non-string JSON fields are `str()`-ed and could be stored as a Python repr | low | `str(body.get(...) or "")`; only a crafted client, page sends strings; fix is a direct type check | P |
| EC11 | `set()` raising something other than `PasswordStoreUnavailable` leaves the rotated token stored | low | only `PasswordStoreUnavailable` is caught around `set` | BS (with BH4) |
| EC12 | Hash written to file while a stale keyring record wins read-back → lingering hash | medium | duplicate of BH4 | BS |
| EC13 | Read-back via `ensure_credential` may mint a third token | medium | duplicate of BH4 | BS |
| EC14 | Password route non-JSON error body shows a JSON SyntaxError | low | `r.json()` without catch in the submit handler | P |
| EC15 | Other tab 401 dead end | medium | duplicate of BH12 | BS |
| EC16 | Claim "publicly known password refused" false for `admin` under another username | medium | duplicate of BH9 | IG-4 |
| EC17 | Claim "until the OWNER sets a new password" false — any visitor can | high | duplicate of EC4 | IG-1 |
| EC18 | Claim "survives restarts" false with a flaky keyring | high | duplicate of EC5 | IG-2 |
| EC19 | Claim "rotation kills pre-change tokens" has a race window | medium | duplicate of EC1 | BS |

Cascade: IG-1..IG-4 trigger a loopback to the human; BS/P/D entries are moot until re-derivation and will be folded into the amended spec (BS/P) or the deferred-work ledger (D: BH7 array-500, VG6).


Iteration 1 review (2026-09-12). Layers: BH2 = blind hunter, VG2 = verification gap, EC2 = edge-case hunter. Routes: P = patch (sent back to the implementer), D = defer, R = rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| BH2-1 | Headless `cli` send is dropped by `HeadlessCliAdapter` but counted as delivered; with no single Telegram owner the code is marked sent and reaches nobody | high | `cli_adapter.py:353-361` returns None; deliverer `_transport` returns "delivered" on no-raise; `server.py:425` starts `telegram_reached` True when owner is None | P |
| BH2-2 | Page, setup note and CLI claim the code went to terminal and Telegram without knowing | medium | 409/403 body is only `setup_required`; copy is static | P (copy states the real rule and points to `reset-password`) |
| BH2-3 | Only Telegram with exactly one allowed id can receive the code remotely | medium | `resolve_owner_addresses` TODO for other channels (pre-existing) | D |
| BH2-4 | Deliverer fallback reroute can send the code to another channel and it is counted as Telegram | medium | `_maybe_reroute` returns delivered; `_deliver_setup_code` sets `telegram_reached` | P |
| BH2-5 | Running server never re-reads the bearer token after a host `reset-password` rotation | medium | `_guard` compares in-memory `self._token` (`server.py:604`) | P |
| BH2-6 | Bare `stackowl control-plane` curl example returns 403 in setup mode; a password change silently rotates the API token | low | output text unchanged; rotation undocumented to machine clients | P |
| BH2-7 | Migration rewrites stackowl.yaml in place with truncation | medium | `path.open("w")` + dump in the new migration | P (new file); sibling `provider_tier_migration.py:81` pre-existing → D |
| BH2-8 | Migration drops `keychain:`/`file:` reference passwords instead of resolving them | false | `control_plane.password` never passed through `SecretResolver` (VG6), so a reference never worked as a password — it compared as the published literal; frozen Boundaries drop it by human decision | R |
| BH2-9 | Migration value handling lossy for int/float YAML values | low | `str()` mirrors the previous pydantic str coercion; unlikely; fix adds branches | R |
| BH2-10 | Asyncio locks are in-process; host CLI `issue_code` can interleave with server `mark_code_sent` | low | millisecond window; consequence is a re-run of reset; fix needs cross-process locking | R |
| BH2-11 | `delete_secret` leaves an old keyring record when the file holds the record itself | medium | keyring delete only when the file is a locator | P |
| BH2-12 | `_write_private`: no fsync; Windows `os.replace` PermissionError under concurrent readers | medium | cross-platform rule; per-request reads hold the file | P |
| BH2-13 | Every guarded request reads the store and can log two WARNINGs per panel; setup mode warns per panel; migration warns per `Settings()` | medium | `_guard` → `lookup` + `_store_unavailable` | P (dedupe per state change) |
| BH2-14 | `_spawn_setup_code` duplicate guard checks `lock.locked()` before tasks take the lock | medium | burst spawns queued tasks that resend while Telegram fails | P |
| BH2-15 | Setup mode with an undelivered code reports healthy | medium | `PasswordStoreHealth` ok in setup | P (degraded when setup and code unsent) |
| BH2-16 | Unauthenticated change-password guesses run scrypt inside `_credential_lock`, delaying the owner's setup | low | `_prove_owner` inside the lock | P (prove, then lock and re-check) |
| BH2-17 | Secret-logging guard checks names, not attribute access (`issued.display`) | low | `_SECRET_NAMES` name-only | P |
| BH2-18 | Missing tests: orchestrator wiring, boot retry schedule, `stop()` cancel, headless adapter delivery, fallback reroute | medium | grep of tests (confirmed by VG2) | P |
| BH2-19 | Test settle fixture finds tasks by `__qualname__`; rename makes it silent | medium | conftest `_settle_setup_code_tasks` | P |
| BH2-20 | Wrong comments/docstrings: override claim, "three forms, one at a time", `gen_config_example.py:175` 13th field | low | read in diff | P |
| BH2-21 | `reset-password` builds `Settings()` first, so a broken stackowl.yaml blocks the recovery command | medium | `cli/app.py:1500` | P |
| VG2-1 | Headless adapter no longer logs the preview — untested | medium | pre-verified | P |
| VG2-2 | Orchestrator `deliverer=` and `PasswordStoreHealth` registration untested | medium | pre-verified; probe only greps `ControlPlaneServer(` | P |
| VG2-3 | `_BOOT_DELIVERY_RETRY_S` never exercised | medium | pre-verified | P |
| VG2-4 | `reset()` rollback when `clear()` fails untested | medium | pre-verified | P |
| VG2-5 | Headless code marked sent (other finding) | high | duplicate of BH2-1 | P |
| VG2-6 | Burst duplicate sends (other finding) | medium | duplicate of BH2-14 | P |
| EC2-1 | Burst before first task takes `_code_lock` | medium | duplicate of BH2-14 | P |
| EC2-2 | Exception outside the per-target try kills the setup-code task silently | medium | `_ensure_setup_code` body unguarded | P |
| EC2-3 | No deliverer / no owner → two WARNINGs on every setup-mode request | medium | duplicate of BH2-13 | P |
| EC2-4 | Stored scrypt record with huge `p` or digest length not bounded | low | only `128*r*n` bounded; local tamper needed; bound is a direct check | P |
| EC2-5 | Non-UTF-8 secret file raises `UnicodeDecodeError` → 500s and `Settings()` failure | medium | reads catch only OSError | P |
| EC2-6 | Keyring write succeeds, locator write fails → new hash live behind old locator, 500 says unchanged | medium | `secret_writer.py:301-307` | P |
| EC2-7 | `consume_code` deletes keyring entry then unlink fails → code gone, 500 says unchanged | low | `_undo` does not put the code back | P |
| EC2-8 | Store error in `code_matches` counted as a failed guess → owner braked | low | `record_failure` runs first | P |
| EC2-9 | `issued_at` in the future keeps a code valid past 24 h | low | expiry computed from `issued_at` only | P |
| EC2-10 | CLI `issue_code` vs server read-modify-write race | low | duplicate of BH2-10 | R |
| EC2-11 | Migration yaml rewrite not atomic for concurrent readers | medium | duplicate of BH2-7 | P |
| EC2-12 | Gateway and core importing the legacy password at the same moment | maybe-false (medium if true) | settle: confirm whether both processes construct `Settings()` concurrently at boot or on config reload | D |
| EC2-13 | `reset-password` crashes on malformed yaml | medium | duplicate of BH2-21 | P |
| EC2-14 | `reset()` incomplete rollback still prints "Nothing was changed" | low | restore helpers only log | P |
| EC2-15 | Windows `os.replace` PermissionError | medium | duplicate of BH2-12 | P |
| EC2-16 | Page renders a non-JSON 503 body as panel data | low | `page.py:689-696` | P |
| EC2-17 | `STACKOWL_CONTROL_PLANE__PASSWORD` env var hits `extra="forbid"` and refuses boot | medium | legacy key stripped only in `_YamlSource` | P |
| EC2-18 | `rotate_credential` with no previous token leaves the minted token on read-back mismatch | low | undo only when previous exists | P |
| EC2-19 | L2 import drops references | false | duplicate of BH2-8 | R |
| EC2-20 | Non-UTF-8 secret file breaks AC3 boot | medium | duplicate of EC2-5 | P |

Cascade: no intent_gap or bad_spec entries; patches sent to the implementer; defers written to deferred-work.md.

## Design Notes

A code, not a password, is the proof because only someone with access to the host's terminal or the owner's Telegram can hold it; that is the same ownership proof the future bridge uses for its first passkey (Q41), so it is built once. 409 on login (rather than a status route) keeps every handler authenticated, satisfying the existing route-auth guard.

## Verification

**Commands:**
- `uv run pytest tests/control_plane tests/test_the_example_config_cannot_go_stale.py tests/config -q` -- expected: all pass
- `uv run ruff check src/stackowl/control_plane src/stackowl/config src/stackowl/cli tests/control_plane && uv run mypy src/stackowl/control_plane src/stackowl/config src/stackowl/cli` -- expected: clean

**Manual checks (if no CLI):**
- After restart with no stored password: login → 409, code visible in the terminal/Telegram, setup via curl → token reads health; old stackowl.yaml with `control_plane.password` boots and loses the key; `stackowl control-plane reset-password` → next request is setup mode without restart.
