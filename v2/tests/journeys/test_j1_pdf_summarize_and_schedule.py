"""J1 JOURNEY — "Summarize a long PDF and schedule a follow-up" (PRD §3, J1).

The business requirement, verbatim from ``_bmad-output/planning-artifacts/
prd-tool-expansion.md`` §3:

  > **J1 — Summarize a long PDF and schedule a follow-up.** *"Summarize this
  > 200-page contract and remind me to review the indemnity clause Friday."* →
  > ``pdf`` (extract) → summarize → ``cronjob`` (Friday reminder job, persisted)
  > → ``send_message`` (Telegram confirmation). Result returned inline; job
  > persisted under the home dir.

This is NOT a per-tool smoke. It proves the USER's END-TO-END OUTCOME across
several tools/epics, driving a single real inbound Telegram message through the
GENUINE path (TelegramChannelAdapter → GatewayScanner → AsyncioBackend pipeline →
execute._dispatch → ToolRegistry) and mocking ONLY the AI provider. The secretary
owl's scripted provider drives the real tool loop within one turn:

    pdf(extract real fixture)  →  compose a summary that QUOTES the real PDF text
                               →  cronjob(create a Friday reminder, persisted)
                               →  send_message(consent-gated Telegram confirmation)
                               →  return the summary as the final inline reply.

REAL (everything except the AI): the migrated ``DbPool`` (tmp_db), the whole
pipeline, the ``ToolRegistry`` + ``PdfTool``/``CronjobTool``/``SendMessageTool``,
the real ``JobScheduler`` + ``jobs`` table, the real consent chokepoint
(``ConsequentialActionGate`` → ``ConsentPolicy`` → ``RoutingPrompter`` →
``TelegramConsentPrompter`` → ``CallbackRouter``), the real ``ProactiveDeliverer``
+ ``NotificationRouter`` + ``ChannelRegistry`` singleton, and the Telegram
adapter's inbound + outbound transport. A REAL pypdf-written PDF fixture carries a
known "indemnity clause" sentence so the summary is checkable against actual
content. FAKED: ONLY the AI provider (scripted, owl-aware) and the Telegram bot
HTTP transport (``_FakeBot`` captures outbound in-process).

Business-outcome assertions (NOT tool return-shapes):
  1. The summary delivered to the user's Telegram chat contains text DERIVED FROM
     the REAL PDF content — proving pdf→summary genuinely happened, not a canned
     string.
  2. The Friday reminder cronjob is persisted in tmp_db AND a FRESH
     ``JobScheduler(db=tmp_db).list_jobs()`` (as if the process restarted) still
     returns it — the reminder will actually fire after a restart.
  3. The Telegram confirmation reaches the user's chat (real consent YES tap →
     real ProactiveDeliverer transports the body to the wire).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from stackowl.audit.logger import AuditLogger
from stackowl.channels.registry import ChannelRegistry
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.callbacks import CallbackRouter
from stackowl.channels.telegram.consent import TelegramConsentPrompter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.config.notification_settings import NotificationSettings
from stackowl.config.settings import Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner
from stackowl.notifications.deliverer import ProactiveDeliverer
from stackowl.notifications.router import NotificationRouter
from stackowl.owls.registry import OwlRegistry
from stackowl.paths import StackowlHome
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.scheduler.scheduler import JobScheduler
from stackowl.tools.consent import ConsentPolicy, RoutingPrompter
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

USER_ID = 313131

# The known sentence baked into the REAL PDF fixture. The agent's summary quotes a
# slice of it; the slice is what assertion (1) checks reached the user's chat —
# proving the summary was DERIVED from real extracted PDF content, not canned.
# The known clause baked into the REAL PDF fixture, written one logical line per
# entry so each survives intact through pypdf's line-wise extraction.
_INDEMNITY_LINES = (
    "CONTRACT - SECTION 7: INDEMNIFICATION",
    "The indemnity clause requires the supplier to",
    "hold the buyer harmless against all third-party",
    "intellectual property claims.",
)
# A distinctive phrase that only exists because pypdf actually extracted the PDF;
# it sits entirely on one fixture line, so it appears verbatim in the extraction.
_PDF_QUOTE = "hold the buyer harmless against all third-party"

_FRIDAY_SCHEDULE = "0 9 * * 5"  # 09:00 every Friday (cron weekday 5)
_REMINDER_GOAL = "Remind me to review the indemnity clause in the contract"


# --- FAKED #1: the Telegram bot HTTP transport (captures outbound in-process) ---


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.answered: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):  # noqa: ANN001
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def answer_callback_query(self, callback_id, text=None):  # noqa: ANN001
        self.answered.append(callback_id)


class _FakeBotApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot

    def add_handler(self, handler: object) -> None:
        pass


# --- FAKED #2 (THE ONLY AI MOCK): the secretary owl's scripted provider ---------


class _ScriptedSecretary:
    """The ONLY mock: stands in for the secretary owl's LLM.

    Within a SINGLE ``complete_with_tools`` call it drives the REAL tool loop via
    the REAL ``tool_dispatcher``, exactly as a real model would:

      1. pdf  — extract the real fixture (real pypdf, Mode A).
      2. compose a summary that QUOTES the extracted text (proves pdf→summary).
      3. cronjob — schedule the Friday reminder (real JobScheduler → jobs table).
      4. send_message — consent-gated Telegram confirmation (real deliverer).

    Returns the summary as the final inline reply, which the adapter delivers to
    the user. Records each tool's structured output for diagnostics only — the
    assertions check user-visible OUTCOMES, not these records.
    """

    protocol = "anthropic"

    def __init__(self, pdf_path: str) -> None:
        self._pdf_path = pdf_path
        self.pdf_out: str = ""
        self.cron_out: str = ""
        self.send_out: str = ""
        self.final: str = ""

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas, tool_dispatcher, history=None):  # noqa: ANN001
        calls: list[dict] = []

        # 1. Extract the REAL PDF (real pypdf text extraction, Mode A).
        self.pdf_out = await tool_dispatcher("pdf", {"path": self._pdf_path})
        calls.append({"name": "pdf", "args": {"path": self._pdf_path}, "result": self.pdf_out})

        # 2. Compose a summary DERIVED from the extracted text. A real model would
        #    paraphrase; here we quote a verbatim slice of what pypdf actually
        #    returned, so the test can prove the summary came from real content.
        assert _PDF_QUOTE in self.pdf_out, (
            "pdf tool did not return the fixture's text — the summary cannot be "
            f"derived from real content. pdf output was: {self.pdf_out!r}"
        )
        # Derive the quoted fragment FROM the real extracted text (a slice of
        # self.pdf_out), NOT from a constant — so the delivered summary can only
        # contain the fixture's words if pypdf actually extracted them. This keeps
        # the pdf->summary coupling in the data flow (survives `python -O`, which
        # strips the bare assert above), so the test can never false-pass on a
        # broken pdf tool.
        _idx = self.pdf_out.find(_PDF_QUOTE)
        extracted_fragment = self.pdf_out[_idx : _idx + len(_PDF_QUOTE)] if _idx != -1 else ""
        summary = (
            "Summary of the contract: the key obligation to review is the "
            f"indemnity clause — the supplier must {extracted_fragment} parties. "
            "I have scheduled a reminder for Friday."
        )

        # 3. Schedule the Friday reminder (real scheduler persists a goal_execution job).
        cron_args = {"action": "create", "prompt": _REMINDER_GOAL, "schedule": _FRIDAY_SCHEDULE}
        self.cron_out = await tool_dispatcher("cronjob", cron_args)
        calls.append({"name": "cronjob", "args": cron_args, "result": self.cron_out})

        # 4. Confirm over Telegram (consequential → real consent gate fires first).
        send_args = {
            "action": "send",
            "text": "Done — I summarised the contract and set a Friday reminder to review the indemnity clause.",
            "target": "telegram",
        }
        self.send_out = await tool_dispatcher("send_message", send_args)
        calls.append({"name": "send_message", "args": send_args, "result": self.send_out})

        self.final = summary
        return (summary, calls)

    async def complete(self, *a, **k):  # pragma: no cover — not on this path
        return ""

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


def _settings() -> Settings:
    # quiet_hours disabled; default_channel telegram so an omitted channel routes here.
    return cast(
        Settings,
        SimpleNamespace(notifications=NotificationSettings(default_channel="telegram")),
    )


@dataclass
class _Env:
    adapter: TelegramChannelAdapter
    bot: _FakeBot
    scanner: GatewayScanner
    backend: AsyncioBackend
    stream_registry: StreamRegistry
    callback_router: CallbackRouter
    provider: _ScriptedSecretary


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _clean_registry():  # noqa: ANN202
    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


def _cd_for(markup, scope: str) -> str:  # noqa: ANN001
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data.endswith(f":{scope}"):
                return btn.callback_data
    raise AssertionError(f"no {scope} button in consent keyboard")


async def _tap(env: _Env, scope: str) -> None:
    for _ in range(250):
        kb = [m for m in env.bot.messages if m["reply_markup"] is not None]
        if kb:
            cd = _cd_for(kb[-1]["reply_markup"], scope)
            update = SimpleNamespace(
                callback_query=SimpleNamespace(id=f"cb-{len(env.bot.answered)}", data=cd)
            )
            await env.callback_router.route(update, None)
            return
        await asyncio.sleep(0.02)
    raise AssertionError("consent prompt never appeared on Telegram")


async def _turn(env: _Env, text: str, *, tap: str) -> None:
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
    run_task = asyncio.create_task(env.backend.run(state))
    out_task = asyncio.create_task(env.adapter.send(reader))
    # The send_message consent prompt appears mid-loop; tap YES so the tool proceeds.
    await _tap(env, tap)
    await run_task
    await out_task
    env.stream_registry.remove(msg.session_id)


def _write_real_pdf(path: Path, lines: tuple[str, ...]) -> None:
    """Write a REAL, text-bearing PDF whose pages carry ``lines`` as extractable text.

    Hand-builds a minimal valid PDF-1.4 with a single ``BT ... ET`` text content
    stream (one ``Tj`` per line) so ``pypdf``'s ``extract_text()`` returns the
    exact lines. No third-party PDF generator is needed (none is installed), and
    the result is a genuine PDF the real ``PdfTool`` extracts in Mode A.
    """

    def _esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    body = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for ln in lines:
        body.append(f"({_esc(ln)}) Tj")
        body.append("T*")
    body.append("ET")
    stream = "\n".join(body).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    count = len(objects) + 1
    out += f"xref\n0 {count}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n" + f"<< /Size {count} /Root 1 0 R >>\n".encode()
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    path.write_bytes(bytes(out))


async def _build(tmp_db: DbPool, pdf_path: Path) -> _Env:
    settings = _settings()

    # The SAME adapter drives inbound (the user's message + the consent prompt +
    # the YES tap) AND outbound (the final summary + the deliverer's confirmation).
    adapter = TelegramChannelAdapter(TelegramSettings(allowed_user_ids=frozenset({USER_ID})))
    bot = _FakeBot()
    adapter._bot_app = _FakeBotApp(bot)
    adapter._bot_user_id = 999
    adapter._bot_username = ""
    ChannelRegistry.instance().register(adapter)

    # REAL consent chokepoint, audit-backed over a tmp sqlite audit_log.
    audit_path = pdf_path.parent / "audit.db"
    conn = sqlite3.connect(audit_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log (audit_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "event_type TEXT NOT NULL, actor TEXT, target TEXT, timestamp REAL NOT NULL, "
        "details TEXT NOT NULL, integrity_hash TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    audit = AuditLogger(audit_path)
    routing = RoutingPrompter()
    prompter = TelegramConsentPrompter(adapter, timeout_seconds=5.0)
    routing.register("telegram", prompter)
    gate = ConsequentialActionGate(ConsentPolicy(prompter=routing, audit_logger=audit))
    router_cb = CallbackRouter(tmp_db, adapter)
    await router_cb.ensure_table()
    router_cb.register("consent:", prompter.handle_callback)
    adapter.attach_callback_router(router_cb)

    # REAL S0 transport chokepoint for send_message: NotificationRouter (writes
    # notification_log) + ProactiveDeliverer (resolves the adapter off the real
    # registry). Clock pinned to noon UTC, well outside any quiet window.
    notif_router = NotificationRouter(
        db=tmp_db, settings=settings,
        clock=lambda: datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
    )
    deliverer = ProactiveDeliverer(
        router=notif_router, registry=ChannelRegistry.instance(), settings=settings
    )

    provider = _ScriptedSecretary(str(pdf_path))
    services = StepServices(
        provider_registry=_FakeProviderRegistry(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),  # REAL pdf/cronjob/send_message
        consent_gate=gate,  # REAL Telegram-backed consent gate
        stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(),
        proactive_deliverer=deliverer,  # REAL deliverer on the S0 chokepoint
        db_pool=tmp_db,  # REAL migrated DbPool — scheduler + router write here
    )
    return _Env(
        adapter=adapter, bot=bot, scanner=GatewayScanner(owl_registry=None),
        backend=AsyncioBackend(services=services), stream_registry=services.stream_registry,  # type: ignore[arg-type]
        callback_router=router_cb, provider=provider,
    )


async def test_j1_summarize_pdf_and_schedule_reminder(
    tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Workspace under a tmp home so the pdf tool's path guard confines to it and
    # nothing is written inside the project dir.
    home = tmp_path / "home"
    ws = home / "workspace"
    ws.mkdir(parents=True)
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))

    pdf_path = ws / "contract.pdf"
    _write_real_pdf(pdf_path, _INDEMNITY_LINES)
    # Precondition: a REAL PDF that pypdf can extract the known sentence from.
    assert pdf_path.exists() and pdf_path.stat().st_size > 0

    env = await _build(tmp_db, pdf_path)

    # The user asks — AS THE USER, over Telegram — for the J1 journey, and taps
    # Approve (session scope) on the send_message confirmation consent prompt.
    await _turn(
        env,
        f"Summarize this contract {pdf_path} and remind me Friday to review the indemnity clause.",
        tap="session",
    )

    # ===================================================================
    # BUSINESS OUTCOME 1 — the user got a SUMMARY OF THE PDF, NOW, and it is
    # derived from the REAL PDF content (not a canned string). We assert the
    # extracted-quote slice landed in the user's Telegram chat.
    # ===================================================================
    delivered = "\n".join(m["text"] for m in env.bot.messages if m["chat_id"] == USER_ID)
    # The adapter MarkdownV2-escapes punctuation on the way out; assert on the
    # unescaped CORE words of the quote (no '.'), which survive escaping.
    quote_core = "hold the buyer harmless against all third"
    assert quote_core in delivered.replace("\\", ""), (
        "BUSINESS OUTCOME 1 FAIL: the PDF-derived summary did not reach the user's "
        f"chat. Delivered text was: {delivered!r}"
    )
    assert "indemnity" in delivered.lower(), (
        f"BUSINESS OUTCOME 1 FAIL: summary missing the indemnity subject. Got: {delivered!r}"
    )

    # ===================================================================
    # BUSINESS OUTCOME 2 — the reminder is GENUINELY scheduled: it persisted to
    # disk in tmp_db AND a FRESH JobScheduler (as if the process restarted) still
    # returns it, so it would actually fire after a restart.
    # ===================================================================
    rows = await tmp_db.fetch_all("SELECT job_id, handler_name, params FROM jobs", ())
    reminder_rows = [
        r for r in rows
        if json.loads(r["params"]).get("created_by") == "cronjob"
        and json.loads(r["params"]).get("goal") == _REMINDER_GOAL
    ]
    assert len(reminder_rows) == 1, (
        "BUSINESS OUTCOME 2 FAIL: the Friday reminder did not persist to the jobs "
        f"table. Rows were: {rows}"
    )
    persisted_job_id = reminder_rows[0]["job_id"]
    assert reminder_rows[0]["handler_name"] == "goal_execution", reminder_rows[0]

    # Restart proof: a brand-new scheduler over the same db still sees the job.
    fresh = JobScheduler(db=tmp_db)
    reloaded = await fresh.list_jobs()
    match = [j for j in reloaded if j.job_id == persisted_job_id]
    assert match, (
        "BUSINESS OUTCOME 2 FAIL: a FRESH JobScheduler did not reload the reminder "
        f"after a simulated restart. Reloaded job ids: {[j.job_id for j in reloaded]}"
    )
    reloaded_job = match[0]
    assert reloaded_job.handler_name == "goal_execution"
    assert reloaded_job.params["goal"] == _REMINDER_GOAL
    assert reloaded_job.schedule == _FRIDAY_SCHEDULE  # the Friday cadence survives reload
    assert reloaded_job.enabled is True  # it WOULD fire (not paused)

    # ===================================================================
    # BUSINESS OUTCOME 3 — a confirmation reached the user. The real consent YES
    # tap let send_message run, and the REAL ProactiveDeliverer transported the
    # body to the user's chat (a keyboard-less message, distinct from the prompt).
    # ===================================================================
    confirm_msgs = [
        m for m in env.bot.messages
        if m["chat_id"] == USER_ID
        and m["reply_markup"] is None
        and "friday reminder" in m["text"].lower()
    ]
    assert confirm_msgs, (
        "BUSINESS OUTCOME 3 FAIL: the Telegram confirmation never reached the user. "
        f"Outbound messages: {[m['text'] for m in env.bot.messages]}"
    )
    # The REAL router wrote a 'delivered' row to the REAL notification_log.
    log_rows = await tmp_db.fetch_all(
        "SELECT channel, delivery_status FROM notification_log", ()
    )
    assert any(
        r["channel"] == "telegram" and r["delivery_status"] == "delivered"
        for r in log_rows
    ), f"BUSINESS OUTCOME 3 FAIL: no 'delivered' notification_log row. Rows: {log_rows}"
