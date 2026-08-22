"""`auto_restart.delay_minutes` says hot_reload:True — make that true.

BAKIR, 2026-08-22: "Why core take long time to restart himself to use the new
code". Measured: ~5.5 minutes from the last file WRITE to serving new code, of
which 300 SECONDS is the quiet-period debounce and ~25s is the actual boot
(migrations 7ms, providers 739ms). The wait IS the cost, and `delay_minutes` is
its knob.

THE DEFECT. That field carries ``json_schema_extra={"hot_reload": True}`` and was
not hot-reloadable: `CodeWatcher` is constructed once in the startup path with
``quiet_period_s=delay_minutes * 60.0`` and nothing re-read it. So lowering the
delay changed nothing until the next restart — and that restart still used the OLD
window. The operator shortens the wait, then waits the old amount to discover
whether it worked.

A setting that advertises hot-reload with no reader for the change is the first of
this codebase's four recurring shapes — a write with no reader — wearing a config
marker instead of a database column.
"""

from __future__ import annotations

import os
from pathlib import Path

from stackowl.config.auto_restart_settings import AutoRestartSettings
from stackowl.config.settings import Settings
from stackowl.runtime.code_watcher import CodeWatcher
from stackowl.startup.auto_restart_reload import make_auto_restart_reload_handler


class _FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _watcher(quiet: float, tmp_path: Path) -> CodeWatcher:
    return CodeWatcher([tmp_path], lambda: None, quiet_period_s=quiet)


def test_the_field_still_CLAIMS_hot_reload() -> None:
    """If the claim is ever dropped, this fix is pointless and the test should say
    so rather than silently guarding nothing."""
    field = AutoRestartSettings.model_fields["delay_minutes"]
    assert (field.json_schema_extra or {}).get("hot_reload") is True


def test_a_settings_payload_changes_the_LIVE_quiet_period(tmp_path: Path) -> None:
    """THE POINT. Not "the handler ran" — the watcher's window actually moves."""
    w = _watcher(300.0, tmp_path)
    assert w._quiet_period == 300.0

    # A REAL Settings — the handler type-guards on it, and rightly so: the same
    # `settings_reloaded` event carries dict payloads from the slash commands. My
    # first version of this test passed a stand-in and was correctly ignored.
    base = Settings()
    settings = base.model_copy(update={
        "runtime": base.runtime.model_copy(update={
            "auto_restart": AutoRestartSettings(delay_minutes=0.5),
        }),
    })
    make_auto_restart_reload_handler(w)(settings)

    assert w._quiet_period == 30.0, (
        f"the live watcher still waits {w._quiet_period}s — the setting is "
        "declared hot_reload and nothing read it"
    )


def test_a_dict_payload_is_IGNORED(tmp_path: Path) -> None:
    """`settings_reloaded` carries a small dict from the config/provider slash
    commands as well as a real Settings. Mutating the watcher on those would let a
    UI notification silently change restart behaviour."""
    w = _watcher(300.0, tmp_path)
    make_auto_restart_reload_handler(w)({"provider": "NeraAiRaw"})
    assert w._quiet_period == 300.0


def test_the_handler_never_raises(tmp_path: Path) -> None:
    """It runs on the watcher thread's dispatch. An exception here would kill
    reload for every other subscriber."""
    w = _watcher(300.0, tmp_path)
    make_auto_restart_reload_handler(w)(object())  # not Settings, not a dict
    assert w._quiet_period == 300.0


def test_shortening_RELEASES_a_change_already_pending(tmp_path: Path) -> None:
    """The operator shortens the delay BECAUSE something is already waiting. If the
    new window only applied to the next change, they would still sit out the old
    timer they just shortened — the exact frustration this fixes."""
    clock = _FakeClock()
    fired: list[int] = []
    src = tmp_path / "src"
    src.mkdir()
    f = src / "a.py"
    # EXPLICIT mtimes: two same-length writes inside one filesystem tick can share
    # an mtime, so the watcher would see no change at all and the test would fail
    # for a reason that has nothing to do with the quiet period.
    f.write_text("x = 1\n")
    os.utime(f, (500.0, 500.0))
    w = CodeWatcher([src], lambda: fired.append(1), quiet_period_s=300.0, clock=clock)
    w._on_start()

    f.write_text("x = 2\n")
    os.utime(f, (600.0, 600.0))
    w._check_once()          # detected, pending
    clock.advance(40.0)
    w._check_once()
    assert fired == [], "must still be waiting out the 300s window"

    w.apply_quiet_period(30.0)   # operator shortens it
    w._check_once()
    assert fired == [1], (
        "the pending change did not release — the operator would wait out the old "
        "window after shortening it"
    )
