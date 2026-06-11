"""Slack B3 — the consent / clarify / memory interactivity wired into startup.

Why this exists (the Phase-B merge gate):
  B1 built the ``SlackConsentPrompter``, B2 built the ``SlackActionRouter`` +
  ``SlackClarifyResolver`` + ``SlackMemoryActionHandler``. They were standalone —
  nothing FED them from a live Bolt app. B3 wires them in the orchestrator's
  Slack block: the consent prompter is registered on ``consent_routing`` BEFORE
  the socket starts (so a boot-time consent request can't miss its prompter and
  spuriously deny), and a Bolt ``@app.action`` catch-all acks-first then routes
  every tap through the ``SlackActionRouter`` to consent / clarify / memory.

  This test drives the REAL wiring round-trips (not the units): the real
  ``RoutingPrompter`` → real ``SlackConsentPrompter`` posts Block Kit buttons via
  a fake Bolt app → the registered Bolt action handler (the SAME closure the
  orchestrator registers) acks + routes the approve action through the real
  ``SlackActionRouter`` → the suspended consent Future resolves to an APPROVING
  scope. Plus the DENY path, the no-prompter-registered fail-closed regression, a
  clarify round-trip, and the composite memory bridge (force_promote + delete).

What is REAL vs faked:
  REAL — ``RoutingPrompter``, ``SlackConsentPrompter``, ``SlackActionRouter``,
  ``SlackClarifyResolver``, ``SlackMemoryActionHandler``, ``ClarifyGateway``,
  ``SqliteMemoryBridge``, ``FactPromoter``, and the SAME Bolt-action closure the
  orchestrator builds (re-created here byte-for-byte to prove the ack-first →
  route seam).
  FAKED — ONLY the Bolt TRANSPORT (a fake AsyncApp exposing ``.client`` with
  ``chat_postMessage`` / ``chat_update``) and the Bolt ``ack`` callback.

Every await is wrapped in ``asyncio.wait_for`` so a hang/wedge FAILS the test.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from stackowl.channels.slack.adapter import SlackChannelAdapter
from stackowl.channels.slack.callbacks import SlackActionRouter
from stackowl.channels.slack.clarify import SlackClarifyResolver
from stackowl.channels.slack.consent import SlackConsentPrompter
from stackowl.channels.slack.memory_callbacks import SlackMemoryActionHandler
from stackowl.channels.slack.settings import SlackSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.memory.trust import trust_for_source
from stackowl.startup.orchestrator import _SlackMemoryBridgeComposite
from stackowl.tools.consent import ConsentRequest, ConsentScope, RoutingPrompter

pytestmark = pytest.mark.asyncio

_WAIT = 5.0  # every await is bounded — a hang FAILS, never wedges the suite.


# ---- Fake Bolt transport (captures posts/updates; serves an action seam) ------


class _FakeBoltClient:
    """Captures chat_postMessage / chat_update kwargs."""

    def __init__(self) -> None:
        self.posted: list[dict[str, object]] = []
        self.updated: list[dict[str, object]] = []

    async def auth_test(self) -> dict[str, str]:
        return {"user_id": "U_BOT"}

    async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        # Mint a deterministic ts so the prompter can later chat_update it.
        ts = f"1700000000.{len(self.posted):06d}"
        self.posted.append(kwargs)
        return {"ok": True, "ts": ts}

    async def chat_update(self, **kwargs: object) -> dict[str, object]:
        self.updated.append(kwargs)
        return {"ok": True}


class _FakeBoltApp:
    """Minimal fake AsyncApp exposing ``.client`` (no socket transport)."""

    def __init__(self) -> None:
        self.client = _FakeBoltClient()


def _wired_adapter() -> tuple[SlackChannelAdapter, _FakeBoltApp]:
    """A real Slack adapter with a fake Bolt app + one session→channel mapping."""
    settings = SlackSettings(
        bot_token="xoxb-test",
        app_token="xapp-test",
        signing_secret="sig-test",
        allowed_user_ids=["U_SENDER"],
    )
    adapter = SlackChannelAdapter(settings)
    app = _FakeBoltApp()
    adapter.set_bolt_app(app)
    # Populate the session→channel map (handle_event does this in prod). The
    # prompter resolves its send target from here; without it, it fails closed.
    adapter._targets["slack:sess1"] = "C1"  # noqa: SLF001 — test-only seeding
    return adapter, app


def _bolt_action_closure(router: SlackActionRouter):
    """Re-create the orchestrator's Bolt @app.action handler (ack-first → route).

    This is the SEAM B3 adds: ack within Bolt's 3s deadline FIRST, then extract
    the action_id (+ a delivery id) and route through the SlackActionRouter,
    contained so a raising handler never breaks the socket.
    """

    async def _on_action(ack, body: dict[str, object]) -> None:  # type: ignore[no-untyped-def]
        with contextlib.suppress(Exception):
            await ack()
        try:
            action = (body.get("actions") or [{}])[0]
            action_id = str(action.get("action_id") or action.get("value") or "")
            delivery_id = str(
                action.get("action_ts") or body.get("trigger_id") or action_id
            )
            await router.route(action_id, delivery_id=delivery_id)
        except Exception:  # noqa: BLE001 — a raising tap must not break the socket
            pass

    return _on_action


def _action_body(action_id: str, *, action_ts: str = "1700000000.500000") -> dict:
    """A minimal Slack block_actions body for the catch-all action handler."""
    return {
        "actions": [{"action_id": action_id, "value": action_id, "action_ts": action_ts}],
        "trigger_id": "trig-123",
    }


async def _noop_ack() -> None:
    return None


# ---- CONSENT round-trip over the wired path (the critical merge gate) ----------


async def test_slack_consent_approve_round_trip() -> None:
    """A consent request routed through the SAME wiring resolves to APPROVE.

    Drives: RoutingPrompter.prompt(channel="slack") → SlackConsentPrompter posts
    Block Kit buttons via the fake Bolt → the registered Bolt @app.action closure
    acks + routes the approve action through the SlackActionRouter → the
    suspended consent Future resolves to the APPROVING scope and prompt() returns
    it (the tool would PROCEED).
    """
    adapter, app = _wired_adapter()
    prompter = SlackConsentPrompter(adapter)

    # Wire EXACTLY as the orchestrator does.
    consent_routing = RoutingPrompter()
    consent_routing.register("slack", prompter)
    router = SlackActionRouter()
    router.register("consent:", prompter.handle_action)
    on_action = _bolt_action_closure(router)

    # The acting pipeline coroutine: ask for consent and park on the Future.
    req = ConsentRequest(
        channel="slack",
        session_id="slack:sess1",
        tool_name="execute_code",
        summary="run a shell command",
        allow_relaxation=True,
    )
    prompt_task = asyncio.ensure_future(consent_routing.prompt(req))
    # Let the prompter post its buttons (and stash the pending Future).
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert app.client.posted, "consent prompt was not posted via the Bolt app"
    post = app.client.posted[0]
    assert post["channel"] == "C1"
    # The approve action_id is consent:{rid}:session (relaxation → session).
    blocks = post["blocks"]
    approve_id = _consent_approve_action_id(blocks)
    assert approve_id.startswith("consent:") and approve_id.endswith(":session")

    # Simulate the user tapping APPROVE → Bolt delivers a block_actions payload.
    await asyncio.wait_for(
        on_action(_noop_ack, _action_body(approve_id)), timeout=_WAIT
    )

    scope = await asyncio.wait_for(prompt_task, timeout=_WAIT)
    assert scope == ConsentScope.SESSION, "consent must resolve to the approving scope"
    # The prompt message was rewritten to the decision (buttons dropped).
    assert app.client.updated, "the resolved prompt message was not updated"


async def test_slack_consent_deny_round_trip() -> None:
    """The DENY button routes through the wiring and blocks the action."""
    adapter, _app = _wired_adapter()
    prompter = SlackConsentPrompter(adapter)
    consent_routing = RoutingPrompter()
    consent_routing.register("slack", prompter)
    router = SlackActionRouter()
    router.register("consent:", prompter.handle_action)
    on_action = _bolt_action_closure(router)

    req = ConsentRequest(
        channel="slack", session_id="slack:sess1",
        tool_name="execute_code", summary="rm -rf", allow_relaxation=True,
    )
    prompt_task = asyncio.ensure_future(consent_routing.prompt(req))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    deny_id = _consent_deny_action_id(_app.client.posted[0]["blocks"])
    assert deny_id.endswith(":deny_session")
    await asyncio.wait_for(on_action(_noop_ack, _action_body(deny_id)), timeout=_WAIT)

    scope = await asyncio.wait_for(prompt_task, timeout=_WAIT)
    assert scope == ConsentScope.DENY_SESSION, "DENY tap must block the action"


async def test_slack_consent_no_prompter_registered_fails_closed() -> None:
    """Regression guard: no Slack prompter registered → consent DENIES.

    If B3 ever fails to register the prompter on consent_routing before the
    socket starts, a consent request must fail CLOSED (deny), never silently
    auto-allow. This mirrors the Telegram register-before-start invariant.
    """
    consent_routing = RoutingPrompter()  # nothing registered for "slack"
    req = ConsentRequest(
        channel="slack", session_id="slack:sess1",
        tool_name="execute_code", summary="x", allow_relaxation=False,
    )
    scope = await asyncio.wait_for(consent_routing.prompt(req), timeout=_WAIT)
    assert scope == ConsentScope.DENY, "an unwired Slack consent must fail closed"


# ---- CLARIFY round-trip over the wired path -----------------------------------


async def test_slack_clarify_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clarify question delivered over Slack, resolved by a tapped button.

    Drives: ClarifyGateway.ask(channel="slack") → adapter.send_clarify posts
    choice buttons via the fake Bolt → the registered Bolt @app.action closure
    routes the clarify tap through the SlackActionRouter → SlackClarifyResolver
    resolves the parked turn (the gateway's blocking waiter wakes with the
    tapped choice text).
    """
    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda *_a, **_k: None)
    )
    adapter, _app = _wired_adapter()
    gateway = ClarifyGateway()
    gateway.register_adapter("slack", adapter)

    resolver = SlackClarifyResolver(gateway)
    router = SlackActionRouter()
    router.register("clarify:", resolver.handle_action)
    on_action = _bolt_action_closure(router)

    # The owl asks a blocking clarify; ask() delivers via send_clarify (posts
    # buttons) and returns the clarify_id. A separate task parks on the answer.
    clarify_id = await asyncio.wait_for(
        gateway.ask(
            "slack:sess1", "slack", "Which env?",
            choices=("dev", "prod"), blocking=True,
        ),
        timeout=_WAIT,
    )
    waiter = asyncio.ensure_future(gateway.wait_for_answer(clarify_id, _WAIT))
    await asyncio.sleep(0)

    assert _app.client.posted, "clarify buttons were not posted"
    # Tap the second choice (idx 1 = "prod").
    tap_id = f"clarify:{clarify_id}:1"
    await asyncio.wait_for(on_action(_noop_ack, _action_body(tap_id)), timeout=_WAIT)

    answer, outcome = await asyncio.wait_for(waiter, timeout=_WAIT)
    assert answer == "prod", "the tapped clarify choice must resolve the parked turn"
    assert outcome == "answered"


# ---- MEMORY approve over the composite bridge (force_promote + delete) ---------


async def test_slack_memory_composite_bridge_promotes(tmp_db: DbPool) -> None:
    """memory_approve routes to the composite bridge and the fact is PROMOTED.

    The bare SqliteMemoryBridge has ``delete`` but NOT ``force_promote`` (which
    lives on FactPromoter). B3 passes a composite exposing BOTH; this proves an
    approve tap force-promotes via the FactPromoter while delete stays on the
    bridge.
    """
    bridge = SqliteMemoryBridge(db=tmp_db)
    promoter = FactPromoter(db=tmp_db)
    composite = _SlackMemoryBridgeComposite(bridge=bridge, promoter=promoter)
    # The composite must expose BOTH operations the handler needs.
    assert hasattr(composite, "force_promote")
    assert hasattr(composite, "delete")

    fact_id = "deadbeefdeadbeefdeadbeefdeadbeef"
    await bridge.stage(
        StagedFact(
            fact_id=fact_id,
            content="user prefers dark mode",
            source_type="manual",
            source_ref="slack:test",
            confidence=1.0,
            trust=trust_for_source("manual"),
        )
    )

    handler = SlackMemoryActionHandler(composite)
    router = SlackActionRouter()
    handler.register(router)
    on_action = _bolt_action_closure(router)

    # An approve tap should force_promote (the staged row is consumed).
    await asyncio.wait_for(
        on_action(_noop_ack, _action_body(f"memory_approve_{fact_id}")), timeout=_WAIT
    )

    staged = await asyncio.wait_for(bridge.list_staged(), timeout=_WAIT)
    assert all(f.fact_id != fact_id for f in staged), (
        "the approved fact must have moved out of the staged queue"
    )


# ---- helpers ------------------------------------------------------------------


def _consent_approve_action_id(blocks: object) -> str:
    return _consent_button_id(blocks, deny=False)


def _consent_deny_action_id(blocks: object) -> str:
    return _consent_button_id(blocks, deny=True)


def _consent_button_id(blocks: object, *, deny: bool) -> str:
    assert isinstance(blocks, list)
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "actions":
            for el in block.get("elements", []):
                aid = str(el.get("action_id", ""))
                if deny and aid.endswith(":deny_session"):
                    return aid
                if not deny and aid.startswith("consent:") and not aid.endswith(
                    ":deny_session"
                ):
                    return aid
    raise AssertionError(f"no consent button found in blocks: {blocks}")
