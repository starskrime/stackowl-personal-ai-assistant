"""A user file may ADD a provider; it may never REPLACE a bundled one.

WHY IT MATTERS, and it is not a logging preference. `ProviderCatalog.load()` merges
`~/.stackowl/providers/*.yaml` over the 49 bundled entries with "user wins on name
collision", and the capability is advertised (`setup/minimal.py:195`). A `ProviderEntry`
carries `base_url`. And `commands/provider_command.py:886` `_add_discover` passes
`entry.base_url` together with the operator's RAW token to `list_models()` — to validate
them — BEFORE `store_secret` persists anything.

So a user file that redefines a bundled entry redirects the next credential typed for
that provider name, and until 2026-08-21 the only trace was `log.setup.debug`. Production
runs at INFO, where, per this repo's own rule, "a `log.*.debug` line does not exist when
you need it". The write happened and nothing said so.

ANSWERED 2026-08-21 (ESC-23): Bakir chose ADDITIVE-ONLY. A collision is now refused and
the bundled entry kept, logged at INFO. This deliberately removes an advertised
capability — the module docstring described the override and `setup/minimal.py` invited
it — which is exactly why it was escalated rather than assumed, and why both of those
texts changed with the behaviour.

The earlier shape of this file (announce the replacement, change nothing) is kept in the
history rather than pretended away: making it VISIBLE was the half that needed no ruling,
and it shipped first while the other half waited.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.setup import provider_catalog
from stackowl.setup.provider_catalog import ProviderCatalog


def _yaml(name: str, base_url: str) -> str:
    return (
        f"name: {name}\nlabel: {name.title()}\nprotocol: openai\ntier: fast\n"
        f"default_model: m\nbase_url: {base_url}\nneeds_api_key: true\nmodels: [m]\n"
    )


@pytest.fixture
def _merge(monkeypatch, tmp_path):
    """Drive `ProviderCatalog.load()` through REAL YAML files in a REAL directory.

    A first draft monkeypatched `_load_dir` to return prebuilt entries. It failed
    every assertion, and correctly: `load()` only consults the user directory when
    `StackowlHome.providers_dir().exists()`, so the patched user branch never ran and
    the "override" silently did nothing. That is a double that had stopped resembling
    the real thing — the second of this repo's four recurring defect shapes — and it
    would have proved the merge worked while testing nothing. Patch the PATH and let
    the real loader parse real files.
    """

    def run(bundled: dict[str, str], user: dict[str, str]):
        bundled_dir, user_dir = tmp_path / "bundled", tmp_path / "user"
        for directory, files in ((bundled_dir, bundled), (user_dir, user)):
            directory.mkdir(parents=True, exist_ok=True)
            for name, base_url in files.items():
                (directory / f"{name}.yaml").write_text(_yaml(name, base_url))

        monkeypatch.setattr(provider_catalog, "_BUNDLED_DIR", bundled_dir)
        from stackowl.paths import StackowlHome

        monkeypatch.setattr(
            StackowlHome, "providers_dir", classmethod(lambda cls: user_dir)
        )
        return ProviderCatalog.load()

    return run


class TestACollisionIsRefusedAndAnnounced:
    def test_the_bundled_base_url_SURVIVES_a_colliding_user_file(
        self, _merge
    ) -> None:
        """THE CASE THE DECISION IS ABOUT. `_add_discover` sends `entry.base_url` the
        operator's raw token before `store_secret` runs, so if the user file won here
        it would receive the next credential typed for the name "openai"."""
        result = _merge(
            {"openai": "https://api.openai.com/v1"},
            {"openai": "http://elsewhere.invalid/v1"},
        )

        assert [e.base_url for e in result] == ["https://api.openai.com/v1"]

    def test_it_logs_the_REFUSAL_at_INFO_and_names_both_urls(
        self, _merge, caplog
    ) -> None:
        caplog.set_level(logging.INFO)

        _merge(
            {"openai": "https://api.openai.com/v1"},
            {"openai": "http://elsewhere.invalid/v1"},
        )

        hits = [r for r in caplog.records if "REFUSED a user file" in r.message]
        assert hits, "a collision was refused and production logged nothing"
        fields = getattr(hits[0], "_fields", {})
        assert fields.get("would_have_changed_base_url") is True
        assert fields.get("kept_base_url") == "https://api.openai.com/v1"
        assert fields.get("ignored_base_url") == "http://elsewhere.invalid/v1"

    def test_it_is_not_emitted_at_DEBUG_only(self, _merge, caplog) -> None:
        """The regression guard. A future edit that drops this back to debug makes
        the event invisible again at the level production actually runs."""
        caplog.set_level(logging.INFO)

        _merge({"a": "u1"}, {"a": "u2"})

        assert any(
            r.levelno >= logging.INFO and "REFUSED a user file" in r.message
            for r in caplog.records
        )

    def test_ADDING_a_new_provider_is_not_announced(self, _merge, caplog) -> None:
        """The advertised, benign use of the directory. Announcing it too would make
        the line noise, and a line people learn to ignore is a line that is not
        evidence."""
        caplog.set_level(logging.INFO)

        result = _merge({"a": "u1"}, {"brandnew": "u2"})

        assert {e.name for e in result} == {"a", "brandnew"}
        assert not [r for r in caplog.records if "REFUSED a user file" in r.message]

    def test_the_replacement_no_longer_takes_effect(self, _merge) -> None:
        """The inverse of what this test asserted while ESC-23 was open. Kept as the
        same test rather than deleted, so the change of behaviour is legible in the
        history instead of looking like coverage that quietly vanished."""
        result = _merge({"a": "bundled"}, {"a": "user"})

        assert [e.base_url for e in result] == ["bundled"]

    def test_a_user_file_can_still_ADD_a_provider_alongside_a_collision(
        self, _merge
    ) -> None:
        """Refusing one name must not discard the whole file's other entries."""
        result = _merge(
            {"openai": "bundled"},
            {"openai": "hijack", "myllm": "http://mine.invalid/v1"},
        )

        by_name = {e.name: e.base_url for e in result}
        assert by_name == {"openai": "bundled", "myllm": "http://mine.invalid/v1"}
