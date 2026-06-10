"""Pin the commit_coupling assignment of every write/consequential tool (D1 §6.1).

Undeclared write/consequential tools fail-safe to "unconfirmed" at resolution
time (see delegate_task), but this map pins the DECLARED couplings so a future
edit cannot silently re-classify an effect as more certain than it is.
"""

from __future__ import annotations

from stackowl.tools.registry import ToolRegistry

# The closed map. transactional = atomic with our own ledger/local-fs write;
# unconfirmed = lossy-ack boundary (network/shell/process/browser/code).
#
# NOTE: the keys are the REGISTERED tool names (Tool.name), which for several
# tools differ from their module filename: undo_store.py -> "undo_write",
# process_tool.py -> "process", browse.py -> "browser_browse", dialog.py ->
# "browser_dialog". The browser tool family also registers many granular
# sub-tools (click/type/scroll/eval_js/upload/download/cookies/tabs/close); each
# drives a remote browser across a lossy-ack boundary, so all are "unconfirmed"
# per the plan's classification (browser side effects whose downstream we don't
# control). They are pinned here so the completeness test below stays green.
_EXPECTED: dict[str, str] = {
    "delegate_task": "unconfirmed",
    "sessions_send": "unconfirmed",
    "sessions_spawn": "unconfirmed",
    "batch_approve": "transactional",
    "apply_patch": "transactional",
    "edit": "transactional",
    "undo_write": "transactional",
    "write_file": "transactional",
    "memory": "transactional",
    "process": "unconfirmed",
    "cronjob": "transactional",
    "shell": "unconfirmed",
    "browser_browse": "unconfirmed",
    "browser_dialog": "unconfirmed",
    "browser_click": "unconfirmed",
    "browser_type": "unconfirmed",
    "browser_scroll": "unconfirmed",
    "browser_eval_js": "unconfirmed",
    "browser_upload": "unconfirmed",
    "browser_download": "unconfirmed",
    "browser_cookies_set": "unconfirmed",
    "browser_cookies_clear": "unconfirmed",
    "browser_tab_close": "unconfirmed",
    "browser_close": "unconfirmed",
    "execute_code": "unconfirmed",
    "skill_manage": "transactional",
    "synthesize_skills": "transactional",
    "owl_build": "transactional",
    "tool_build": "transactional",
    "send_file": "unconfirmed",
    "send_message": "unconfirmed",
}


def test_declared_couplings_match_the_pin() -> None:
    reg = ToolRegistry.with_defaults()
    for name, expected in _EXPECTED.items():
        tool = reg.get(name)
        assert tool is not None, f"{name} not registered"
        actual = tool.manifest.commit_coupling
        assert actual == expected, (
            f"{name}: commit_coupling={actual!r} expected {expected!r}"
        )


def test_every_side_effecting_tool_declares_a_coupling() -> None:
    """No write/consequential default tool may leave commit_coupling undeclared.

    A None on a side-effecting tool is the fail-safe (treated unconfirmed) but we
    require an EXPLICIT declaration so the classification is a reviewed decision,
    not an accident.
    """
    reg = ToolRegistry.with_defaults()
    undeclared: list[str] = []
    for tool in reg.all():
        m = tool.manifest
        if m.action_severity in ("write", "consequential") and m.commit_coupling is None:
            undeclared.append(tool.name)
    assert undeclared == [], f"side-effecting tools missing commit_coupling: {undeclared}"
