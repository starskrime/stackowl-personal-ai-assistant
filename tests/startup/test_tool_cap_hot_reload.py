"""ESC-35 — `tool_count_cap` claimed hot_reload and was not reloadable.

THE DEFECT. `OrchestratorSettings.tool_count_cap` is declared
``json_schema_extra={"hot_reload": True}``. The presented tool array is memoized
per session in `infra/presented_tools.py`, and the only `clear()` callers in
``src/`` were the skill store and two owl paths — none a config-reload path. So
changing the cap reached only sessions STARTED afterwards.

What makes it worth fixing rather than documenting is the operator's experience:
because the field claims hot-reload, `/config set` returns ``✓ tool_count_cap =
40`` with NO "restart required" suffix, while every live conversation carries on
with the old roster. The setting was honest and nothing downstream honoured it.

Same defect as `auto_restart.delay_minutes`, fixed 2026-08-22, whose own comment
names the shape: a setting that advertises hot-reload with no reader for the
change is the write-with-no-reader wearing a config marker.

These tests pin the EFFECT (the memo is actually dropped) rather than the call,
and they pin the two ways an over-eager fix would be wrong: dropping on an
unrelated reload, and dropping the budget basis along with the memo.
"""

from __future__ import annotations

from stackowl.config.settings import Settings
from stackowl.infra import presented_tools
from stackowl.startup.tool_cap_reload import make_tool_cap_reload_handler


def _seed_memo() -> presented_tools._MemoKey:
    key = presented_tools.make_key(
        session_key="s1", owl="secretary", provider="p", protocol="openai",
        window=16000, hydrated=(),
    )
    presented_tools.put(key, [{"function": {"name": "x"}}])
    return key


def _reset() -> None:
    presented_tools.clear()
    presented_tools._last_tool_count_cap = None


# ---------------------------------------------------------------------------
# The effect
# ---------------------------------------------------------------------------

def test_changing_the_cap_drops_the_memo() -> None:
    _reset()
    key = _seed_memo()
    assert presented_tools.get(key) is not None

    assert presented_tools.apply_tool_count_cap(40) is False, "first sight, no wipe"
    assert presented_tools.get(key) is not None, "a warm memo must survive first sight"

    assert presented_tools.apply_tool_count_cap(18) is True
    assert presented_tools.get(key) is None, "the live session must pick up the new cap"


def test_the_same_cap_twice_drops_nothing() -> None:
    """An unrelated config edit re-emits settings_reloaded. Wiping every live
    conversation's roster because someone changed a log level would be a worse
    bug than the one being fixed."""
    _reset()
    presented_tools.apply_tool_count_cap(40)
    key = _seed_memo()
    assert presented_tools.apply_tool_count_cap(40) is False
    assert presented_tools.get(key) is not None


def test_the_budget_BASIS_survives_a_cap_change() -> None:
    """The distinction `_drop_memo` exists for. A cap change says "this array may
    be stale". It does NOT say "re-measure how much room the history leaves" —
    conflating those is what let a 3-second browser recycle shrink a live agent's
    toolset (D05.4)."""
    _reset()
    key = presented_tools.make_key(
        session_key="s2", owl="secretary", provider="p", protocol="openai",
        window=16000, hydrated=(),
    )
    presented_tools.put(key, [{"function": {"name": "x"}}])
    basis = presented_tools.budget_basis(key, 1234)

    presented_tools.apply_tool_count_cap(40)
    presented_tools.apply_tool_count_cap(12)

    assert presented_tools.get(key) is None, "memo dropped"
    assert presented_tools.budget_basis(key, 9999) == basis, (
        "the basis must survive — re-measuring it is what amputates the toolset"
    )


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------

def test_the_handler_applies_a_real_Settings_payload() -> None:
    _reset()
    handler = make_tool_cap_reload_handler()
    settings = Settings()
    handler(settings)  # first sight
    key = _seed_memo()

    # Settings are FROZEN — construct a new one rather than mutating, which is
    # also what the ConfigWatcher does (`settings_factory=lambda: Settings()`),
    # so this exercises the real payload shape.
    bumped = settings.model_copy(
        update={
            "orchestrator": settings.orchestrator.model_copy(
                update={"tool_count_cap": settings.orchestrator.tool_count_cap + 5},
            ),
        },
    )
    assert bumped.orchestrator.tool_count_cap != settings.orchestrator.tool_count_cap
    handler(bumped)

    assert presented_tools.get(key) is None


def test_a_dict_payload_is_ignored() -> None:
    """The config and tier slash commands emit small dicts on the same event.
    Acting on one would read tool_count_cap off an object that has none."""
    _reset()
    presented_tools.apply_tool_count_cap(40)
    key = _seed_memo()
    make_tool_cap_reload_handler()({"some": "dict"})
    assert presented_tools.get(key) is not None


def test_the_handler_never_raises() -> None:
    """A reload error must not be able to kill the watcher thread."""
    handler = make_tool_cap_reload_handler()
    for payload in (None, 42, "text", object(), {"a": 1}):
        handler(payload)


def test_it_is_wired_on_the_settings_reloaded_event() -> None:
    """Registered != reachable. The handler existing proves nothing if nothing
    subscribes it, which is the exact shape this whole escalation is about."""
    from pathlib import Path

    src = Path("src/stackowl/startup/orchestrator.py").read_text(encoding="utf-8")
    assert "make_tool_cap_reload_handler" in src
    assert '"settings_reloaded", make_tool_cap_reload_handler()' in src
