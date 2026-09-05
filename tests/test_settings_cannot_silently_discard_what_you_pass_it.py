"""D18.4 — the loader accepted constructor arguments and threw them away.

``settings_customise_sources`` takes ``init_settings`` as a parameter and never
returns it::

    return (env_settings, _YamlSource(settings_cls, config_path))

pydantic-settings only consults the sources a model RETURNS, so every keyword
argument passed to ``Settings(...)`` was silently discarded. Measured 2026-09-05,
before the fix::

    BrowserSettings(screenshots_dir="/srv/shots")          -> /srv/shots        OK
    Settings(browser={"screenshots_dir": "/srv/shots"})    -> ~/.stackowl/...   DISCARDED
    Settings(webhook={"port": 9999})                       -> 8766              DISCARDED
    Settings(**{"no_such_key": 1, "webhook": {"port": "not-a-number"}})  -> ACCEPTED

The sub-model was never the problem — it validates correctly on its own. The
composition was, and it failed in the direction that says nothing.

WHAT THIS REACHED, all measured rather than reasoned:

* **`tests/test_webhook_receiver_supervisor_contract.py`** built its receiver with
  ``Settings(webhook=WebhookSettings(enabled=True, sources={}))``. The
  ``enabled=True`` never arrived, so the supervisor contract for an ENABLED
  receiver was being asserted against a DISABLED one.
* **`tests/test_story_6_4b.py`** set a 10 MB memory ceiling that never arrived.
* **`tests/test_the_example_config_cannot_go_stale.py`** — D18.2's own acceptance
  check — asserted that the generated example is real config by calling
  ``Settings(**data)``. It validated NOTHING: the same call accepts an unknown key
  and a non-numeric port. A test that passes immediately may be vacuous, and this
  one passed immediately.

That last one is the reason this guard is written as it is. The defect's signature
is that everything looks fine — no exception, no warning, correct-looking defaults
— so the only way to catch it is to assert that a value you passed COMES BACK, and
that a value that is nonsense is REFUSED. Both directions, or the next silent drop
looks exactly like a pass again.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.config.settings import Settings


@pytest.mark.tripwire
def test_a_value_you_pass_in_comes_back_out() -> None:
    """The positive direction: an override must actually take effect."""
    settings = Settings(webhook={"port": 9999})
    assert settings.webhook.port == 9999, (
        "Settings discarded a constructor argument. `settings_customise_sources` "
        "must RETURN init_settings — pydantic-settings consults only the sources "
        "it is given, so omitting it silently drops every keyword argument."
    )


@pytest.mark.tripwire
def test_nonsense_is_refused_rather_than_ignored() -> None:
    """The negative direction, which is what makes the positive one meaningful.

    While kwargs were dropped, `Settings(**anything)` succeeded — so any test
    asserting "it loads" was asserting nothing at all. This is the assertion that
    keeps D18.2's round-trip check honest.
    """
    with pytest.raises(ValidationError):
        Settings(webhook={"port": "not-a-number"})

    with pytest.raises(ValidationError):
        Settings(webhook={"prot": 8766})  # a typo INSIDE a section


def test_an_unknown_top_level_key_is_announced_not_swallowed(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The root uses extra="ignore" while every sub-model uses extra="forbid".

    So a typo inside a section raises (asserted above), and a typo in a SECTION
    NAME used to discard that whole section in silence — the root being exactly
    where section names live. It now WARNS and still boots: refusing would mean a
    config that used to work suddenly fails, and locking the operator out of his
    platform to catch a typo is a bad trade.
    """
    (tmp_path / "stackowl.yaml").write_text("webhok:\n  port: 1\nsystem:\n  timezone: UTC\n")
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))

    with caplog.at_level("WARNING"):
        settings = Settings()

    assert settings.system.timezone == "UTC", "it must still boot, not refuse"
    assert any("webhok" in r.getMessage() for r in caplog.records), (
        "a mistyped section name was ignored without a word — the whole section's "
        "configuration vanished silently"
    )


def test_a_nested_section_is_validated_not_replaced() -> None:
    """A sub-model instance must survive composition, not be swapped for defaults.

    `BrowserSettings(screenshots_dir=...)` always worked on its own; it was the
    trip through `Settings` that lost it. That asymmetry is what made the defect
    invisible — every unit test of a sub-model passed.
    """
    from pathlib import Path

    settings = Settings(browser={"screenshots_dir": "/srv/shots"})
    assert settings.browser.screenshots_dir == Path("/srv/shots")


def test_precedence_is_explicit_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit argument outranks the environment.

    This is pydantic-settings' documented default order and the intuitive one: a
    caller who names a value meant it. It is asserted because the ORDER of the
    returned tuple is the whole mechanism, and a future edit could reorder it
    without any other test noticing.
    """
    monkeypatch.setenv("STACKOWL_JUDGE_TIER", "powerful")
    settings = Settings(judge_tier="fast")
    assert settings.judge_tier == "fast"
