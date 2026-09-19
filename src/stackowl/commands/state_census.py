"""Story 4.2 — the census of every slash command and sub-command's state-change
declaration, mirroring ``commands/manifest.py``'s ``SHIPPED_COMMANDS`` precedent.

WHY THIS EXISTS. Epic 4's future "one door" command gate (stories 4.3+) needs to
know, for every one of the platform's slash commands AND sub-commands, whether
invoking it changes state and — if so — the future command type(s) it will
eventually submit through that gate. Tools already carry this on
``ToolManifest`` (``tools/base.py``); commands had no equivalent mechanism at
all until this module.

TWO DICTS, mirroring the two levels a command tree actually has:

* ``COMMAND_CENSUS`` — one entry per TOP-LEVEL command (33 today, the same 33
  keys as ``commands/manifest.py::SHIPPED_COMMANDS``). Its severity covers the
  command's OWN bare-invocation behavior: for a "verb"-grammar command whose
  bare form is just a listing/menu (``/config``, ``/skill``, ``/owl``, ...) that
  is ``"read"``; for a "flag"/"leaf"-grammar command whose bare invocation IS
  the action (``/focus <mode>``, ``/bye``, ``/connect <service>``, ...) it is
  whatever that action does. Some flag-grammar commands are read when called
  with no operand and state-changing only with one (``/tier <name>``, ``/focus
  <mode>``, ``/connect <service>``) — this census, like ``ToolManifest``, has no
  concept of "sometimes"; it declares the WORST-CASE severity the bare
  invocation can reach, same principle as a tool whose severity covers its most
  dangerous call shape. The per-command Design Notes in spec-4-2 record each of
  these judgment calls.

* ``SUBCOMMAND_CENSUS`` — one entry per live sub-command PATH (91 today),
  dot-namespaced and recursively flattened (``"config.set"``,
  ``"browser.profile.delete"`` for a 2-level nested child). A parent node that
  itself has children (``browser.profile``, ``browser.watch``) is its own key
  too, distinct from its children, exactly as ``CommandMeta.subcommands``/
  ``SubCommand.children`` models it as a real tree node.

ONE SHARED NAMESPACE WITH TOOLS. A ``command_types`` string names the same
real-world action regardless of which surface (a tool or a command/sub-command)
will eventually submit it — see ``authz/state_change_census.py``'s module
docstring. Confirmed live examples of the SAME string appearing on both sides:

* ``"skill.delete"``      — tool ``skill_manage(action="delete")`` AND ``/skill rm``
* ``"skill.set_enabled"`` — tool ``skill_manage(action="enable"/"disable")`` AND
                             ``/skill enable``/``/skill disable``
* ``"browser.close_session"`` — tool ``browser_close`` AND ``/browser close``

GROUND TRUTH, NOT THE SPEC'S OWN PROSE COUNT. spec-4-2's frontmatter says "34
slash commands"; the REAL registry (``register_all_commands(CommandDeps())`` →
``CommandRegistry.instance().list()``) and ``commands/manifest.py::
SHIPPED_COMMANDS`` both give 33 — the frontmatter's count is off by one (it
likely double-counted the exempt "owls" class alongside its still-live "owl"
successor; measured 2026-09-19, see this story's Verification). This module and
its tripwires are built against the 33 the real registry actually returns,
since that is what ``test_every_command_declares_state_change.py`` diffs
against and what a future undeclared command must be caught by.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["read", "write", "consequential"]


@dataclass(frozen=True)
class CommandDeclaration:
    """One command's or sub-command's state-change declaration."""

    action_severity: Severity
    command_types: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# COMMAND_CENSUS — 33 top-level commands (== SHIPPED_COMMANDS).
# ---------------------------------------------------------------------------
COMMAND_CENSUS: dict[str, CommandDeclaration] = {
    "help": CommandDeclaration("read"),
    "find": CommandDeclaration("read"),
    "tools": CommandDeclaration("read"),
    "explain": CommandDeclaration("read"),
    "whoami": CommandDeclaration("read"),
    "why": CommandDeclaration("read"),
    "permissions": CommandDeclaration("read"),
    "style": CommandDeclaration("read"),
    "learn": CommandDeclaration("read"),
    # Starts a fresh conversation — a real state change (session.start_new).
    "new": CommandDeclaration("write", ("session.start_new",)),
    "config": CommandDeclaration("read"),
    "cost": CommandDeclaration("read"),
    "provider": CommandDeclaration("read"),
    # Bare `/tier <name>` sets the active tier preference; bare-empty is a
    # read-only menu. Worst-case severity declared, per this module's docstring.
    "tier": CommandDeclaration("write", ("tier.set_preference",)),
    "browser": CommandDeclaration("read"),
    "skill": CommandDeclaration("read"),
    "memory": CommandDeclaration("read"),
    "owl": CommandDeclaration("read"),
    # Bare `/focus <mode>` sets the routing mode; bare-no-arg is read.
    "focus": CommandDeclaration("write", ("focus.set_mode",)),
    "preferences": CommandDeclaration("read"),
    # Same delivery seam as the send_message tool (Story 4.8 scope, epics.md
    # names "brief" explicitly).
    "urgent": CommandDeclaration("consequential", ("notifications.broadcast_urgent",)),
    "quiet": CommandDeclaration("write", ("notifications.set_quiet_hours",)),
    "notifications": CommandDeclaration("read"),
    # Sets shutdown_event, stops the whole server (bye_command.py:45).
    "bye": CommandDeclaration("consequential", ("system.shutdown",)),
    # Own docstring: "the DESTRUCTIVE one".
    "reset": CommandDeclaration("consequential", ("session.clear_history",)),
    "audit": CommandDeclaration("read"),
    "brief": CommandDeclaration("consequential", ("notifications.deliver_brief",)),
    "parliament": CommandDeclaration("write", ("parliament.start_debate",)),
    "webhook": CommandDeclaration("read"),
    # Bare/no-service is read; a named service is a real, irreversible link.
    "connect": CommandDeclaration("consequential", ("integrations.link_account",)),
    "disconnect": CommandDeclaration("consequential", ("integrations.unlink_account",)),
    "plugins": CommandDeclaration("read"),
    # Only the autonomy step mutates; delegates to /config set.
    "onboarding": CommandDeclaration("write", ("config.set_autonomy_level",)),
}


# ---------------------------------------------------------------------------
# SUBCOMMAND_CENSUS — 91 live dotted sub-command paths, recursively flattened.
# ---------------------------------------------------------------------------
SUBCOMMAND_CENSUS: dict[str, CommandDeclaration] = {
    # ---------------------------------------------------------------- config
    "config.list": CommandDeclaration("read"),
    "config.get": CommandDeclaration("read"),
    "config.export": CommandDeclaration("read"),
    "config.set": CommandDeclaration("write", ("config.set_value",)),
    "config.reset": CommandDeclaration("write", ("config.reset_value",)),
    "config.detect-timezone": CommandDeclaration("write", ("config.set_value",)),
    # ------------------------------------------------------------------ cost
    "cost.turns": CommandDeclaration("read"),
    "cost.privacy": CommandDeclaration("consequential", ("cost.wipe_history",)),
    # -------------------------------------------------------------- provider
    "provider.list": CommandDeclaration("read"),
    "provider.status": CommandDeclaration("read"),
    "provider.models": CommandDeclaration("read"),
    "provider.add": CommandDeclaration("write", ("provider.add",)),
    # Real, irreversible delete with no confirmation gate today — a pre-existing
    # gap this story only DECLARES (see spec-4-2 Design Notes / deferred-work.md).
    "provider.remove": CommandDeclaration("consequential", ("provider.remove",)),
    "provider.set-tier": CommandDeclaration("write", ("provider.set_tier",)),
    "provider.edit": CommandDeclaration("write", ("provider.edit_field",)),
    "provider.enable": CommandDeclaration("write", ("provider.set_enabled",)),
    "provider.disable": CommandDeclaration("write", ("provider.set_enabled",)),
    "provider.set-token": CommandDeclaration("write", ("provider.set_token",)),
    "provider.rename": CommandDeclaration("write", ("provider.rename",)),
    "provider.model-add": CommandDeclaration("write", ("provider.add_model",)),
    "provider.remove-model": CommandDeclaration("write", ("provider.remove_model",)),
    "provider.set-model-tokens": CommandDeclaration("write", ("provider.set_model_field",)),
    "provider.set-model-context": CommandDeclaration("write", ("provider.set_model_field",)),
    # ------------------------------------------------------------------ tier
    "tier.list": CommandDeclaration("read"),
    "tier.menu": CommandDeclaration("read"),
    "tier.add": CommandDeclaration("write", ("tier.add_provider",)),
    "tier.remove": CommandDeclaration("write", ("tier.remove_provider",)),
    # --------------------------------------------------------------- browser
    "browser.help": CommandDeclaration("read"),
    "browser.settings": CommandDeclaration("read"),
    "browser.sessions": CommandDeclaration("read"),
    "browser.close": CommandDeclaration("write", ("browser.close_session",)),
    "browser.fetch-binary": CommandDeclaration("write", ("browser.fetch_binary",)),
    "browser.profile": CommandDeclaration("read"),
    "browser.profile.list": CommandDeclaration("read"),
    "browser.profile.delete": CommandDeclaration("write", ("browser.delete_profile",)),
    "browser.watch": CommandDeclaration("read"),
    "browser.watch.list": CommandDeclaration("read"),
    # ----------------------------------------------------------------- skill
    "skill.use": CommandDeclaration("read"),
    "skill.list": CommandDeclaration("read"),
    "skill.show": CommandDeclaration("read"),
    "skill.edit": CommandDeclaration("read"),
    "skill.diff": CommandDeclaration("read"),
    "skill.menu": CommandDeclaration("read"),
    "skill.add": CommandDeclaration("write", ("skill.install",)),
    "skill.rm": CommandDeclaration("consequential", ("skill.delete",)),
    "skill.enable": CommandDeclaration("write", ("skill.set_enabled",)),
    "skill.disable": CommandDeclaration("write", ("skill.set_enabled",)),
    "skill.reload": CommandDeclaration("write", ("skill.reload_index",)),
    "skill.pin": CommandDeclaration("write", ("skill.set_pinned",)),
    "skill.unpin": CommandDeclaration("write", ("skill.set_pinned",)),
    "skill.dedupe": CommandDeclaration("consequential", ("skill.dedupe",)),
    "skill.migrate": CommandDeclaration("write", ("skill.migrate_standard",)),
    "skill.restore": CommandDeclaration("write", ("skill.restore_version",)),
    # ---------------------------------------------------------------- memory
    "memory.stats": CommandDeclaration("read"),
    "memory.search": CommandDeclaration("read"),
    "memory.budget": CommandDeclaration("read"),
    "memory.export": CommandDeclaration("read"),
    "memory.remember": CommandDeclaration("write", ("memory.add_entry",)),
    "memory.forget": CommandDeclaration("write", ("memory.remove_entry",)),
    # ------------------------------------------------------------------- owl
    "owl.list": CommandDeclaration("read"),
    "owl.dna": CommandDeclaration("read"),
    "owl.dna-dry-run": CommandDeclaration("read"),
    "owl.health": CommandDeclaration("read"),
    "owl.objectives": CommandDeclaration("read"),
    "owl.objective": CommandDeclaration("read"),
    "owl.create": CommandDeclaration("consequential", ("owl.create",)),
    # Stays a single `write` entry even though it can carry authority-widening
    # fields (--tools/--capability_profile) — Story 4.9's classification-time
    # call once it builds the real owl commands (deferred-work.md flag).
    "owl.edit": CommandDeclaration("write", ("owl.edit",)),
    "owl.rename": CommandDeclaration("write", ("owl.rename",)),
    "owl.retire": CommandDeclaration("consequential", ("owl.retire",)),
    # Bucketed to 4.7 (scheduling), not 4.9 (owl authority) — Story 4.7's AC
    # text explicitly covers "any slash command that changes schedules", and
    # these two literally suspend/resume the owl's cron cadence.
    "owl.pause": CommandDeclaration("write", ("scheduling.pause_owl_job",)),
    "owl.resume": CommandDeclaration("write", ("scheduling.resume_owl_job",)),
    "owl.reset-dna": CommandDeclaration("consequential", ("owl.reset_dna",)),
    "owl.dna-restore": CommandDeclaration("consequential", ("owl.dna_restore",)),
    "owl.objective-cancel": CommandDeclaration("consequential", ("owl.cancel_objective",)),
    "owl.objective-merge": CommandDeclaration("consequential", ("owl.merge_objective",)),
    # ------------------------------------------------------------- notifications
    "notifications.missed": CommandDeclaration("read"),
    # ------------------------------------------------------------ preferences
    "preferences.list": CommandDeclaration("read"),
    "preferences.remove": CommandDeclaration("write", ("preferences.remove_note",)),
    # ------------------------------------------------------------------ audit
    "audit.export": CommandDeclaration("write", ("audit.export_log",)),
    # ------------------------------------------------------------- parliament
    "parliament.log": CommandDeclaration("read"),
    "parliament.push": CommandDeclaration("write", ("parliament.push_interjection",)),
    "parliament.expand": CommandDeclaration("write", ("parliament.expand_claim",)),
    "parliament.unsuppress": CommandDeclaration("write", ("parliament.unsuppress",)),
    # ---------------------------------------------------------------- webhook
    "webhook.list": CommandDeclaration("read"),
    "webhook.register": CommandDeclaration("consequential", ("webhook.register_source",)),
    "webhook.enable": CommandDeclaration("write", ("webhook.set_enabled",)),
    "webhook.disable": CommandDeclaration("write", ("webhook.set_enabled",)),
    # ---------------------------------------------------------------- plugins
    "plugins.list": CommandDeclaration("read"),
    "plugins.info": CommandDeclaration("read"),
    "plugins.enable": CommandDeclaration("write", ("plugins.set_enabled",)),
    "plugins.disable": CommandDeclaration("write", ("plugins.set_enabled",)),
}
