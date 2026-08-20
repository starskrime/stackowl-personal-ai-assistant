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
* a plugin the operator DISABLED must stay disabled;
* what loaded, what was skipped and why must be visible in one line — nobody is
  watching a boot.

AND THE TEST'S OWN LESSON, 2026-08-19. This file used to build a fake index whose
entries had a ``path`` attribute. ``PluginIndexEntry`` has no such field — it is
the DOWNLOADABLE CATALOGUE (name / url / version / sha256), not a list of what is
installed — so the double described a class that does not exist, and the whole boot
path was green while loading nothing. That is failure mode 2 in PROCESS.md: a test
double that stopped resembling the real thing. Every test below now uses REAL
DIRECTORIES in the layout the installer actually writes, and one asserts the
catalogue's shape directly so the old double cannot come back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stackowl.plugins.boot import load_installed_plugins
from stackowl.plugins.index import PluginIndexEntry

pytestmark = pytest.mark.asyncio


def _install(root: Path, name: str) -> Path:
    """A plugin on disk, in the layout ``_install_local_plugin`` writes."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return plugin_dir


class _Loader:
    """Stands in for LocalPluginLoader with its REAL method name and argument."""

    def __init__(self, *, fail: set[str] | None = None,
                 hang: set[str] | None = None) -> None:
        self.loaded: list[str] = []
        self._fail = fail or set()
        self._hang = hang or set()

    def load(self, plugin_dir: Path) -> object:
        name = Path(plugin_dir).name
        if name in self._hang:
            import time

            time.sleep(30)
        if name in self._fail:
            raise RuntimeError(f"{name} is broken")
        self.loaded.append(name)
        return object()


class _Registry:
    """The enable/disable half of PluginRegistry, with its real semantics:
    ``list()`` returns ENABLED rows only, ``exists()`` knows both."""

    def __init__(self, *, known: set[str], enabled: set[str]) -> None:
        self._known = known
        self._enabled = enabled

    def list(self) -> list[object]:
        return [type("M", (), {"name": n})() for n in sorted(self._enabled)]

    def exists(self, name: str) -> bool:
        return name in self._known


class TestItReadsWhatIsACTUALLYInstalled:
    async def test_a_plugin_in_the_directory_is_loaded(self, tmp_path: Path) -> None:
        """The whole point. A plugin dropped into ~/.stackowl/plugins/ loads."""
        _install(tmp_path, "hello")
        loader = _Loader()

        report = await load_installed_plugins(plugins_dir=tmp_path, loader=loader)

        assert loader.loaded == ["hello"]
        assert report.loaded == ("hello",)

    async def test_the_catalogue_is_not_the_installed_list(self) -> None:
        """The defect this file missed for two days, as an assertion.

        ``PluginIndexEntry`` describes something DOWNLOADABLE. Iterating it to find
        installed plugins can only ever yield "no install path recorded", which is
        exactly what happened.
        """
        fields = set(PluginIndexEntry.__dataclass_fields__)

        assert "path" not in fields, (
            "PluginIndexEntry gained a path — if the catalogue now records where a "
            "plugin was installed, this boot path should be reconsidered rather "
            "than left reading the directory"
        )
        assert {"url", "sha256"} <= fields, "this is a download catalogue, not an inventory"

    async def test_a_directory_without_a_manifest_is_not_a_plugin(
        self, tmp_path: Path
    ) -> None:
        """A stray directory — a leftover, an editor's backup — is not an install."""
        (tmp_path / "notaplugin").mkdir()
        loader = _Loader()

        report = await load_installed_plugins(plugins_dir=tmp_path, loader=loader)

        assert loader.loaded == []
        assert report.loaded == ()

    async def test_no_plugins_installed_is_a_clean_noop(self, tmp_path: Path) -> None:
        report = await load_installed_plugins(plugins_dir=tmp_path, loader=_Loader())

        assert report.loaded == ()
        assert report.skipped == ()

    async def test_a_missing_plugins_directory_is_a_clean_noop(
        self, tmp_path: Path
    ) -> None:
        """A fresh install has never had one."""
        report = await load_installed_plugins(
            plugins_dir=tmp_path / "never-created", loader=_Loader()
        )

        assert report.loaded == ()


class TestTheOperatorsDisableIsHonoured:
    async def test_a_disabled_plugin_is_not_loaded(self, tmp_path: Path) -> None:
        """`/plugins disable` writes enabled = 0. A boot that loaded it anyway would
        be an actuator the operator can see and the platform ignores."""
        _install(tmp_path, "off")
        loader = _Loader()

        report = await load_installed_plugins(
            plugins_dir=tmp_path, loader=loader,
            registry=_Registry(known={"off"}, enabled=set()),
        )

        assert loader.loaded == []
        assert report.skipped == (("off", "disabled by the operator"),)

    async def test_a_plugin_the_registry_never_heard_of_still_loads(
        self, tmp_path: Path
    ) -> None:
        """Bakir's ESC-16 answer: the directory IS the consent. A hand-placed plugin
        with no registry row is not 'disabled', it is simply not installed through
        the CLI."""
        _install(tmp_path, "handplaced")
        loader = _Loader()

        await load_installed_plugins(
            plugins_dir=tmp_path, loader=loader,
            registry=_Registry(known=set(), enabled=set()),
        )

        assert loader.loaded == ["handplaced"]

    async def test_an_unreadable_registry_does_not_silence_the_plugins(
        self, tmp_path: Path
    ) -> None:
        """Fail towards the working system: a registry we cannot read decides
        nothing, rather than disabling everything."""
        _install(tmp_path, "hello")
        loader = _Loader()

        class _Broken:
            def list(self) -> list[object]:
                raise RuntimeError("database is gone")

        await load_installed_plugins(
            plugins_dir=tmp_path, loader=loader, registry=_Broken(),
        )

        assert loader.loaded == ["hello"]


class TestOneBadPluginCannotStopTheBoot:
    async def test_a_raising_plugin_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        _install(tmp_path, "broken")
        loader = _Loader(fail={"broken"})

        report = await load_installed_plugins(plugins_dir=tmp_path, loader=loader)

        assert report.loaded == ()
        assert report.skipped and report.skipped[0][0] == "broken"
        assert "RuntimeError" in report.skipped[0][1], "the reason must survive"

    async def test_a_bad_plugin_does_not_stop_the_GOOD_ones(self, tmp_path: Path) -> None:
        _install(tmp_path, "broken")
        _install(tmp_path, "fine")
        loader = _Loader(fail={"broken"})

        report = await load_installed_plugins(plugins_dir=tmp_path, loader=loader)

        assert report.loaded == ("fine",)
        assert [n for n, _ in report.skipped] == ["broken"]

    async def test_a_HANGING_plugin_cannot_wedge_startup(self, tmp_path: Path) -> None:
        _install(tmp_path, "slow")
        loader = _Loader(hang={"slow"})

        report = await asyncio.wait_for(
            load_installed_plugins(
                plugins_dir=tmp_path, loader=loader, timeout_seconds=0.05,
            ),
            timeout=10,
        )

        assert report.loaded == ()
        assert "timed out" in report.skipped[0][1]


class TestItNeverBreaksAnUnwiredBoot:
    async def test_no_directory_is_a_noop(self) -> None:
        assert (await load_installed_plugins(
            plugins_dir=None, loader=_Loader())).loaded == ()

    async def test_no_loader_is_a_noop(self, tmp_path: Path) -> None:
        _install(tmp_path, "hello")
        assert (await load_installed_plugins(
            plugins_dir=tmp_path, loader=None)).loaded == ()
