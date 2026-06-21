"""Tests for the live identity-resolver hot-reload handler.

When ``identity.aliases`` changes in stackowl.yaml, the ConfigWatcher emits
``settings_reloaded`` with the new Settings. The handler must mutate the SHARED
IdentityResolver in place so every durable-knowledge consumer (preferences,
fact staging) picks up the new mapping without a restart.
"""
from __future__ import annotations

from stackowl.config.settings import IdentitySettings, Settings
from stackowl.startup.identity_reload import make_identity_reload_handler
from stackowl.tenancy.identity import IdentityResolver


def _settings_with_aliases(aliases: dict[str, list[str]]) -> Settings:
    # NOTE: Settings drops init_settings from its source chain (only env + YAML),
    # so `Settings(identity=...)` is ignored. Build a base Settings (defaults) and
    # swap in the identity section via model_copy. Production reads aliases from
    # the YAML file via the ConfigWatcher's `lambda: Settings()`, so this only
    # affects how the TEST injects a known alias map.
    return Settings().model_copy(update={"identity": IdentitySettings(aliases=aliases)})


def test_settings_payload_refreshes_resolver_live() -> None:
    resolver = IdentityResolver({})  # unconfigured at startup
    assert resolver.resolve("telegram:123") == "telegram:123"

    handler = make_identity_reload_handler(resolver)
    handler(_settings_with_aliases({"owner-primary": ["telegram:123", "slack:U0"]}))

    # The same instance now resolves the new mapping — no restart, no re-inject.
    assert resolver.resolve("telegram:123") == "owner-primary"
    assert resolver.resolve("slack:U0") == "owner-primary"


def test_non_settings_payload_is_ignored() -> None:
    # The /config and /provider slash commands emit a dict payload — the handler
    # must NOT touch the resolver for those.
    resolver = IdentityResolver({"owner-primary": ["telegram:123"]})
    handler = make_identity_reload_handler(resolver)

    handler({"key": "some.other.setting"})  # dict, not Settings

    assert resolver.resolve("telegram:123") == "owner-primary"  # unchanged


def test_handler_never_raises_on_bad_payload() -> None:
    resolver = IdentityResolver({})
    handler = make_identity_reload_handler(resolver)
    # None / arbitrary objects must be swallowed (logged), never raised — a
    # throwing handler would kill the watcher dispatch.
    handler(None)
    handler(object())
    assert resolver.resolve("x") == "x"


def test_shared_instance_propagates_to_both_holders() -> None:
    """The single-shared-instance design: one resolver handed to both holders.

    The orchestrator gives StepServices.identity_resolver and
    FactExtractor._identity_resolver the SAME IdentityResolver. An in-place
    update via the reload handler must be visible through BOTH references.
    Pre-fix (two separate instances / no subscriber) this is impossible — that
    is the gun this test arms.
    """
    shared = IdentityResolver({})

    # Stand-ins for the two live holders that each keep a reference.
    class _HolderA:
        def __init__(self, r: IdentityResolver) -> None:
            self.identity_resolver = r

    class _HolderB:
        def __init__(self, r: IdentityResolver) -> None:
            self._identity_resolver = r

    a = _HolderA(shared)
    b = _HolderB(shared)

    handler = make_identity_reload_handler(shared)
    handler(_settings_with_aliases({"owner-primary": ["telegram:9", "slack:Z"]}))

    assert a.identity_resolver.resolve("telegram:9") == "owner-primary"
    assert b._identity_resolver.resolve("slack:Z") == "owner-primary"
