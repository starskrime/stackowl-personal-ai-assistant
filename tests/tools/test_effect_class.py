"""TS1 — effect_class tagging on the durable-effect tools (ADR-T2).

A creation/delivery/schedule tool MUST declare its ``effect_class`` so the honesty
layer (TS3 overclaim gate) can demand a MEASURED ``verified==True`` before a success
of that class is allowed into the answer. A read-only tool stays ``None`` (the
default) so existing tools are byte-identical.
"""

from __future__ import annotations

from stackowl.tools.io.read_file import ReadFileTool
from stackowl.tools.knowledge.skill_manage import SkillManageTool
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.scheduling.cronjob import CronjobTool
from stackowl.tools.scheduling.send_file import SendFileTool
from stackowl.tools.scheduling.send_message import SendMessageTool


def test_creation_tools_create_persistent_entity() -> None:
    assert OwlBuildTool().manifest.effect_class == "creates_persistent_entity"
    assert SkillManageTool().manifest.effect_class == "creates_persistent_entity"


def test_cronjob_schedules() -> None:
    assert CronjobTool().manifest.effect_class == "schedules"


def test_send_tools_send_message() -> None:
    assert SendFileTool().manifest.effect_class == "sends_message"
    assert SendMessageTool().manifest.effect_class == "sends_message"


def test_read_only_tool_has_no_effect_class() -> None:
    """Back-compat (TS1 / (d)): a read-only tool keeps the default None — the
    ~92 un-migrated tools are unaffected."""
    assert ReadFileTool().manifest.effect_class is None
