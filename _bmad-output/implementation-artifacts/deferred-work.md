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
