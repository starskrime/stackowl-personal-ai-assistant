"""Shell consent model — run-all-silently + catastrophic-only consent.

The shell tool drops its hardcoded allowlist: ANY command runs silently. Only a
narrow set of truly catastrophic, system-destroying command shapes
(``rm -rf /``, ``dd of=/dev/...``, ``mkfs``/``shred``/``wipefs`` on devices,
recursive chmod/chown on system roots, fork bombs) require user approval via the
existing consent gate. When no interactive user is present, a catastrophic
command fails closed (deny); it is NEVER auto-refused otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.consent import ConsentPolicy
from stackowl.tools.registry import ConsequentialActionGate
from stackowl.tools.system.shell import ShellTool, is_catastrophic

# =========================================================================== #
# 1. is_catastrophic — conservative detector
# =========================================================================== #


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "rm -fr /",
        "rm -rf ~",
        "rm -rf ~/",
        "rm -rf $HOME",
        "rm -rf /home",
        "rm -rf /home/*",
        "rm -rf /etc",
        "rm -rf /usr",
        "rm -rf /bin",
        "rm -rf /var",
        "rm -rf /boot",
        "rm -rf /lib",
        "rm -rf /lib64",
        "rm -rf /root",
        "rm --recursive --force /",
        "rm -r --force /etc",
        "dd if=/dev/zero of=/dev/sda",
        "dd of=/dev/sdb bs=1M",
        "mkfs /dev/sda1",
        "mkfs.ext4 /dev/sdb",
        "wipefs -a /dev/sda",
        "shred /dev/sda",
        "chmod -R 777 /",
        "chmod --recursive 000 /etc",
        "chown -R root /usr",
        ":(){",
        # --- F156 bypass corpus: trailing-slash / subpath / dot-segment must NOT evade ---
        "rm -rf /etc/",
        "rm -rf /usr/lib",
        "rm -rf /home/boss",
        "rm -rf /home/boss/",
        "rm -rf /etc/../etc",
        "rm -rf /var/log",
        "rm -rf /usr/local/bin",
        "rm -rf /etc/.",
        "chmod -R 777 /usr/bin",
        "chmod -R 777 /etc/",
        "chown -R root /var/log",
        # --- F156 residual: leading double-slash must NOT evade (normpath preserves "//") ---
        "rm -rf //etc",
        "rm -rf //usr",
        "rm -rf //home/boss",
        "rm -rf //root",
        "rm -rf //boot",
        "chmod -R 777 //etc",
        # triple-slash sanity (normpath already collapses ///etc → /etc)
        "rm -rf ///etc",
    ],
)
def test_catastrophic_commands_flagged(command: str) -> None:
    import shlex

    flagged, reason = is_catastrophic(shlex.split(command))
    assert flagged is True, command
    assert reason  # a human-readable reason is always provided


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls -la /etc",
        "git status",
        "rm -rf ./build",
        "rm -rf build",
        "rm -rf node_modules",
        "rm file.txt",
        "rm -rf /tmp/scratch-dir",
        "pip install yt-dlp",
        "yt-dlp https://x.com/a-b.mp4 -o /tmp/f.mp4",
        "python -m yt_dlp",
        "ffmpeg -i in.mp4 out.mp4",
        "curl https://example.com -o /tmp/x",
        "echo hello > /tmp/note.txt",
        "dd if=in.iso of=/tmp/out.iso",
        "chmod 644 file.txt",
        "chmod -R 755 ./mydir",
        "chown user:user ./file",
        "mkfs --help",
        # --- F156 must NOT over-flag legitimate subpaths / trailing-slash / relative ---
        "rm -rf /tmp/x/",
        "rm -rf ./build/",
        "rm -rf /tmp/scratch/sub",
        "chmod -R 755 /tmp/mydir/",
    ],
)
def test_benign_commands_not_flagged(command: str) -> None:
    import shlex

    flagged, _reason = is_catastrophic(shlex.split(command))
    assert flagged is False, command


# =========================================================================== #
# 2. Shell behavior — previously-blocked commands now run; no consent prompt
# =========================================================================== #


class _FakeProc:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return (b"ok", b"")


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch create_subprocess_exec; record the args it was invoked with."""
    captured: dict[str, Any] = {"called": False, "args": None}

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["called"] = True
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr(
        "stackowl.tools.system.shell.asyncio.create_subprocess_exec", _fake_exec
    )
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["yt-dlp --version", "ffmpeg -version", "curl --help"])
async def test_previously_blocked_command_now_runs(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    captured = _patch_subprocess(monkeypatch)

    result = await ShellTool().execute(command=command)

    assert captured["called"] is True
    assert result.success is True
    assert "not allowed" not in (result.error or "").lower()


# =========================================================================== #
# 3. Catastrophic + consent gate
# =========================================================================== #


class _StubPrompter:
    def __init__(self, allow: bool) -> None:
        self._allow = allow
        self.prompted = False

    async def prompt(self, req: Any) -> Any:
        from stackowl.tools.consent import ConsentScope

        self.prompted = True
        return ConsentScope.ONCE if self._allow else ConsentScope.DENY


def _services_with_gate(allow: bool) -> tuple[StepServices, _StubPrompter]:
    prompter = _StubPrompter(allow)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=prompter))
    return StepServices(consent_gate=gate), prompter


@pytest.mark.asyncio
async def test_catastrophic_denied_never_spawns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    captured = _patch_subprocess(monkeypatch)
    services, prompter = _services_with_gate(allow=False)

    token = set_services(services)
    trace = TraceContext.start(session_id="123", interactive=True, channel="telegram")
    try:
        result = await ShellTool().execute(command="rm -rf /")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert prompter.prompted is True
    assert captured["called"] is False  # never spawned
    assert result.success is False
    assert "declined" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_catastrophic_approved_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    captured = _patch_subprocess(monkeypatch)
    services, prompter = _services_with_gate(allow=True)

    token = set_services(services)
    trace = TraceContext.start(session_id="123", interactive=True, channel="telegram")
    try:
        result = await ShellTool().execute(command="rm -rf /etc")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert prompter.prompted is True
    assert captured["called"] is True  # approved → ran
    assert result.success is True


@pytest.mark.asyncio
async def test_catastrophic_non_interactive_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    captured = _patch_subprocess(monkeypatch)
    services, prompter = _services_with_gate(allow=True)  # gate WOULD allow

    token = set_services(services)
    trace = TraceContext.start(session_id="123", interactive=False, channel="telegram")
    try:
        result = await ShellTool().execute(command="rm -rf /")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert prompter.prompted is False  # never even attempted to prompt
    assert captured["called"] is False  # never spawned
    assert result.success is False
    assert "no user" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_catastrophic_no_gate_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    captured = _patch_subprocess(monkeypatch)

    token = set_services(StepServices(consent_gate=None))
    trace = TraceContext.start(session_id="123", interactive=True, channel="telegram")
    try:
        result = await ShellTool().execute(command="rm -rf /")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert captured["called"] is False
    assert result.success is False


@pytest.mark.asyncio
async def test_benign_command_skips_gate_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    captured = _patch_subprocess(monkeypatch)
    services, prompter = _services_with_gate(allow=False)  # would deny IF consulted

    token = set_services(services)
    trace = TraceContext.start(session_id="123", interactive=True, channel="telegram")
    try:
        result = await ShellTool().execute(command="rm -rf ./build")
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert prompter.prompted is False  # benign → gate never consulted
    assert captured["called"] is True
    assert result.success is True


# =========================================================================== #
# 4. Consent delivery (Part 1) — special chars don't break the send
# =========================================================================== #


@pytest.mark.asyncio
async def test_consent_prompt_sent_as_plain_text() -> None:
    """A command full of markdown-special chars must deliver as plain text
    (parse_mode=None) so Telegram cannot 400 on entity parsing."""
    from stackowl.channels.telegram.consent import TelegramConsentPrompter
    from stackowl.tools.consent import ConsentRequest

    captured: dict[str, Any] = {}

    class _Adapter:
        async def send_inline_keyboard(
            self,
            text: str,
            keyboard: dict,
            chat_id: int | None = None,
            parse_mode: str | None = "MarkdownV2",
        ) -> None:
            captured["text"] = text
            captured["parse_mode"] = parse_mode

    prompter = TelegramConsentPrompter(_Adapter(), timeout_seconds=0.02)
    req = ConsentRequest(
        tool_name="shell",
        channel="telegram",
        session_id="424242",
        summary="Run shell command: yt-dlp https://x.com/a-b.mp4 -o /tmp/f.mp4",
    )
    await prompter.prompt(req)  # times out to deny, but the send was attempted

    assert captured["parse_mode"] is None  # plain text — cannot 400 on entities
    assert "yt-dlp" in captured["text"]


# =========================================================================== #
# 5. Subprocess CWD defaults to the workspace (H2) — files a command writes by
#    relative name land where send_file/write_file expect them.
# =========================================================================== #


@pytest.mark.asyncio
async def test_no_workdir_defaults_cwd_to_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no workdir, a command that writes a file by relative name lands in
    the workspace, and its observed CWD IS the workspace (real subprocess)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    ws = tmp_path / "ws"
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))

    # Benign command → runs without consent. Writes its own CWD into marker.txt.
    code = "import os; open('marker.txt', 'w').write(os.getcwd())"
    result = await ShellTool().execute(command=f"{sys.executable} -c {code!r}")

    assert result.success is True, result.error
    marker = ws / "marker.txt"
    assert marker.exists()  # file landed IN the workspace (mkdir self-healed)
    assert Path(marker.read_text()).resolve() == ws.resolve()  # CWD was the workspace


@pytest.mark.asyncio
async def test_explicit_workdir_overrides_workspace_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit workdir still wins over the workspace default."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))

    code = "import os; open('marker.txt', 'w').write(os.getcwd())"
    result = await ShellTool().execute(
        command=f"{sys.executable} -c {code!r}", workdir=str(explicit)
    )

    assert result.success is True, result.error
    assert (explicit / "marker.txt").exists()  # landed in the explicit dir
    assert not (ws / "marker.txt").exists()  # NOT the workspace
    assert Path((explicit / "marker.txt").read_text()).resolve() == explicit.resolve()
