"""J3 JOURNEY — "Debug a failing script" (PRD §3, J3).

The business requirement, verbatim from ``_bmad-output/planning-artifacts/
prd-tool-expansion.md`` §3:

  > **J3 — Debug a failing script.** *"This script throws on line 40 — fix it."*
  > → ``search_files``/``read_file`` locate it → ``execute_code`` (sandboxed) to
  > reproduce → diagnose → ``edit``/``apply_patch`` to fix → re-run
  > ``execute_code`` to confirm green.

``execute_code`` is **E11 — NOT shipped yet**. So this test proves the SHIPPABLE
ARC available now (E3): the user reports a bug → the agent LOCATES the script and
the buggy line (``search_files`` → ``read_file``, REAL) → FIXES it on disk
(``edit``, REAL). The reproduce + confirm-green ``execute_code`` steps are
``@pytest.mark.skip``-marked below so the journey COMPLETES (no silent gap) the
moment E11 lands.

This is NOT a per-tool smoke. It proves the USER's END-TO-END OUTCOME across
several tools, driving a single real inbound Telegram message through the GENUINE
path (TelegramChannelAdapter → GatewayScanner → AsyncioBackend pipeline →
execute._dispatch → ToolRegistry) and mocking ONLY the AI provider. The scripted
secretary owl drives the REAL tool loop within one turn:

    search_files(locate the buggy script by content)  →  read_file(read it)
        →  derive the buggy line FROM the real read_file output
        →  edit(replace the buggy line with the fix, on disk, REAL)
        →  return a confirmation that QUOTES the real fix as the inline reply.

REAL (everything except the AI): the whole pipeline, ``ToolRegistry`` +
``SearchFilesTool``/``ReadFileTool``/``EditTool`` (incl. fuzzy locate, line-ending
preservation, undo snapshot, post-write read-back verify), the path guard
confining to the workspace, the consent gate (``edit`` is severity 'write' — no
consent round-trip fires), and the Telegram adapter's inbound + outbound
transport. The buggy script is a REAL file written to disk under the test
workspace with a KNOWN bug on a KNOWN line, so the fix is checkable byte-for-byte.
FAKED: ONLY the AI provider (scripted, owl-aware, honoring the ModelProvider
contract — ``name`` + a real ``CompletionResult`` so the REAL triage/execute steps
run genuinely) and the Telegram bot HTTP transport (``_FakeBot``, in-process).

Business-outcome assertion (the LIVE arc): the user's buggy file ON DISK now
contains the FIX and no longer contains the bug — derived from the REAL
``read_file`` output, not constants — AND the agent's reply confirming the fix
reached the user's Telegram chat. A broken search/read/edit fails the test.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner
from stackowl.owls.registry import OwlRegistry
from stackowl.paths import StackowlHome
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.sandbox.bwrap import BwrapSandbox
from stackowl.sandbox.capability import SandboxCapability
from stackowl.sandbox.cgroup import CgroupRecipe
from stackowl.sandbox.selector import SandboxSelector
from stackowl.tools.consent import (
    ConsentPolicy,
    ConsentRequest,
    ConsentScope,
    RoutingPrompter,
)
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 434343

# The REAL buggy script written to disk. The bug is on line 4: an off-by-one
# wrong operator — ``>=`` where ``>`` is meant — so ``classify(0)`` wrongly returns
# "positive". The user reports it as "throws/wrong on that line". The fix replaces
# ``>=`` with ``>``. Both the bug token and the fix token are checkable on disk.
_SCRIPT_NAME = "classifier.py"
_BUG_LINE_NO = 4  # 1-based line carrying the bug, what the user cites
_BUG_TOKEN = "if value >= 0:"  # the buggy comparison
_FIX_TOKEN = "if value > 0:"  # the corrected comparison
# A distinctive marker so search_files locates THIS script (and proves the search
# genuinely ran — the located path is read straight out of the real tool output).
# search_files' content target is a REGEX, so the marker is metacharacter-free
# plain text (a unique function name) that means the same literal and as a regex.
_SEARCH_MARKER = "classify_sign"

_BUGGY_SCRIPT = (
    "def classify_sign(value):\n"
    '    """Return the sign label for value."""\n'
    "    # BUG: should be a strict greater-than; >= mislabels 0 as positive.\n"
    "    if value >= 0:\n"
    '        return "positive"\n'
    '    return "negative"\n'
)


# --- FAKED #1: the Telegram bot HTTP transport (captures outbound in-process) ----


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        pass


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


# --- FAKED #2 (THE ONLY AI MOCK): the secretary owl's scripted provider ---------


class _ScriptedSecretary:
    """The ONLY mock: stands in for the secretary owl's LLM.

    Within a SINGLE ``complete_with_tools`` call it drives the REAL tool loop via
    the REAL ``tool_dispatcher``, exactly as a real debugging model would:

      1. search_files — LOCATE the buggy script by a content marker (real engine).
      2. read_file    — read the located file (real, confined to the workspace).
      3. derive the buggy line FROM the real read_file output (not a constant).
      4. edit         — replace the buggy line with the fix, ON DISK (real EditTool:
                        fuzzy locate, line-ending preserve, undo snapshot,
                        post-write read-back verify).

    Returns a confirmation that QUOTES the real fix as the final inline reply,
    which the adapter delivers to the user. Records each tool's structured output
    for diagnostics — the assertions check the user-visible OUTCOME (the file on
    disk + the delivered reply), not these records.

    Honors the ModelProvider contract (``name`` + a real ``CompletionResult`` from
    ``complete``) so the REAL ``triage``/``execute`` pipeline steps resolve it
    genuinely (a silently-crashing triage would be a no-hidden-errors violation).
    """

    protocol = "anthropic"

    def __init__(self) -> None:
        self.search_out: str = ""
        self.read_out: str = ""
        self.edit_out: str = ""
        self.after_src: str = ""
        self.located_path: str = ""
        self.derived_bug_line: str = ""
        self.final: str = ""

    @property
    def name(self) -> str:
        return "secretary"

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        calls: list[dict] = []

        # 1. LOCATE the buggy script by its content marker (real search engine).
        search_args = {"pattern": _SEARCH_MARKER, "target": "content"}
        self.search_out = await tool_dispatcher("search_files", search_args)
        calls.append({"name": "search_files", "args": search_args, "result": self.search_out})
        # Resolve the located path FROM the real search output (not a constant), so a
        # broken search can't false-pass: the first hit's "path:line: text" head.
        results = json.loads(self.search_out).get("results", [])
        assert results, (
            "search_files did not locate the buggy script — cannot proceed to read/fix. "
            f"search output was: {self.search_out!r}"
        )
        # search_files renders hits WORKSPACE-RELATIVE (path:line: text). The agent
        # pipes that RELATIVE hit path STRAIGHT into read_file/edit — no manual
        # workspace resolution — because read_file/edit now anchor a relative path
        # under the workspace exactly as search_files does (the round-trip fix). The
        # located path still comes from the REAL search output — a broken search
        # yields the wrong file and the read/edit downstream assertions fail.
        self.located_path = results[0].split(":", 1)[0]

        # 2. READ the located file (real read_file, confined to the workspace).
        read_args = {"path": self.located_path}
        self.read_out = await tool_dispatcher("read_file", read_args)
        calls.append({"name": "read_file", "args": read_args, "result": self.read_out})

        # 3. DERIVE the buggy line FROM the real read_file output — NOT a constant.
        #    A real model reads the file and copies the offending line verbatim; here
        #    we pull line _BUG_LINE_NO straight out of what read_file returned, so the
        #    edit's old_string can only be right if read_file genuinely returned the
        #    file's content. (Keeps the read->fix coupling in the data flow.)
        file_lines = self.read_out.splitlines()
        self.derived_bug_line = file_lines[_BUG_LINE_NO - 1].strip()
        assert self.derived_bug_line == _BUG_TOKEN, (
            "read_file did not return the known buggy line — the fix cannot be "
            f"derived from real content. Line {_BUG_LINE_NO} was: {self.derived_bug_line!r}"
        )

        # 4. FIX it ON DISK: replace the derived buggy line with the corrected one
        #    (real EditTool — unique fuzzy locate + read-back verify + undo snapshot).
        new_line = self.derived_bug_line.replace(">=", ">")
        edit_args = {
            "path": self.located_path,
            "old_string": self.derived_bug_line,
            "new_string": new_line,
        }
        self.edit_out = await tool_dispatcher("edit", edit_args)
        calls.append({"name": "edit", "args": edit_args, "result": self.edit_out})

        self.final = (
            f"Found the bug in {self.located_path} on line {_BUG_LINE_NO}: the comparison "
            f"`{self.derived_bug_line}` mislabels 0 as positive. I changed it to "
            f"`{new_line}` so classify(0) now returns negative. Fixed on disk."
        )
        return (self.final, calls)

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        # Honor the ModelProvider result contract so the REAL triage/execute steps
        # run genuinely (returning a bare string would crash triage and be swallowed
        # — a no-hidden-errors violation fixed across the other journeys).
        return CompletionResult(
            content="I'll locate the script and fix the buggy line.",
            input_tokens=8,
            output_tokens=10,
            model="secretary-model",
            provider_name="secretary",
            duration_ms=1.0,
        )

    async def stream(self, *a, **k):  # pragma: no cover — not on this path
        if False:
            yield ""


class _FakeProviderRegistry:
    """Matches the REAL ProviderRegistry's return SHAPES, not just its names.

    `get` returns a provider; `get_by_tier` and `get_with_cascade` return
    `(provider, model)` — `registry.py:465/523/557`. This double returned a bare
    provider from all three, so `resolve_cascade_tier` (which returns
    `get_with_cascade(tier)` VERBATIM) handed the router something it could not
    unpack: `TypeError: cannot unpack non-iterable`. The E3 arc never took that
    branch, so the drift sat here harmlessly until the E11 arc did — a test
    double that stopped resembling the real thing, which is defect shape 2.
    """

    def __init__(self, p: object) -> None:
        self._p = p

    def get(self, name: str) -> object:
        return self._p

    def get_by_tier(self, tier: str) -> tuple[object, str]:
        return (self._p, "scripted")

    def get_with_cascade(self, preferred_tier: str) -> tuple[object, str]:
        # The router/critical_failure steps resolve via cascade; route them to the
        # single scripted secretary so the REAL router runs clean (no swallowed
        # AttributeError — keeps the no-hidden-errors discipline).
        return (self._p, "scripted")


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: object
    #: The consent prompter, when this env wired one (the E11 arc). Held here
    #: rather than dug out of ConsentPolicy's internals, which the first cut did
    #: and which broke on a private name.
    prompter: object | None = None


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


async def _turn(env: _Env, text: str) -> str:
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=USER_ID),
        effective_chat=SimpleNamespace(id=USER_ID),
    )
    await env.adapter._handle_update(update, None)
    msg = await env.adapter.receive()
    decision = env.scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    _writer, reader = env.stream_registry.create(msg.trace_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_key=msg.session_key, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.trace_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


class _ApprovingPrompter:
    """Stands in for the human at the consent prompt — approves ONCE, and records.

    `execute_code` is consequential, so the REAL consent gate fires. Faking the
    HUMAN is legitimate here (a journey test cannot type into Telegram); faking
    the GATE would not be, so the real `ConsequentialActionGate` and
    `ConsentPolicy` still run and a regression that stopped asking would be
    caught by `asked` being empty.
    """

    def __init__(self) -> None:
        self.asked: list[ConsentRequest] = []

    async def prompt(self, req: ConsentRequest) -> ConsentScope:
        self.asked.append(req)
        return ConsentScope.ONCE


def _bwrap_live() -> bool:
    """True iff a REAL bwrap run can isolate AND enforce caps on this host.

    Same probe J11 uses. Measured on this box 2026-09-06: True, and J11's six
    live-sandbox tests pass — which is half of why the E11 arc below stopped
    being skipped.
    """
    import shutil

    if shutil.which("bwrap") is None:
        return False
    if not SandboxCapability.probe().bwrap_viable:
        return False
    cg_ok, _why = CgroupRecipe.delegation_available()
    return bool(cg_ok)


_NEEDS_BWRAP = pytest.mark.skipif(
    not _bwrap_live(),
    reason="real bwrap sandbox not viable on this host (live-run outcomes)",
)


def _build(provider: object, *, sandboxed: bool = False) -> _Env:
    # The SAME adapter drives inbound (the user's bug report) AND outbound (the
    # agent's fix confirmation).
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    # `execute_code` is CONSEQUENTIAL and consent-gated, and it refuses outright
    # without a sandbox selector ("no sandbox_selector wired — refusing"). The
    # E11 arc therefore needs both; the E3 arc above needs neither, so both stay
    # optional and that arc's wiring is byte-identical to what it always was.
    approving = _ApprovingPrompter() if sandboxed else None
    if approving is not None:
        routing = RoutingPrompter()
        routing.register("telegram", approving)
        consent_gate = ConsequentialActionGate(ConsentPolicy(prompter=routing))
    else:
        consent_gate = ConsequentialActionGate()  # edit is 'write' — no consent fires
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),  # REAL search_files/read_file/edit
        consent_gate=consent_gate,
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        # REAL SandboxSelector with the REAL bwrap backend — the code genuinely
        # runs in the cage, or this arc proves nothing about reproducing a bug.
        sandbox_selector=(
            SandboxSelector(backends=[BwrapSandbox(enabled=True)]) if sandboxed else None
        ),
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider, prompter=approving,
    )


async def test_j3_debug_failing_script_locate_and_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Workspace under a tmp home so the path guard confines search/read/edit to it
    # and nothing is written inside the project dir.
    home = tmp_path / "home"
    ws = home / "workspace"
    (ws / "src").mkdir(parents=True)
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))

    script_path = ws / "src" / _SCRIPT_NAME
    script_path.write_text(_BUGGY_SCRIPT, encoding="utf-8")
    # Precondition: a REAL buggy file on disk carrying the KNOWN bug on the KNOWN line.
    assert script_path.exists()
    assert _BUG_TOKEN in script_path.read_text(encoding="utf-8")
    assert _FIX_TOKEN not in script_path.read_text(encoding="utf-8")

    env = _build(_ScriptedSecretary())

    # The user reports the bug — AS THE USER, over Telegram — and asks for the fix.
    reply = await _turn(
        env,
        f"This script {script_path} throws on line {_BUG_LINE_NO} — fix it.",
    )

    # ===================================================================
    # BUSINESS OUTCOME (the LIVE arc) — the user's buggy file ON DISK is ACTUALLY
    # FIXED: it now contains the corrected line and no longer contains the bug. The
    # asserted text is DERIVED from the REAL read_file output the agent saw, not a
    # constant — so a broken search/read/edit cannot false-pass.
    # ===================================================================
    on_disk = script_path.read_text(encoding="utf-8")
    derived_bug = env.provider.derived_bug_line  # pulled from real read_file output
    fixed_line = derived_bug.replace(">=", ">")
    assert env.provider.located_path, "search_files never located the script"

    assert fixed_line in on_disk, (
        "BUSINESS OUTCOME FAIL: the corrected line did not land on disk. "
        f"Expected {fixed_line!r} in:\n{on_disk}"
    )
    assert derived_bug not in on_disk, (
        "BUSINESS OUTCOME FAIL: the buggy line is STILL on disk — the fix did not "
        f"replace it. Bug {derived_bug!r} still present in:\n{on_disk}"
    )
    # The behavioural meaning of the fix survives: classify(0) -> negative.
    ns: dict[str, object] = {}
    exec(compile(on_disk, str(script_path), "exec"), ns)  # noqa: S102 — fixed file, our content
    assert ns["classify_sign"](0) == "negative", (  # type: ignore[operator]
        "BUSINESS OUTCOME FAIL: the fixed script still mislabels 0 as positive."
    )

    # The real EditTool proved the write (undo token + unified diff surfaced), so
    # the user is one undo away and can see exactly what changed.
    assert "Undo token:" in env.provider.edit_out, env.provider.edit_out
    assert "@@" in env.provider.edit_out or "+    if value > 0:" in env.provider.edit_out

    # ===================================================================
    # BUSINESS OUTCOME (delivery) — the fix confirmation reached the user's chat.
    # ===================================================================
    delivered = "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)
    assert reply.strip() and env.bot.messages[-1]["chat_id"] == USER_ID
    # The adapter MarkdownV2-escapes punctuation outbound; assert on the unescaped
    # core word "negative" (the fix's user-visible meaning) which survives escaping.
    assert "negative" in delivered.lower(), (
        "BUSINESS OUTCOME FAIL: the fix confirmation did not reach the user's chat. "
        f"Delivered text was: {delivered!r}"
    )


# --- THE ONLY AI MOCK for the E11 arc: a debugger that REPRODUCES before fixing --


class _ScriptedDebugger:
    """Drives the full J3 loop: reproduce RED -> fix -> re-run GREEN.

    Every step below is the REAL tool through the REAL dispatcher; only this
    provider is scripted.

      1. read_file      — read the user's buggy script (real, workspace-confined).
      2. execute_code   — run THAT CONTENT plus a check that asserts the correct
                          behaviour. It FAILS: the bug is reproduced, in the cage.
      3. edit           — fix the buggy line ON DISK (real EditTool).
      4. execute_code   — the SAME check against the fixed content. It PASSES.

    WHY THERE IS NO SECOND read_file. A repeat call with identical arguments is
    exactly what the platform's loop guard exists to stop, and it DID: the
    second read returned the guard's directive instead of the file, which then
    went into the sandbox and died as a SyntaxError. The guard was right. So the
    post-fix source is derived by applying the same one-line replacement, and
    the test asserts that derived text equals the file ON DISK byte-for-byte —
    which proves the executed code IS the file's content more explicitly than a
    re-read would, since a re-read only proves the two calls agreed.

    WHY THE CONTENT TRAVELS AS CODE RATHER THAN A PATH. The bwrap backend mounts
    only its OWN per-run scratch workspace — the user's workspace is deliberately
    never bind-mounted, which is the sandbox's whole point. So "run the user's
    script" can only mean "run what read_file actually returned", and that is
    also what keeps the arc honest: the code executed is DERIVED from the real
    read, so a broken read_file or a broken edit cannot false-pass.
    """

    protocol = "anthropic"

    @property
    def name(self) -> str:
        return "secretary"

    def __init__(self) -> None:
        self.reproduce_out: str = ""
        self.confirm_out: str = ""
        self.edit_out: str = ""
        self.after_src: str = ""
        self.located_path: str = ""
        self.final: str = ""

    @staticmethod
    def _check(source: str) -> str:
        """The SAME check both times — the transition is the business outcome."""
        return (
            source
            + "\n"
            + "assert classify_sign(0) == \"negative\", (\n"
            + "    \"REPRODUCED: classify_sign(0) returned \" + classify_sign(0)\n"
            + ")\n"
            + "print(\"GREEN\")\n"
        )

    @staticmethod
    def _failed(raw: str) -> bool:
        """Did the sandboxed run FAIL? Read the record, never the prose."""
        try:
            record = json.loads(raw).get("record", {})
        except (json.JSONDecodeError, AttributeError):
            return True
        return int(record.get("exit_code", 1)) != 0

    async def complete_with_tools(  # noqa: ANN001
        self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None, **_kw
    ):
        calls: list[dict] = []
        self.located_path = f"src/{_SCRIPT_NAME}"

        # 1. READ the user's script (real, workspace-confined).
        read_args = {"path": self.located_path}
        before_src = await tool_dispatcher("read_file", read_args)
        calls.append({"name": "read_file", "args": read_args, "result": before_src})

        # 2. REPRODUCE: run the real content + the check, in the real sandbox.
        repro_args = {"code": self._check(before_src), "language": "python"}
        self.reproduce_out = await tool_dispatcher("execute_code", repro_args)
        calls.append({"name": "execute_code", "args": repro_args, "result": self.reproduce_out})

        # 3. FIX it on disk — the derived line, exactly as the live arc above does.
        bug_line = before_src.splitlines()[_BUG_LINE_NO - 1].strip()
        edit_args = {
            "path": self.located_path,
            "old_string": bug_line,
            "new_string": bug_line.replace(">=", ">"),
        }
        self.edit_out = await tool_dispatcher("edit", edit_args)
        calls.append({"name": "edit", "args": edit_args, "result": self.edit_out})

        # 4. CONFIRM GREEN: the SAME check, against the fixed content. The test
        #    asserts `after_src` equals the file on disk, so this is not a
        #    constant standing in for reality.
        self.after_src = before_src.replace(bug_line, edit_args["new_string"])
        confirm_args = {"code": self._check(self.after_src), "language": "python"}
        self.confirm_out = await tool_dispatcher("execute_code", confirm_args)
        calls.append({"name": "execute_code", "args": confirm_args, "result": self.confirm_out})

        self.final = (
            "Reproduced the failure, fixed the comparison, and re-ran the same "
            "check — it now passes."
        )
        return (self.final, calls)

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        # The FULL result contract, as the live arc's provider does — a partial
        # one raises a pydantic ValidationError inside the REAL router, which
        # then falls back and swallows the cause.
        return CompletionResult(
            content="I'll reproduce the failure, fix it, and re-run the check.",
            input_tokens=8,
            output_tokens=10,
            model="secretary-model",
            provider_name="secretary",
            duration_ms=1.0,
        )


@_NEEDS_BWRAP
async def test_j3_reproduce_and_confirm_green_with_execute_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E11 continuation of J3 — the sandboxed reproduce + confirm-green steps.

    UNSKIPPED 2026-09-06, and the skip is why this exists. It carried
    ``reason="E11 execute_code not shipped"`` in TWO places — the decorator and
    the body's first statement — while `execute_code` had shipped at
    `tools/code/execute_code.py` with 42 recorded invocations, and J11's six
    real-sandbox tests pass on this host. The function beneath those two claims
    was a docstring and nothing else.

    Business outcome: the SAME sandboxed check transitions from RED to GREEN
    across the real edit — the user's script is demonstrably fixed by RUNNING it,
    not by inspecting the diff.
    """
    home = tmp_path / "home"
    ws = home / "workspace"
    (ws / "src").mkdir(parents=True)
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))

    script_path = ws / "src" / _SCRIPT_NAME
    script_path.write_text(_BUGGY_SCRIPT, encoding="utf-8")
    assert _BUG_TOKEN in script_path.read_text(encoding="utf-8")

    env = _build(_ScriptedDebugger(), sandboxed=True)
    reply = await _turn(
        env,
        f"This script {script_path} throws on line {_BUG_LINE_NO} — fix it.",
    )
    dbg: _ScriptedDebugger = env.provider  # type: ignore[assignment]

    # ===================================================================
    # BUSINESS OUTCOME — RED then GREEN, from the SAME check, in the REAL cage.
    # Asserted on the sandbox's own exit_code, not on prose, so a sandbox that
    # silently refused to run cannot read as a pass.
    # ===================================================================
    assert dbg.reproduce_out, "the reproduce step never ran"
    assert dbg._failed(dbg.reproduce_out), (
        "BUSINESS OUTCOME FAIL: the check did not FAIL against the buggy script, so "
        f"the bug was never reproduced. execute_code returned: {dbg.reproduce_out!r}"
    )
    assert not dbg._failed(dbg.confirm_out), (
        "BUSINESS OUTCOME FAIL: the same check still fails after the fix — the edit "
        f"did not resolve the reported failure. execute_code returned: {dbg.confirm_out!r}"
    )

    # THE CONFIRM STEP RAN THE FILE'S ACTUAL CONTENT. Without this the GREEN
    # result would only prove that some string passes the check.
    on_disk = script_path.read_text(encoding="utf-8")
    assert dbg.after_src == on_disk, (
        "the confirmed-green code is not what is on disk — the GREEN result proves "
        f"nothing about the user's file.\n  ran: {dbg.after_src!r}\n disk: {on_disk!r}"
    )

    # The fix is real and on disk, not merely reported.
    assert _FIX_TOKEN in on_disk and _BUG_TOKEN not in on_disk, (
        f"the file on disk was not actually fixed: {on_disk!r}"
    )

    # The user was told, over the real transport.
    assert reply.strip(), "the user received nothing"

    # THE CAGE WAS REALLY ASKED. execute_code is consequential, so the REAL
    # consent gate must have prompted — an empty list here means the gate was
    # bypassed and this arc proved less than it claims.
    assert env.prompter is not None and env.prompter.asked, (  # type: ignore[union-attr]
        "execute_code ran without the REAL consent gate ever prompting"
    )
    assert any(
        "execute_code" in str(getattr(r, "tool_name", "") or getattr(r, "summary", ""))
        for r in env.prompter.asked  # type: ignore[union-attr]
    ), f"the gate prompted, but not for execute_code: {env.prompter.asked}"  # type: ignore[union-attr]
