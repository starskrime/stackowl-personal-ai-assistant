"""Settings were re-read from disk 1,060 times a day, on the hot path.

MEASURED 2026-08-31. ``[config] Loaded providers`` is logged by
``Settings._post_init``, a pydantic model validator that runs on EVERY
construction, so the log counts them exactly::

    5,499 constructions across the retained logs
    1,060 today

and the line immediately preceding each one names the hot path::

    324  [pipeline] execute: tool_loop entry
    308  [asyncio_backend] run: step ok
     91  [format] OutputStyle.enforce: exit
     88  [owls] revalidate_agent_owls: exit

Each construction reads and parses ``stackowl.yaml`` and runs the full model
validation: **19ms**, measured over 30 runs (min 18.8, p50 19.0, max 19.9) against
a 2,034-byte file. A ``stat()`` on the same file is **0.0044ms** — 4,300x cheaper.

WHAT THIS IS NOT. It is NOT the cause of the provider_registry timeouts; that was
a live inference in the registry's health check, and 19ms cannot starve a 5,000ms
window. The correlation between the two was checked and refuted, and this stands on
its own numbers.

THE RELOAD CONTRACT ALREADY EXISTS AND IS NOT THIS. ``ConfigWatcher`` polls the
file's mtime every 5s, debounces until it settles, and emits ``settings_reloaded``
with a fresh ``Settings``. That is how a config change is meant to propagate.
Re-parsing the file inside the tool loop is not the reload mechanism; it is an
accident that costs 19ms of event-loop time and one INFO line every time.

SO THE CACHE IS KEYED ON WHAT WOULD CHANGE THE ANSWER — the config file's
(mtime, size) and the STACKOWL_ environment. That makes it strictly FRESHER than
the watcher (which waits up to two 5s polls) while doing a stat instead of a parse.
"""

from __future__ import annotations

import time

import pytest

from stackowl.config.settings import Settings, cached_settings, reset_settings_cache
from stackowl.paths import StackowlHome


@pytest.fixture(autouse=True)
def _clean_cache():  # noqa: ANN202
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_the_same_object_comes_back_when_nothing_changed() -> None:
    """The whole point: 1,060 parses a day become one."""
    first = cached_settings()
    assert cached_settings() is first


def test_it_is_a_real_Settings() -> None:
    assert isinstance(cached_settings(), Settings)


def test_a_CHANGED_CONFIG_FILE_invalidates_it(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Fresher than the watcher, which waits up to two 5-second polls."""
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text("test_mode: false\n", encoding="utf-8")
    monkeypatch.setattr(StackowlHome, "config_file", staticmethod(lambda: cfg))
    reset_settings_cache()

    first = cached_settings()
    # A byte-size change is enough; mtime resolution is not relied on alone.
    cfg.write_text("test_mode: false\nsettings_watch: false\n", encoding="utf-8")

    assert cached_settings() is not first


def test_an_ENV_change_invalidates_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """`settings_customise_sources` puts env FIRST, so a cache keyed only on the
    file would answer with a value the environment has already overridden — and
    every test that sets STACKOWL_* and then drives a hot path would silently read
    the stale one."""
    first = cached_settings()
    monkeypatch.setenv("STACKOWL_TEST_MODE", "true")

    assert cached_settings() is not first


def test_an_UNRELATED_env_change_does_not_invalidate_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only STACKOWL_ can reach these settings, so nothing else may cost a reparse
    — otherwise any subprocess env tweak reintroduces the 19ms."""
    first = cached_settings()
    monkeypatch.setenv("SOMETHING_ELSE", "x")

    assert cached_settings() is first


def test_a_MISSING_config_file_still_answers(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """A fresh install has no config file. Caching must not turn that into an
    error, and must not cache-poison once the file appears."""
    cfg = tmp_path / "absent.yaml"
    monkeypatch.setattr(StackowlHome, "config_file", staticmethod(lambda: cfg))
    reset_settings_cache()

    first = cached_settings()
    assert isinstance(first, Settings)

    cfg.write_text("test_mode: false\n", encoding="utf-8")
    assert cached_settings() is not first, "the file appearing must be noticed"


def test_it_is_MEASURABLY_cheaper_than_constructing() -> None:
    """The claim that justifies the change, asserted rather than asserted-about.
    19ms per construction against a stat; this only needs to be clearly faster,
    not a specific ratio, so the test does not become a benchmark that flakes."""
    cached_settings()  # prime

    t0 = time.perf_counter()
    for _ in range(20):
        cached_settings()
    cached_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for _ in range(3):
        Settings()
    raw_ms = ((time.perf_counter() - t0) * 1000) / 3

    assert cached_ms < raw_ms, (
        f"20 cached reads took {cached_ms:.2f}ms; ONE construction takes "
        f"{raw_ms:.2f}ms — the cache is not paying for itself"
    )


def test_reset_is_available_for_tests_that_change_the_world() -> None:
    first = cached_settings()
    reset_settings_cache()
    assert cached_settings() is not first
