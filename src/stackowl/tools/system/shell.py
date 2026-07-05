"""ShellTool — runs shell commands via subprocess, never shell=True (ARCH-75).

Maximum-autonomy model: ANY command runs SILENTLY (install/network/write — no
prompt, no allowlist). Only a narrow set of truly catastrophic, system-destroying
command shapes (``rm -rf`` on a system/home root, ``dd``/``mkfs``/``shred``/
``wipefs`` on a block device, recursive chmod/chown on a system root, a classic
fork bomb) require explicit user approval via the consent gate. When no
interactive user is present to approve, a catastrophic command fails CLOSED
(deny) — it is never auto-refused otherwise. ``shell=False`` +
``create_subprocess_exec`` keep real injection safety (pipes/redirects/chaining
are inert).
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult

# Default per-command timeout. Raised from the old 30s so an agent-requested
# self-install / longer build / download can complete (Phase D). A per-call
# `timeout` arg overrides this, bounded to _TIMEOUT_CEILING_SEC.
_TIMEOUT_SEC = 120.0
# Hard ceiling: even an agent-requested timeout cannot exceed this, so a single
# command can never wedge the turn indefinitely.
_TIMEOUT_CEILING_SEC = 300.0


def _resolve_timeout(raw: object) -> float:
    """Resolve the effective per-call timeout: default if unset, else bounded.

    Returns ``_TIMEOUT_SEC`` when no timeout is requested; otherwise the requested
    value clamped to (0, _TIMEOUT_CEILING_SEC]. A non-numeric/invalid request falls
    back to the default rather than raising (no-hidden-errors: the command still runs).
    """
    if raw is None:
        return _TIMEOUT_SEC
    try:
        requested = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _TIMEOUT_SEC
    if requested <= 0:
        return _TIMEOUT_SEC
    return min(requested, _TIMEOUT_CEILING_SEC)

# System / home roots whose recursive deletion is catastrophic. A normal project
# path (``./build``, ``/tmp/scratch``, ``node_modules``) is NOT in here — the
# detector targets only well-known system-destroying shapes, never normal writes.
# NOTE (F156, documented cut): this denylist is POSIX-rooted. Windows roots
# (``C:\``, ``C:\Windows``, UNC ``\\host\share``) are out of scope for this slice;
# catastrophic-detection on Windows is a separate follow-up, not a silent gap.
_SYSTEM_ROOTS: frozenset[str] = frozenset(
    {
        "/",
        "/*",
        "~",
        "~/",
        "$HOME",
        "/home",
        "/home/*",
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/var",
        "/boot",
        "/root",
        "/lib",
        "/lib32",
        "/lib64",
        "/libx32",
        "/opt",
        "/sys",
        "/proc",
        "/dev",
    }
)


# Absolute, lexically-normalized forms of the protected roots, built once. These
# back the path-normalized ancestor/descendant predicate (F156). Non-absolute /
# shell-shape entries (``~``, ``$HOME``, ``/*``, ``/home/*``) are intentionally
# excluded here — they are covered by the literal-token membership predicate, since
# they do NOT survive normpath/expanduser as themselves on a CI box.
_ABSOLUTE_ROOTS: frozenset[str] = frozenset(
    os.path.normpath(r) for r in _SYSTEM_ROOTS if r.startswith("/") and "*" not in r
)


def _norm_target(op: str) -> str:
    """Return the lexical canonical form of a path operand (no FS I/O).

    Strips a trailing ``/`` and a trailing glob ``*`` (and any ``/`` it left),
    expands ``~`` and ``$VAR``, then collapses ``.``/``..``/duplicate separators
    via ``os.path.normpath``. Pure-lexical: NO ``Path.resolve()`` (would hit the
    filesystem / follow symlinks / can raise — this gate runs before EVERY spawn).
    Never raises (F156 invariant): any failure falls back to the raw operand.
    """
    try:
        stripped = op.rstrip("/").rstrip("*").rstrip("/") or "/"
        # Collapse a leading run of 2+ slashes to one BEFORE normpath. POSIX.1
        # §4.13 lets normpath PRESERVE exactly two leading slashes ("//etc" stays
        # "//etc"), which would evade the absolute-root predicates (F156 residual).
        stripped = re.sub(r"^/{2,}", "/", stripped)
        return os.path.normpath(os.path.expanduser(os.path.expandvars(stripped)))
    except (ValueError, TypeError):  # pragma: no cover — defensive, never raise
        return op


def _hits_system_root(op: str) -> bool:
    """True if an operand targets a protected system/home root (two predicates).

    (1) Literal-token membership — preserves un-resolvable shell shapes
        (``~``, ``~/``, ``$HOME``, ``/*``, ``/home/*``) that normalization would
        otherwise mangle, so the existing assertions keep flagging.
    (2) Path-normalized ancestor/descendant — a normalized absolute operand
        matches when it EQUALS a root, is a SUBPATH of a root (``/usr/lib`` under
        ``/usr``, ``/etc/`` after strip), or is itself an ANCESTOR of a root
        (``/`` ancestors everything). Relative paths (``./build``) never match.
    Conservative: any match blocks. Pure-lexical, never raises.
    """
    if op in _SYSTEM_ROOTS:  # predicate 1
        return True
    n = _norm_target(op)  # predicate 2
    if not os.path.isabs(n):
        return False
    if n == "/":  # bare filesystem root — only the literal "/" / "/*" is catastrophic
        return True
    for r in _ABSOLUTE_ROOTS:
        if r == "/":  # "/" is an ancestor of EVERYTHING — handled above; skip here
            continue  # so /tmp/x is not mis-flagged as a "subpath of /"
        if n == r:
            return True
        if n.startswith(r + "/"):  # operand is a subpath of a root (/usr/lib ⊂ /usr)
            return True
        if r.startswith(n + "/"):  # operand is an ancestor of a root (/home ⊃ /home/x)
            return True
    return False


def _is_recursive_force(flags: list[str]) -> bool:
    """True if a flag set requests both recursive AND force (rm -rf shapes)."""
    recursive = False
    force = False
    for tok in flags:
        if tok in ("--recursive",):
            recursive = True
        elif tok in ("--force",):
            force = True
        elif tok.startswith("--"):
            continue
        elif tok.startswith("-"):
            letters = tok[1:]
            if "r" in letters or "R" in letters:
                recursive = True
            if "f" in letters:
                force = True
    return recursive and force


def _is_recursive(flags: list[str]) -> bool:
    """True if a flag set requests recursion (chmod/chown -R shapes)."""
    for tok in flags:
        if tok == "--recursive":
            return True
        if tok.startswith("--"):
            continue
        if tok.startswith("-") and ("r" in tok[1:] or "R" in tok[1:]):
            return True
    return False


def _default_workspace_cwd() -> str | None:
    """The workspace dir to use as the subprocess CWD when no ``workdir`` is given.

    Files a command writes by relative name then land in the workspace, where
    ``send_file``/``write_file`` expect them. Self-healing (B5): if the dir cannot
    be created, log and fall back to ``None`` (process CWD) rather than crashing.
    """
    try:
        ws = StackowlHome.workspace()
        ws.mkdir(parents=True, exist_ok=True)
        return str(ws)
    except OSError as exc:  # B5 — never let a cwd-prep failure crash the command
        log.tool.warning(
            "shell.execute: workspace cwd unavailable — using process cwd",
            extra={"_fields": {"error": str(exc)}},
        )
        return None


def _split_flags_and_operands(rest: list[str]) -> tuple[list[str], list[str]]:
    """Partition the tail of a command into option flags and positional operands."""
    flags = [tok for tok in rest if tok.startswith("-")]
    operands = [tok for tok in rest if not tok.startswith("-")]
    return flags, operands


# Shell control operators that separate sub-commands. The detector splits on
# these so a chained sub-command (``cd /x && rm -rf /``) is inspected, not just
# the first word — now that the tool executes through a real shell, ``&&``/``|``/
# ``;`` are live, so the safety net must follow them.
_SHELL_OP_RE = re.compile(r"(&&|\|\||;|\||&|\n)")


def _shell_segments(args: list[str]) -> list[list[str]]:
    """Split a token list into sub-command argv lists on shell control operators.

    Best-effort and conservative: each token is exploded on operator boundaries
    (``"a;"`` → ``"a"`` then ``;``; ``"/&&rm"`` → ``/`` then ``&&`` then ``rm``)
    so spaced AND glued chains both surface their sub-commands. Used ONLY by the
    catastrophic detector; over-splitting can only make detection stricter, never
    looser (execution still uses the original command string verbatim).
    """
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in args:
        for part in _SHELL_OP_RE.split(tok):
            if part == "":
                continue
            if _SHELL_OP_RE.fullmatch(part):  # an operator → sub-command boundary
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append(part)
    if current:
        segments.append(current)
    return segments


# F-31 — recognized STDOUT redirection operators. ONLY stdout (`>`/`1>` and their
# append forms) is treated as the produced artifact: stderr (`2>`) and combined
# (`&>`) redirects are routinely EMPTY on a successful run, so verifying them would
# wrongly flag a real success. `-o`/`--output` flags are intentionally NOT parsed
# here — their value is too often a non-path option (e.g. `ssh -o Key=val`), which
# would make verification unsafe (a documented deferral, not a silent gap).
_STDOUT_REDIR_OPS: frozenset[str] = frozenset({">", ">>", "1>", "1>>"})
_GLUED_STDOUT_REDIR_RE = re.compile(r"^1?>>?(?P<path>.+)$")


def _redirect_target(argv: list[str]) -> str | None:
    """Best-effort: the stdout-redirection target path named in a shell command.

    ``argv`` is the shlex-split command. Returns the LAST stdout (`>`/`>>`) target
    path (shell semantics — last redirect wins), or ``None`` when the command names
    no such output file. Conservative: fd-2 / ``&>`` redirects, fd-dups (``>&2``)
    and ``/dev/*`` sinks are ignored, so verification never misfires on a command
    that legitimately writes nothing to a file. Pure-lexical, never raises.
    """
    target: str | None = None
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok in _STDOUT_REDIR_OPS:  # spaced form: `... > file`
            target = argv[i + 1] if i + 1 < n else None
            i += 2
            continue
        if not tok.startswith("2>") and not tok.startswith("&>"):
            m = _GLUED_STDOUT_REDIR_RE.match(tok)  # glued form: `>file`, `1>>file`
            if m:
                target = m.group("path")
        i += 1
    if not target:
        return None
    target = target.strip()
    # fd-dup (`>&2`) and device sinks (`/dev/null`) are not real artifacts.
    if not target or target.startswith("&") or target.startswith("/dev/"):
        return None
    return target


def is_catastrophic(args: list[str]) -> tuple[bool, str]:
    """Detect a truly system-destroying command shape (conservative).

    ``args`` is the shlex-split command. Returns ``(True, human_reason)`` on a
    match, ``(False, "")`` otherwise. Splits chained commands on shell operators
    and checks EACH sub-command, so ``cd /x && rm -rf /`` is caught even though
    the base word is ``cd``.
    """
    if not args:
        return (False, "")
    for segment in _shell_segments(args):
        hit, reason = _is_catastrophic_segment(segment)
        if hit:
            return (hit, reason)
    return (False, "")


def _is_catastrophic_segment(args: list[str]) -> tuple[bool, str]:
    """Catastrophic-shape check for a SINGLE sub-command (no operators).

    Intentionally narrow: it matches command STRUCTURE and target PATHS
    (multilingual-safe — no natural-language keywords), and errs toward catching
    only the obvious catastrophic shapes so that normal file writes/deletes
    (``rm -rf ./build``, ``echo x > f.txt``) run silently.
    """
    if not args:
        return (False, "")

    base = Path(args[0]).name
    rest = args[1:]

    # Fork bomb — the classic ``:(){ :|:& };:`` definition token. shell=False
    # largely neutralizes it, but flag it anyway (defense in depth).
    if any(tok.startswith(":(){") or tok == ":(){" for tok in args):
        return (True, "fork bomb")

    # rm -rf <system/home root>
    if base == "rm":
        flags, operands = _split_flags_and_operands(rest)
        if _is_recursive_force(flags) and any(_hits_system_root(op) for op in operands):
            target = next(op for op in operands if _hits_system_root(op))
            return (True, f"recursive force-delete of a system root: {target}")

    # dd of=/dev/... — overwriting a block device
    if base == "dd":
        for tok in rest:
            if tok.startswith("of=") and tok[3:].startswith("/dev/"):
                return (True, f"dd writing to a block device: {tok}")

    # mkfs / mkfs.* / wipefs / shred targeting a /dev device
    if base == "mkfs" or base.startswith("mkfs.") or base in ("wipefs", "shred"):
        _flags, operands = _split_flags_and_operands(rest)
        if any(op.startswith("/dev/") for op in operands):
            dev = next(op for op in operands if op.startswith("/dev/"))
            return (True, f"{base} targeting a device: {dev}")

    # chmod/chown -R on a system root
    if base in ("chmod", "chown"):
        flags, operands = _split_flags_and_operands(rest)
        if _is_recursive(flags) and any(_hits_system_root(op) for op in operands):
            target = next(op for op in operands if _hits_system_root(op))
            return (True, f"recursive {base} on a system root: {target}")

    return (False, "")


async def _gate_catastrophic(
    *, tool_name: str, command: str, reason: str
) -> ToolResult | None:
    """Require consent for a catastrophic command shape.

    Shared by :class:`ShellTool` and any tool that runs argv through
    :func:`run_argv` (e.g. a learned tool). Returns a structured declined
    :class:`ToolResult` when the command must NOT run (no user present, no gate,
    gate error, or user denial); returns ``None`` when the user approved and the
    command may proceed. Fail-closed everywhere — any error path denies and never
    spawns.
    """
    ctx = TraceContext.get()
    interactive = bool(ctx.get("interactive", False))
    channel = ctx.get("channel")
    session_id = ctx.get("session_id")
    log.tool.warning(
        "shell.execute: catastrophic command — requiring consent",
        extra={"_fields": {"reason": reason, "interactive": interactive, "channel": channel}},
    )

    # No interactive user to approve → fail closed (deny). Never auto-run.
    if not interactive or not session_id or not channel:
        log.tool.error(
            "shell.execute: catastrophic command and no user present — refused (fail closed)",
            extra={"_fields": {"reason": reason, "interactive": interactive}},
        )
        return ToolResult(
            success=False,
            output="",
            error=(
                "refused: catastrophic command and no user present to approve — "
                f"reason: {reason}"
            ),
            duration_ms=0,
            # Pre-execution refusal — the command was never spawned, so this is not
            # an effectful failure (it must not trip the honest give-up floor).
            side_effect_committed=False,
        )

    gate = get_services().consent_gate
    if gate is None:
        log.tool.error(
            "shell.execute: catastrophic command but NO consent gate wired — refused",
            extra={"_fields": {"reason": reason}},
        )
        return ToolResult(
            success=False,
            output="",
            error=f"refused: catastrophic command and no consent gate available — reason: {reason}",
            duration_ms=0,
            side_effect_committed=False,  # never spawned
        )

    try:
        allowed = await gate.policy.request(
            tool_name=tool_name,
            channel=channel,
            session_id=session_id,
            category="catastrophic",
            summary=f"Run shell command: {command}",
        )
    except Exception as exc:  # no-hidden-errors — fail closed on any gate error
        log.tool.error(
            "shell.execute: consent gate raised — refused (fail closed)",
            exc_info=exc,
            extra={"_fields": {"reason": reason}},
        )
        return ToolResult(
            success=False,
            output="",
            error=f"refused: consent check failed — reason: {reason}",
            duration_ms=0,
            side_effect_committed=False,  # gate error before any spawn
        )

    if not allowed:
        log.tool.info(
            "shell.execute: catastrophic command declined by user",
            extra={"_fields": {"reason": reason}},
        )
        return ToolResult(
            success=False,
            output="",
            error=f"declined by user — reason: {reason}",
            duration_ms=0,
            side_effect_committed=False,  # declined → never spawned
        )

    log.tool.info(
        "shell.execute: catastrophic command approved — proceeding",
        extra={"_fields": {"reason": reason}},
    )
    return None


async def run_argv(
    argv: list[str],
    *,
    tool_name: str = "shell",
    workdir: str | None = None,
    timeout_sec: float = _TIMEOUT_SEC,
    shell_command: str | None = None,
    intent: str = "write",
) -> ToolResult:
    """Run a command through the shared subprocess boundary.

    The SINGLE execution seam shared by :class:`ShellTool` and learned tools: the
    catastrophic-shape check (operator-aware, see :func:`is_catastrophic`) +
    consent path, then the spawn, timeout, and OSError handling, with 4-point
    logging. Honors the workspace-CWD default (no ``workdir`` → workspace). Never
    raises — every failure becomes a structured failed :class:`ToolResult`.

    ``intent`` is the CALLER's per-invocation declaration of what the command does:
    ``"read"`` for a read-only probe/query (``curl -sI``, ``test -f``, ``grep``),
    ``"write"`` (default — conservative, byte-identical to the historical behavior)
    for anything that installs/writes/sends/mutates. It sets the returned
    ``side_effect_committed`` so a FAILED read-only probe is not miscounted as a
    failed mutation by the honest give-up floor. The declaration is NOT trusted
    over reality: a command that redirects stdout to a named file is an OBSERVABLE
    write and is treated as a write regardless of ``intent`` (anti-gaming, mirroring
    the base :class:`Tool` demoting a self-asserted ``verified=True``).

    Two execution modes:

    * ``shell_command`` given → run it through a real shell
      (``create_subprocess_shell`` = ``/bin/sh -c``) so builtins (``cd``),
      operators (``&&``/``||``/``;``), pipes and redirects work. ``argv`` is the
      shlex-split form, used ONLY for the catastrophic safety check.
    * ``shell_command`` ``None`` → run ``argv`` directly via
      ``create_subprocess_exec`` (``shell=False``). The path learned tools use:
      pre-built argv, no shell interpretation.
    """
    t0 = time.monotonic()
    if not argv:
        return ToolResult(
            success=False, output="", error="Empty command", duration_ms=0,
            side_effect_committed=False,  # nothing to spawn
        )

    cwd = workdir or _default_workspace_cwd()
    rendered = " ".join(argv)
    log.tool.debug(
        "shell.execute: entry",
        extra={"_fields": {"command": rendered[:200], "timeout_sec": timeout_sec, "intent": intent}},
    )

    # PER-INVOCATION side-effect boundary (DECISION). A caller-declared read that
    # names no output file crosses no side-effect boundary → committed=False, so a
    # FAILED read-only probe is not miscounted as a failed mutation by the honest
    # give-up floor. Anti-gaming: a stdout redirect to a named file is an OBSERVABLE
    # write that REFUTES a 'read' declaration — reality wins over the self-report.
    # (Redirects are inert under exec mode, so the refutation only applies to a real
    # shell command, mirroring the F-31 artifact_path logic below.)
    has_named_write = shell_command is not None and _redirect_target(argv) is not None
    read_only = intent == "read" and not has_named_write
    committed = not read_only
    if intent == "read":
        log.tool.debug(
            "shell.execute: read-only intent declared",
            extra={"_fields": {"honored": read_only, "named_write": has_named_write}},
        )

    # CATASTROPHIC gate — every command runs silently EXCEPT a narrow set of
    # system-destroying shapes, which require the user's explicit approval.
    catastrophic, reason = is_catastrophic(argv)
    if catastrophic:
        decision = await _gate_catastrophic(tool_name=tool_name, command=rendered, reason=reason)
        if decision is not None:
            return decision  # refused / declined / fail-closed — never spawns

    log.tool.debug(
        "shell.execute: launching subprocess",
        extra={"_fields": {"args": argv[:5], "shell": shell_command is not None}},
    )
    try:
        if shell_command is not None:
            proc = await asyncio.create_subprocess_shell(
                shell_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
            )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except TimeoutError:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "shell.execute: timeout",
            extra={"_fields": {"command": rendered[:100], "timeout_sec": timeout_sec, "duration_ms": duration_ms}},
        )
        return ToolResult(
            success=False, output="", error=f"Command timed out after {timeout_sec}s",
            duration_ms=duration_ms, side_effect_committed=committed,
        )
    except OSError as exc:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.error("shell.execute: OS error", exc_info=exc, extra={"_fields": {"command": rendered[:100]}})
        return ToolResult(
            success=False, output="", error=str(exc),
            duration_ms=duration_ms, side_effect_committed=committed,
        )

    duration_ms = (time.monotonic() - t0) * 1000
    success = proc.returncode == 0
    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip() if not success else None
    # F-31 — when the command UNAMBIGUOUSLY redirects stdout to a named file, record
    # that file as the artifact this call produced so the verify() seam can read it
    # back (exists + non-empty + fresh) and surface an exit-0-but-produced-nothing
    # command as verified=False instead of a silent false success. Only meaningful
    # for real shell execution — operators/redirects are inert under exec mode — and
    # only when a file is named; otherwise artifact_path stays None ⇒ verified stays
    # None (we never over-claim verification for a generic shell command).
    artifact_path: str | None = None
    if shell_command is not None:
        redir = _redirect_target(argv)
        if redir is not None:
            base = cwd or os.getcwd()
            artifact_path = redir if os.path.isabs(redir) else os.path.join(base, redir)
    log.tool.debug(
        "shell.execute: exit",
        extra={
            "_fields": {
                "success": success,
                "returncode": proc.returncode,
                "output_len": len(output),
                "artifact_path": artifact_path,
                "duration_ms": duration_ms,
            }
        },
    )
    return ToolResult(
        success=success, output=output, error=error,
        duration_ms=duration_ms, artifact_path=artifact_path,
        side_effect_committed=committed,
    )


class ShellTool(Tool):
    """Run any shell command in a subprocess; catastrophic ones need consent."""

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return (
            "Run a shell command in a subprocess. Full shell syntax works: "
            "builtins (cd), operators (&&, ||, ;), pipes (|) and redirects (>). "
            "Installs, downloads, network and file writes run silently with no "
            "prompt. Only truly catastrophic, system-destroying commands "
            "(rm -rf on a system/home root, dd/mkfs/shred/wipefs on a device, "
            "recursive chmod/chown on a system root, fork bombs) require the user's "
            "explicit approval; if no user is present to approve, they are refused."
        )

    @property
    def manifest(self) -> ToolManifest:
        """Shell genuinely MUTATES the world (installs, writes, network) → 'write'.

        'write' (not 'consequential') so the ledger marks shell side-effecting and
        the delegation gate treats a shell-capable owl as already-acted, WITHOUT
        adding a consent prompt (the gate fires only on 'consequential'; the
        narrow catastrophic-shape consent path is separate, inside run_argv).
        """
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="unconfirmed",
            progress_key="RUN_CMD",
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command string, run through a real shell — chaining "
                        "(cd /dir && make), pipes (a | b) and redirects (> f) all "
                        "work. Any command runs silently — including installs, "
                        "downloads, network calls and file writes. Only catastrophic, "
                        "system-destroying commands require the user's explicit "
                        "approval before they run. Tip: set workdir instead of a "
                        "leading cd when you just need a different directory."
                    ),
                },
                "workdir": {
                    "type": "string",
                    "description": (
                        "Working directory (optional; defaults to the StackOwl "
                        "workspace directory, where files written by relative name land)."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Optional per-command timeout in seconds (default "
                        f"{int(_TIMEOUT_SEC)}, bounded to {int(_TIMEOUT_CEILING_SEC)}). "
                        "Raise it for longer installs/downloads/builds."
                    ),
                },
                "intent": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": (
                        "What the command DOES (optional; default 'write'). Set "
                        "'read' for a read-only probe or query that only inspects "
                        "state — e.g. curl -sI, test -f, ls, cat, grep, git status. "
                        "Set 'write' (or omit) for anything that installs, downloads, "
                        "writes/deletes files, sends, or otherwise mutates. This only "
                        "affects how a FAILED command is judged: a failed 'read' probe "
                        "is treated as a normal empty/negative answer, not a failed "
                        "action. Declaring 'read' for a command that redirects output "
                        "to a file has no effect — that is still a write."
                    ),
                },
            },
            "required": ["command"],
        }

    async def verify(
        self, args: dict[str, object], result: ToolResult, *, started_at: float
    ) -> bool | None:
        """Post-condition: if the command redirected stdout to a named file, that
        file now exists, is non-empty, and is THIS run's artifact (fresh).

        No named output file ⇒ ``result.artifact_path`` is None ⇒ verify_artifact
        returns None ⇒ ``verified`` stays None. Shell is a generic actuator: we
        only read back the ONE effect the invocation explicitly names, and never
        over-claim verification for an arbitrary command.
        """
        from stackowl.tools.verification import verify_artifact

        return verify_artifact(result.artifact_path, not_before=started_at)

    async def execute(self, **kwargs: object) -> ToolResult:
        command = str(kwargs.get("command", ""))
        # No explicit workdir → default the subprocess CWD to the workspace, so a
        # file the command writes by relative name lands where send_file/write_file
        # expect it. An explicit non-empty workdir still wins.
        workdir = str(kwargs.get("workdir", "")) or None
        timeout_sec = _resolve_timeout(kwargs.get("timeout"))
        # Per-call intent (caller-declared). Unknown/absent → 'write' (conservative,
        # byte-identical to the historical static severity). See run_argv.
        intent = str(kwargs.get("intent", "write")).strip().lower()
        if intent not in ("read", "write"):
            intent = "write"
        try:
            args = shlex.split(command)
        except ValueError as exc:
            return ToolResult(
                success=False, output="", error=f"Invalid command syntax: {exc}", duration_ms=0,
                side_effect_committed=False,  # unparseable — never spawned
            )
        # The shared seam does the catastrophic-shape check (on the shlex-split
        # `args`) + consent path, the workspace-CWD default, the spawn, timeout
        # and OSError handling. We pass the ORIGINAL command string so it runs
        # through a real shell — builtins (cd), operators (&&/||/;), pipes and
        # redirects all work — while the safety check still inspects `args`.
        return await run_argv(
            args,
            tool_name="shell",
            workdir=workdir,
            timeout_sec=timeout_sec,
            shell_command=command,
            intent=intent,
        )
