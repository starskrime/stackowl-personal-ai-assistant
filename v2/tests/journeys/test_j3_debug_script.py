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
    def __init__(self, p: _ScriptedSecretary) -> None:
        self._p = p

    def get(self, name: str) -> _ScriptedSecretary:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedSecretary:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _ScriptedSecretary:
        # The router/critical_failure steps resolve via cascade; route them to the
        # single scripted secretary so the REAL router runs clean (no swallowed
        # AttributeError — keeps the no-hidden-errors discipline).
        return self._p


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    provider: _ScriptedSecretary


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
    _writer, reader = env.stream_registry.create(msg.session_id)
    state = PipelineState(
        trace_id=msg.trace_id, session_id=msg.session_id, input_text=input_text,
        channel=msg.channel, owl_name=decision.target, pipeline_step="start",
    )
    before = len(env.bot.messages)
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)
    return "".join(m["text"] for m in env.bot.messages[before:] if m["reply_markup"] is None)


def _build(provider: _ScriptedSecretary) -> _Env:
    # The SAME adapter drives inbound (the user's bug report) AND outbound (the
    # agent's fix confirmation).
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)  # type: ignore[assignment]
    adapter._bot_user_id = 999
    adapter._bot_username = ""

    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),  # REAL search_files/read_file/edit
        consent_gate=ConsequentialActionGate(),  # edit is 'write' — no consent fires
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        provider=provider,
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


@pytest.mark.skip(
    reason="E11 execute_code not shipped — reproduce + confirm-green steps complete "
    "this journey when E11 lands."
)
async def test_j3_reproduce_and_confirm_green_with_execute_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E11 continuation of J3 — the sandboxed reproduce + confirm-green steps.

    When ``execute_code`` ships (E11), this completes the full J3 loop the PRD
    describes, with NO new business logic faked beyond the AI provider:

      1. search_files/read_file locate the buggy script (as in the live arc above).
      2. execute_code (sandboxed) RUNS the script and REPRODUCES the failure
         (e.g. an assert that classify_sign(0) == "negative" fails) — proving the bug
         is real before touching it.
      3. edit fixes the buggy line on disk (as in the live arc above).
      4. execute_code RE-RUNS the same check and it now passes — confirm-green,
         proving the fix actually resolves the reported failure end-to-end.

    Business outcome to assert here: the SAME sandboxed check transitions from
    RED (step 2) to GREEN (step 4) across the real edit — i.e. the user's script
    is demonstrably fixed by running it, not just by inspecting the diff.
    """
    pytest.skip("E11 execute_code not shipped")
