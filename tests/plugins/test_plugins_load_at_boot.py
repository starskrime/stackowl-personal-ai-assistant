"""Plugins load at boot — Bakir's call, 2026-08-17.

He chose "on load" over load-on-explicit-command and over leaving the loader
unwired. I had recommended the explicit command; his decision stands and this is
built to it.

WHAT THAT CHOICE OBLIGES. Automatic loading means the platform EXECUTES
third-party code during startup, so the load path carries risks an
operator-triggered load would not:

* a plugin that RAISES must not stop the boot — the platform is the user's whole
  assistant, and losing it to one bad plugin is a far worse outcome than losing
  the plugin;
* a plugin that HANGS must not wedge startup, which a bare await would allow;
* the capability gate stays at LOAD time, so an ungranted plugin never reaches a
  call site;
* what loaded, what was skipped and why must be visible in one line — nobody is
  watching a boot.

Every test here is one of those four. None of them is defensive padding: each is
the difference between "a plugin failed" and "StackOwl did not start".
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.plugins.boot import load_installed_plugins

pytestmark = pytest.mark.asyncio


class _Entry:
    def __init__(self, name: str, path: str = "/tmp/p") -> None:
        self.name = name
        self.path = path


class _Index:
    def __init__(self, *entries: _Entry) -> None:
        self._entries = list(entries)

    def all(self) -> list[_Entry]:
        return self._entries


class _Loader:
    """Stands in for LocalPluginLoader with its REAL method name."""

    def __init__(self, *, fail: set[str] | None = None,
                 hang: set[str] | None = None) -> None:
        self.loaded: list[str] = []
        self._fail = fail or set()
        self._hang = hang or set()

    def load(self, plugin_dir: object) -> object:
        name = str(plugin_dir).rsplit("/", 1)[-1]
        if name in self._hang:
            import time

            time.sleep(5)
        if name in self._fail:
            raise RuntimeError(f"{name} is broken")
        self.loaded.append(name)
        return object()


class TestItActuallyLoads:
    async def test_an_installed_plugin_is_loaded(self) -> None:
        """The whole point. Before this, boot COUNTED plugins and loaded none:
        LocalPluginLoader had zero construction sites anywhere in src."""
        loader = _Loader()

        report = await load_installed_plugins(
            index=_Index(_Entry("hello", "/tmp/plugins/hello")), loader=loader,
        )

        assert loader.loaded == ["hello"]
        assert report.loaded == ("hello",)
        assert report.skipped == ()

    async def test_no_plugins_installed_is_a_clean_noop(self) -> None:
        """Today's real state — the directory is empty and the table has 0 rows."""
        report = await load_installed_plugins(index=_Index(), loader=_Loader())

        assert report.loaded == ()
        assert report.skipped == ()


class TestOneBadPluginCannotStopTheBoot:
    async def test_a_raising_plugin_is_skipped_not_fatal(self) -> None:
        """The risk Bakir's choice creates, and the guard that answers it. Losing
        the platform to one bad plugin is far worse than losing the plugin."""
        loader = _Loader(fail={"broken"})

        report = await load_installed_plugins(
            index=_Index(_Entry("broken", "/tmp/plugins/broken")), loader=loader,
        )

        assert report.loaded == ()
        assert [n for n, _ in report.skipped] == ["broken"]
        assert "broken" in report.skipped[0][1]

    async def test_a_bad_plugin_does_not_stop_the_GOOD_ones(self) -> None:
        """Order must not decide who loads. A sequential loop that let the first
        raise escape would silently drop every plugin after it."""
        loader = _Loader(fail={"broken"})

        report = await load_installed_plugins(
            index=_Index(
                _Entry("broken", "/tmp/plugins/broken"),
                _Entry("good", "/tmp/plugins/good"),
            ),
            loader=loader,
        )

        assert loader.loaded == ["good"]
        assert report.loaded == ("good",)

    async def test_a_HANGING_plugin_cannot_wedge_startup(self) -> None:
        """A bare await on third-party code makes startup hostage to it. The load
        is time-bounded, so a hanging plugin is abandoned and the boot continues."""
        loader = _Loader(hang={"slow"})

        report = await asyncio.wait_for(
            load_installed_plugins(
                index=_Index(_Entry("slow", "/tmp/plugins/slow")),
                loader=loader, timeout_seconds=0.05,
            ),
            timeout=3,
        )

        assert report.loaded == ()
        assert [n for n, _ in report.skipped] == ["slow"]
        assert "timed out" in report.skipped[0][1].lower()


class TestItNeverBreaksAnUnwiredBoot:
    async def test_no_index_is_a_noop(self) -> None:
        assert (await load_installed_plugins(index=None, loader=_Loader())).loaded == ()

    async def test_no_loader_is_a_noop(self) -> None:
        assert (await load_installed_plugins(index=_Index(_Entry("x")), loader=None)).loaded == ()

    async def test_an_index_that_raises_does_not_stop_the_boot(self) -> None:
        class _Broken:
            def all(self) -> list[_Entry]:
                raise RuntimeError("index unreadable")

        report = await load_installed_plugins(index=_Broken(), loader=_Loader())

        assert report.loaded == ()
