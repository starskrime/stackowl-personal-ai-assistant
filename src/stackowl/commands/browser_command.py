"""BrowserCommand — /browser slash command (sessions, profiles, watch, settings)."""

from __future__ import annotations

import shutil
from pathlib import Path

from stackowl.commands.base import SlashCommand
from stackowl.commands.registry import register_command
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState


def _owner_key_for_session(state: PipelineState) -> str:
    """Resolve the owner_key for this conversation. Mirrors tools.py logic."""
    if state.channel == "telegram" and state.session_id:
        return f"telegram:{state.session_id}"
    return "local"


def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h{int((seconds % 3600) // 60)}m"


class BrowserCommand(SlashCommand):
    @property
    def command(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Browser admin: sessions, close, settings, fetch-binary, "
            "profile list/delete, watch add/list/remove."
        )

    async def handle(self, args: str, state: PipelineState) -> str:
        log.gateway.debug(
            "[commands] browser.handle: entry",
            extra={"_fields": {"session": state.session_id, "args_len": len(args)}},
        )
        services = get_services()
        runtime = services.browser_runtime
        sessions = services.browser_sessions
        parts = args.strip().split()
        sub = parts[0] if parts else "help"

        if sub == "help" or not parts:
            return self._help_text()
        if sub == "settings":
            return self._settings_text(runtime)
        if sub == "sessions":
            return await self._sessions_text(sessions, _owner_key_for_session(state))
        if sub == "close":
            return await self._close(sessions, parts[1:], _owner_key_for_session(state))
        if sub == "fetch-binary":
            return await self._fetch_binary()
        if sub == "profile":
            return self._profile_subcmd(runtime, parts[1:], _owner_key_for_session(state))
        if sub == "watch":
            return self._watch_subcmd(parts[1:])
        return f"Unknown subcommand: '{sub}'. Try /browser help."

    def _help_text(self) -> str:
        return (
            "/browser subcommands:\n"
            "  sessions               — list active browser sessions for this conversation\n"
            "  close <id> | close all — release one session or all of yours\n"
            "  settings               — show current BrowserSettings\n"
            "  fetch-binary           — re-run `python -m camoufox fetch`\n"
            "  profile list           — list persistent profiles for this conversation\n"
            "  profile delete <name>  — remove a profile dir\n"
            "  watch list             — (read-only) hint to register a website-watch job\n"
        )

    def _settings_text(self, runtime: object | None) -> str:
        if runtime is None:
            return "Browser runtime not started."
        s = runtime.settings  # type: ignore[attr-defined]
        lines = [
            "Browser settings:",
            f"  headless_mode:                  {s.headless_mode}",
            f"  humanize:                       {s.humanize}",
            f"  block_images:                   {s.block_images}",
            f"  block_webrtc:                   {s.block_webrtc}",
            f"  geoip:                          {s.geoip}",
            f"  nav_recycle_threshold:          {s.nav_recycle_threshold}",
            f"  idle_recycle_minutes:           {s.idle_recycle_minutes}",
            f"  session_idle_timeout_minutes:   {s.session_idle_timeout_minutes}",
            f"  max_concurrent_sessions:        {s.max_concurrent_sessions}",
            f"  max_concurrent_pages_per_sess:  {s.max_concurrent_pages_per_session}",
            f"  profiles_dir:                   {s.profiles_dir}",
            f"  screenshots_dir:                {s.screenshots_dir}",
            f"  available:                      {getattr(runtime, 'available', False)}",
        ]
        return "\n".join(lines)

    async def _sessions_text(self, sessions: object | None, owner_key: str) -> str:
        if sessions is None:
            return "Browser sessions registry unavailable."
        infos = await sessions.list_for_owner(owner_key)  # type: ignore[attr-defined]
        if not infos:
            return f"No active browser sessions for {owner_key}."
        lines = [f"Active sessions for {owner_key}:"]
        for info in infos:
            profile = info.profile_name or "(incognito)"
            last_url = info.last_url_path or "—"
            lines.append(
                f"  {info.session_id[:12]}  profile={profile:<16}  "
                f"pages={info.page_count}  age={_fmt_age(info.age_seconds)}  last={last_url}"
            )
        return "\n".join(lines)

    async def _close(self, sessions: object | None, args: list[str], owner_key: str) -> str:
        if sessions is None:
            return "Browser sessions registry unavailable."
        if not args:
            return "Usage: /browser close <session_id_prefix> | /browser close all"
        target = args[0]
        if target == "all":
            count = await sessions.close_all_for_owner(owner_key)  # type: ignore[attr-defined]
            return f"Closed {count} session(s) for {owner_key}."
        # Match by prefix.
        infos = await sessions.list_for_owner(owner_key)  # type: ignore[attr-defined]
        matches = [i for i in infos if i.session_id.startswith(target)]
        if not matches:
            return f"No session matches prefix '{target}' for {owner_key}."
        if len(matches) > 1:
            return f"Prefix '{target}' is ambiguous ({len(matches)} matches). Be more specific."
        await sessions.close(matches[0].session_id)  # type: ignore[attr-defined]
        return f"Closed session {matches[0].session_id[:12]}."

    async def _fetch_binary(self) -> str:
        from stackowl.startup.browser_probe import BrowserProbe

        result = await BrowserProbe().check(fetch_if_missing=True)
        if result.binary_ok:
            return f"Camoufox binary ready at {result.binary_path}."
        return f"Binary fetch failed: {result.error}"

    def _profile_subcmd(self, runtime: object | None, args: list[str], owner_key: str) -> str:
        if runtime is None:
            return "Browser runtime not started — cannot list profiles."
        profiles_dir: Path = runtime.settings.profiles_dir  # type: ignore[attr-defined]
        owner_dir = profiles_dir / owner_key.replace(":", "_").replace("/", "_")
        sub = args[0] if args else "list"
        if sub == "list":
            if not owner_dir.exists():
                return f"No profiles for {owner_key}."
            entries = sorted(p.name for p in owner_dir.iterdir() if p.is_dir())
            if not entries:
                return f"No profiles for {owner_key}."
            return f"Profiles for {owner_key}:\n  " + "\n  ".join(entries)
        if sub == "delete" and len(args) >= 2:
            raw_name = args[1]
            # Path-traversal guard: a name with '..' or a path separator could
            # escape owner_dir and rmtree something outside it (e.g. '..' →
            # the parent profiles_dir). Reject before touching the filesystem.
            if ".." in raw_name or "/" in raw_name or "\\" in raw_name:
                log.gateway.warning(
                    "[commands] browser.profile_delete: rejected unsafe profile name",
                    extra={"_fields": {"name": raw_name}},
                )
                return f"✗ Invalid profile name '{raw_name}'."
            target = raw_name.replace(":", "_")
            target_dir = owner_dir / target
            if not target_dir.exists():
                return f"Profile '{args[1]}' not found for {owner_key}."
            try:
                shutil.rmtree(target_dir)
            except OSError as exc:
                log.gateway.error(
                    "[commands] browser.profile_delete: rmtree failed",
                    exc_info=exc,
                    extra={"_fields": {"target_dir": str(target_dir)}},
                )
                return f"✗ Failed to delete profile '{args[1]}': {exc}"
            if target_dir.exists():
                log.gateway.error(
                    "[commands] browser.profile_delete: dir still present after rmtree",
                    extra={"_fields": {"target_dir": str(target_dir)}},
                )
                return f"✗ Profile '{args[1]}' could not be removed (directory still present)."
            log.gateway.info(
                "[commands] browser.profile_delete: deleted",
                extra={"_fields": {"profile": args[1], "owner_key": owner_key}},
            )
            return f"Deleted profile '{args[1]}' for {owner_key}."
        return "Usage: /browser profile list | /browser profile delete <name>"

    def _watch_subcmd(self, args: list[str]) -> str:
        sub = args[0] if args else "list"
        if sub == "list":
            return (
                "Website watches are persisted as scheduler jobs. "
                "Ask an owl to 'watch <url> daily' to register one. "
                "Use /agent list to see scheduler activity."
            )
        return "Usage: /browser watch list (use natural language to add/remove watches)"


_CMD = register_command(BrowserCommand())
