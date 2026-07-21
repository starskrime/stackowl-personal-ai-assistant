# Multi-Tier Provider Membership — Progress Tracker

Companion to `2026-07-20-multi-tier-provider-membership.md`. Status: `not started` / `in progress` / `blocked (reason)` / `done (commit sha)`.

**Spec:** `docs/superpowers/specs/2026-07-20-multi-tier-provider-membership-design.md`
**Plan:** `docs/superpowers/plans/2026-07-20-multi-tier-provider-membership.md`

## Phase 1 — Schema & registry core
- [x] Task 1: ProviderConfig.tiers + legacy tier= constructor alias — done (commit 730ee5e2, fix cbe7cbb1)
- [x] Task 2: On-disk migration module — done (commit a7b01b6d)
- [x] Task 3: Wire migration into Settings' YAML source + fix summary log line — done (commit fd0e548a)
- [x] Task 4: ProviderRegistry — multi-tier membership everywhere — done (commit 3d5efb5e)
- [x] Task 5: TierSelector — containment check — done (commit f8fc2adc)
- [x] Task 6: RegistryAccessorsMixin.tier_of -> tiers_of — done (commit ace38fbe)

## Phase 2 — /provider command surface
- [x] Task 7: /provider list/menu/status display every tier — done (commit acacacc2)
- [x] Task 8: /provider set-tier becomes additive — done (commit a624be2e, fix 9d615b76)
- [x] Task 9: /provider add + guided add-tier write tiers list — done (commit 0c0ddb75)

## Phase 3 — /tier command surface
- [x] Task 10: /tier admin subcommands — containment + additive/subtractive semantics — done (commit 96c63b74)

## Phase 4 — CLI surface
- [x] Task 11: cli/providers_cli.py + setup/yaml_writer.py multi-tier aware — done (commit 8bcf6b6d)

## Phase 5 — Integration & regression
- [x] Task 12: Fix journey/button-chain assertions — done (commit e77882e7)
- [x] Task 13: New integration test — provider in two tiers, routable from both — done (commit 79b5b0da)
- [x] Task 14: Final full regression pass — done (587/587 passed, ruff/mypy clean)

## Final whole-branch review
- [ ] Dispatched — not started

---

**Overall status:** all 14 tasks complete, final whole-branch review pending
