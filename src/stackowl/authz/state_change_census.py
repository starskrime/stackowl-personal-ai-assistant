"""Story 4.2 — the shared not-yet-migrated ledger for every declared command type.

Epic 4's future "one door" gate (stories 4.3+) needs to know, for every real-world
action the platform can take, whether a live surface (a tool or a slash command)
has DECLARED it yet, and which future migration story owns moving it onto the
real command-dispatch path. This module is that ledger.

ONE SHARED NAMESPACE. A command-type string (e.g. ``"skill.delete"``) means the
same real action regardless of WHICH surface names it — ``tools/knowledge/
skill_manage.py``'s ``ToolManifest.command_types`` and ``commands/state_census.py``'s
``SUBCOMMAND_CENSUS["skill.rm"]`` both point at this one key. That is deliberate:
``/skill rm`` and ``skill_manage(action="delete")`` will eventually submit the
SAME command, so they must not mint two different names for it (see
``commands/state_census.py``'s module docstring for the full list of shared keys).

WHY A DICT AND NOT A DERIVED SET (mirrors ``journal/coverage.py::UNJOURNALED_TABLES``,
the same shape for the same reason). This dict is NOT computed from the live
tool/command trees — it is authored, one entry per command-type string, naming the
story that will eventually migrate it and today's status. A command type that
silently stopped being declared would silently vanish from a derived set with no
trace; kept here, deleting the entry is a decision a reviewer makes on the PR that
retires the underlying tool/command, not something that happens by omission.

``status`` exists so a future story (4.3+) FLIPS an entry to ``"migrated"`` as it
burns this list down, rather than deleting it — deleting it here would either (a)
make the closure tripwire in ``tests/authz/test_state_change_census_has_no_stale_
entries.py`` immediately fail (the tool/command still declares the type) or (b)
require deleting the tool/command's own declaration in lockstep, which is scope
those future stories own, not this one. Story 4.2 shipped with nothing
``"migrated"`` (it only declares, per its own Boundaries); Story 4.3 is the
first to flip entries — its pilot pair, ``scheduling.pause_job``/
``scheduling.resume_job`` — once ``cronjob.py``'s pause/resume actually ran
through ``commands/spec/submit.py::submit_command``.

Cross-referenced by two independent tripwires (deliberately, so a story error in
either direction is caught):
  * ``tests/tools/test_every_tool_declares_state_change.py`` and
    ``tests/commands/test_every_command_declares_state_change.py`` — every
    declared command type has an entry HERE (forward direction: nothing
    undeclared).
  * ``tests/authz/test_state_change_census_has_no_stale_entries.py`` — every key
    HERE is declared by at least one live tool or command (reverse direction:
    no stale/orphaned ledger entries — neither package-local test can make this
    check alone since it needs BOTH trees).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

#: The five migration stories that can own a command type, per epic-4-context.
#: 4.3 is the cronjob pause/resume pilot ONLY; 4.7-4.10 are the follow-on waves
#: that migrate everything else (scheduling, messaging, owl/skill authority, and
#: the long tail, respectively).
MigrationStory = Literal["4.3", "4.7", "4.8", "4.9", "4.10"]
MIGRATION_STORIES: Final[frozenset[str]] = frozenset({"4.3", "4.7", "4.8", "4.9", "4.10"})


@dataclass(frozen=True)
class CommandTypeMigration:
    """One ledger entry: who owns migrating this command type, and today's status."""

    story: MigrationStory
    status: Literal["pending", "migrated"] = "pending"
    reason: str = ""


# ---------------------------------------------------------------------------
# Shared reason strings — bucketed by story, mirroring journal/coverage.py's
# single reviewable reason per class rather than N hand-typed sentences that
# would all say the same thing.
# ---------------------------------------------------------------------------
_REASON_4_3_MIGRATED = (
    "Story 4.3 migrated this: cronjob's pause/resume actions now call "
    "commands/spec/submit.py::submit_command, which runs a COMMAND task "
    "through scheduler/commands.py's registered CommandSpec + handler — "
    "JobScheduler.pause/.resume are called only from that handler, never "
    "directly by cronjob.py any more."
)
_REASON_4_7_MIGRATED = (
    "Story 4.7 migrated this: cronjob's create/update/remove/run, "
    "owl_schedule's pause/resume/snooze, and objective_tool's creation now "
    "all call commands/spec/submit.py::submit_command instead of a "
    "JobScheduler/ObjectiveStore mutator directly — scheduler/commands.py "
    "(create_job/edit_job/delete_job/run_now_job/pause_owl_job/"
    "resume_owl_job/set_owl_schedule) and objectives/commands.py "
    "(set_objective) hold the registered CommandSpec + handler pairs."
)
_REASON_4_8_MIGRATED = (
    "Story 4.8 migrated this: send_message/send_file, /urgent's broadcast, "
    "and /brief + the scheduled morning-brief job now all call "
    "commands/spec/submit.py::submit_command instead of "
    "ProactiveDeliverer.deliver/.transport or ProactiveJobDeliverer."
    "deliver_for_job directly — notifications/commands.py (messaging."
    "send_message/send_file, notifications.broadcast_urgent/deliver_brief) "
    "holds the registered CommandSpec + handler pairs."
)
_REASON_4_9_MIGRATED = (
    "Story 4.9 migrated this: owl_build/tool_build/skill_manage/synthesize_skills "
    "and their /owl + /skill slash-command equivalents now all call "
    "commands/spec/submit.py::submit_command instead of persist_owl/registry."
    "replace/delete_owl/ToolRegistry.register/.unregister/record_skill_mutation/"
    "SkillIndexStore.delete/.set_enabled/.set_pinned directly — "
    "owl_build_commands.py (owls.build.create/edit/rename/retire/restore/grant), "
    "owls_dna_commands.py (owl.reset_dna/dna_restore/cancel_objective/"
    "merge_objective), tool_build_commands.py (owls.build_tool.create/delete) "
    "and skill_commands.py (the 12 skill.* types) hold the registered "
    "CommandSpec + handler pairs. owl.create/edit/rename/retire CONSOLIDATED "
    "onto owls.build.create/edit/rename/retire (see this module's own "
    "docstring: 'ONE SHARED NAMESPACE') — their old keys are deleted below,"
    "never left status='pending'."
)
_REASON_4_10 = (
    "Story 4.10's wave — the long tail: everything write/consequential this "
    "story (4.2) found that does not belong to 4.3/4.7/4.8/4.9's named scope."
)

#: Every command-type string declared anywhere live (tools' ``ToolManifest.
#: command_types`` union commands'/subcommands' ``CommandDeclaration.
#: command_types``), mapped to its owning migration story. Nothing is
#: ``"migrated"`` yet (Story 4.2 boundary: declare, never migrate).
COMMAND_TYPE_MIGRATIONS: dict[str, CommandTypeMigration] = {
    # ------------------------------------------------------------------ 4.3
    "scheduling.pause_job": CommandTypeMigration(
        "4.3", status="migrated", reason=_REASON_4_3_MIGRATED,
    ),
    "scheduling.resume_job": CommandTypeMigration(
        "4.3", status="migrated", reason=_REASON_4_3_MIGRATED,
    ),
    # ------------------------------------------------------------------ 4.7
    "scheduling.create_job": CommandTypeMigration(
        "4.7", status="migrated", reason=_REASON_4_7_MIGRATED,
    ),
    "scheduling.edit_job": CommandTypeMigration(
        "4.7", status="migrated", reason=_REASON_4_7_MIGRATED,
    ),
    "scheduling.delete_job": CommandTypeMigration(
        "4.7", status="migrated", reason=_REASON_4_7_MIGRATED,
    ),
    "scheduling.run_now_job": CommandTypeMigration(
        "4.7", status="migrated", reason=_REASON_4_7_MIGRATED,
    ),
    "scheduling.set_objective": CommandTypeMigration(
        "4.7", status="migrated", reason=_REASON_4_7_MIGRATED,
    ),
    "scheduling.set_owl_schedule": CommandTypeMigration(
        "4.7", status="migrated", reason=_REASON_4_7_MIGRATED,
    ),
    "scheduling.pause_owl_job": CommandTypeMigration(
        "4.7", status="migrated", reason=_REASON_4_7_MIGRATED,
    ),
    "scheduling.resume_owl_job": CommandTypeMigration(
        "4.7", status="migrated", reason=_REASON_4_7_MIGRATED,
    ),
    # ------------------------------------------------------------------ 4.8
    "messaging.send_file": CommandTypeMigration(
        "4.8", status="migrated", reason=_REASON_4_8_MIGRATED,
    ),
    "messaging.send_message": CommandTypeMigration(
        "4.8", status="migrated", reason=_REASON_4_8_MIGRATED,
    ),
    "notifications.broadcast_urgent": CommandTypeMigration(
        "4.8", status="migrated", reason=_REASON_4_8_MIGRATED,
    ),
    "notifications.deliver_brief": CommandTypeMigration(
        "4.8", status="migrated", reason=_REASON_4_8_MIGRATED,
    ),
    # ------------------------------------------------------------------ 4.9
    # owls.build / owls.build_tool / owl.create / owl.edit / owl.rename /
    # owl.retire are DELETED here (never left status="pending") — Story 4.9
    # consolidated create/edit/rename/retire onto the new owls.build.* types
    # below (declaration retired in lockstep, per this module's own
    # docstring: "deleting the entry is a decision a reviewer makes on the
    # PR that retires the underlying tool/command").
    "owls.build.create": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owls.build.edit": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owls.build.rename": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owls.build.retire": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owls.build.restore": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owls.build.grant": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owls.build_tool.create": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owls.build_tool.delete": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.author_create": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.author_edit": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.author_patch": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.delete": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.set_enabled": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.synthesize": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.install": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.reload_index": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.set_pinned": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.dedupe": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.migrate_standard": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "skill.restore_version": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owl.reset_dna": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owl.dna_restore": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owl.cancel_objective": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    "owl.merge_objective": CommandTypeMigration(
        "4.9", status="migrated", reason=_REASON_4_9_MIGRATED,
    ),
    # ------------------------------------------------------------------ 4.10
    "files.apply_patch": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "consent.batch_approve": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.browse": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.click": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.close_session": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.clear_cookies": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.set_cookie": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.handle_dialog": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.download_file": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.eval_js": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.scroll": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.close_tab": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.type_text": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.upload_file": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.fetch_binary": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "browser.delete_profile": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "dev.claude_code_run": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "dev.git_command": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "dev.run_tests": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "agents.delegate_task": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "agents.session_send": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "agents.session_spawn": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "files.edit": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "files.undo_write": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "files.write": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "code.execute": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "memory.record_reflection": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "memory.add_entry": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "memory.remove_entry": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "process.manage": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "preferences.set_output_format": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "preferences.remove_note": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "system.shell_exec": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "system.shutdown": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "session.start_new": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "session.clear_history": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "tier.set_preference": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "tier.add_provider": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "tier.remove_provider": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "focus.set_mode": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "notifications.set_quiet_hours": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "parliament.start_debate": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "parliament.push_interjection": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "parliament.expand_claim": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "parliament.unsuppress": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "integrations.link_account": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "integrations.unlink_account": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "audit.export_log": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "config.set_value": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "config.reset_value": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "config.set_autonomy_level": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "cost.wipe_history": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.add": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.remove": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.set_tier": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.edit_field": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.set_enabled": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.set_token": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.rename": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.add_model": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.remove_model": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "provider.set_model_field": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "webhook.register_source": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "webhook.set_enabled": CommandTypeMigration("4.10", reason=_REASON_4_10),
    "plugins.set_enabled": CommandTypeMigration("4.10", reason=_REASON_4_10),
}
