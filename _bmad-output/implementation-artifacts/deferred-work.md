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
