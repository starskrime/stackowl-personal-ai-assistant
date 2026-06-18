"""Pin the corrected action_severity classification of the mutating tools.

shell and write_file genuinely MUTATE the world; they were latently mis-labeled
"read" (the manifest default). They are now "write" so the durable ledger and the
delegation side-effect gate are correct — WITHOUT adding any consent prompt (the
consent gate fires only on "consequential"). web_fetch deliberately stays "read"
(a guarded GET in the researcher preset; flagging it would disable delegation
self-heal for research owls).
"""

from __future__ import annotations

from stackowl.tools.registry import ToolRegistry


def test_mutating_tools_are_write_severity() -> None:
    reg = ToolRegistry.with_defaults()
    assert reg.get("shell").manifest.action_severity == "write"
    assert reg.get("write_file").manifest.action_severity == "write"


def test_web_fetch_stays_read_deliberately() -> None:
    reg = ToolRegistry.with_defaults()
    assert reg.get("web_fetch").manifest.action_severity == "read"
