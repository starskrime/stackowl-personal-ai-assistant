"""Replacing a bundled provider entry is announced at INFO, not DEBUG.

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

WHAT THIS DOES NOT DO. It does not stop the replacement. Whether a user file may replace
a bundled entry at all, rather than only introduce a new name, removes an advertised
capability and is ESC-23, open with Bakir. This closes the "nothing notices" half only,
which needs no ruling.
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


class TestTheReplacementIsVisibleInProduction:
    def test_it_logs_at_INFO_and_names_the_url_change(self, _merge, caplog) -> None:
        """THE CASE THAT MATTERS: the same name pointing somewhere else."""
        caplog.set_level(logging.INFO)

        _merge(
            {"openai": "https://api.openai.com/v1"},
            {"openai": "http://elsewhere.invalid/v1"},
        )

        hits = [r for r in caplog.records if "REPLACED a bundled provider" in r.message]
        assert hits, "a bundled entry was replaced and production logged nothing"
        fields = getattr(hits[0], "_fields", {})
        assert fields.get("base_url_changed") is True
        assert fields.get("to_base_url") == "http://elsewhere.invalid/v1"

    def test_it_is_not_emitted_at_DEBUG_only(self, _merge, caplog) -> None:
        """The regression guard. A future edit that drops this back to debug makes
        the event invisible again at the level production actually runs."""
        caplog.set_level(logging.INFO)

        _merge({"a": "u1"}, {"a": "u2"})

        assert any(
            r.levelno >= logging.INFO and "REPLACED a bundled provider" in r.message
            for r in caplog.records
        )

    def test_ADDING_a_new_provider_is_not_announced(self, _merge, caplog) -> None:
        """The advertised, benign use of the directory. Announcing it too would make
        the line noise, and a line people learn to ignore is a line that is not
        evidence."""
        caplog.set_level(logging.INFO)

        result = _merge({"a": "u1"}, {"brandnew": "u2"})

        assert {e.name for e in result} == {"a", "brandnew"}
        assert not [r for r in caplog.records if "REPLACED a bundled provider" in r.message]

    def test_the_replacement_still_takes_effect(self, _merge) -> None:
        """Behaviour is UNCHANGED by this commit — only the silence is fixed. If this
        ever fails, ESC-23 was decided by accident instead of by Bakir."""
        result = _merge({"a": "bundled"}, {"a": "user"})

        assert [e.base_url for e in result] == ["user"]
