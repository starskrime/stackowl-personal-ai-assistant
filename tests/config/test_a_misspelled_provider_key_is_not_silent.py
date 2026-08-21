"""A key the operator meant to set must never disappear without a word.

FOUND 2026-08-20 by the D16.3 panel, verified by construction rather than by
reading:

    >>> ProviderConfig(name='x', protocol='openai', default_model='m',
    ...                tiers=('fast',), suports_native_tools=False)
    >>> c.supports_native_tools
    True                      # the typo was dropped; no error, no log, no trace

``ProviderConfig`` declares no ``model_config``, so pydantic defaults to
``extra="ignore"``. Its SIBLING in the same file, ``ModelOverride``
(``config/provider.py:18``), carries ``ConfigDict(frozen=True, extra="forbid")``.
One of the two models an operator hand-edits rejects a typo; the other swallows it.

WHY THIS IS THE EXPENSIVE KIND OF BUG HERE. Nine of ``ProviderConfig``'s seventeen
fields are unreachable from any ``/provider`` command — ``context_chars``,
``supports_native_tools``, ``max_output_tokens``, ``cache_ttl`` and the rest are
hand-edited YAML or nothing. So the fields most likely to be typed by hand are
exactly the ones whose typos vanish, and every one of them describes what a backend
can DO. A dropped ``supports_native_tools`` does not fail — it silently selects the
opposite capability path and the turn still completes.

WHY A WARNING AND NOT ``extra="forbid"``. Forbidding is the stricter fix and the
panel's Stability lens preferred it, on the reasoning that a loud boot failure is
the cheapest possible discovery. That is right for THIS box — the live config was
measured clean, 4 entries, 7 known keys each, no unknown keys. It is not obviously
right for a deployment we cannot see: making a previously-accepted config file fail
to boot on upgrade is a product decision about other people's installations, not a
bug fix. So this surfaces the key loudly and keeps booting, and the stricter option
is escalated rather than taken unilaterally.

The rule being satisfied is the standing one: no hidden errors — recover loudly or
propagate. Dropping a key the operator typed is neither.
"""

from __future__ import annotations

import pytest

from stackowl.config.provider import ProviderConfig


def _cfg(**over: object) -> ProviderConfig:
    base: dict = {
        "name": "x", "protocol": "openai", "default_model": "m", "tiers": ("fast",),
    }
    base.update(over)
    return ProviderConfig(**base)  # type: ignore[arg-type]


class TestAnUnknownKeyIsAnnounced:
    def test_a_misspelled_capability_key_warns_and_names_it(self, caplog) -> None:
        """The measured case. `suports_native_tools` is one letter from a field that
        decides which tool-calling path a backend takes."""
        with caplog.at_level("WARNING"):
            cfg = _cfg(suports_native_tools=False)

        assert cfg.supports_native_tools is True, "the typo must still not take effect"
        messages = " ".join(r.message for r in caplog.records)
        assert "unknown" in messages.lower(), (
            f"dropped silently; records were {[r.message for r in caplog.records]}"
        )
        fields = " ".join(str(getattr(r, "_fields", "")) for r in caplog.records)
        assert "suports_native_tools" in fields + messages, (
            "the warning must NAME the key, or the operator cannot find it"
        )

    def test_several_unknown_keys_are_all_named(self, caplog) -> None:
        """One line per config entry, listing every stray key — not one line per key,
        which would bury a real one under a copy-pasted block."""
        with caplog.at_level("WARNING"):
            _cfg(quriks=("a",), contex_chars=100, supports_vision=True)

        blob = " ".join(
            r.message + str(getattr(r, "_fields", "")) for r in caplog.records
        )
        for key in ("quriks", "contex_chars", "supports_vision"):
            assert key in blob, f"{key} was dropped without mention"

    def test_the_warning_names_the_provider(self, caplog) -> None:
        """A deployment can carry several backends. "an unknown key" with no name is
        a warning the operator cannot act on."""
        with caplog.at_level("WARNING"):
            _cfg(name="NeraAiRaw", typo_key=1)

        blob = " ".join(
            r.message + str(getattr(r, "_fields", "")) for r in caplog.records
        )
        assert "NeraAiRaw" in blob


class TestAValidConfigIsUnchanged:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"enabled": False},
            {"base_url": "http://x/v1", "api_key": "k"},
            {"supports_native_tools": False, "context_chars": 1000},
        ],
    )
    def test_no_warning_for_known_keys(self, caplog, kwargs: dict) -> None:
        """The warning has to mean something. Every live provider entry on this box
        uses only known keys, so a correct config must stay silent."""
        with caplog.at_level("WARNING"):
            cfg = _cfg(**kwargs)

        assert not [r for r in caplog.records if "unknown" in r.message.lower()]
        assert cfg.name == "x"

    def test_the_live_config_shape_is_silent(self, caplog) -> None:
        """Generated from the SAME field set the code uses, not a hand-copied list —
        a fixture that drifts from the model is how this class of test stops
        resembling the real thing."""
        known = {
            "name": "NeraAiRaw", "protocol": "openai", "default_model": "neraai-v1-raw",
            "tiers": ("fast", "standard", "powerful"), "enabled": True,
            "api_key": "k", "base_url": "http://x/v1",
        }
        assert set(known) <= set(ProviderConfig.model_fields), (
            "this fixture drifted from the model"
        )

        with caplog.at_level("WARNING"):
            ProviderConfig(**known)  # type: ignore[arg-type]

        assert not [r for r in caplog.records if "unknown" in r.message.lower()]
