"""Slack B1 — SlackConsentPrompter Block Kit consent round-trip.

Mirrors the Telegram consent prompter exactly in behavior, with the
Slack-specific target resolution: the destination channel is resolved from
``adapter.target_for_session(session_id)`` (the ``slack:{hash}`` session_id is
NOT itself a send target). Fail-closed (DENY) on: unresolved target, send
failure, timeout, unknown scope, any exception.

The acting coroutine calls :meth:`SlackConsentPrompter.prompt`, which posts an
Approve/Deny Block Kit message and suspends on a Future; a separate
``block_actions`` handler calls :meth:`handle_action` to resolve it.
"""

from __future__ import annotations

import asyncio

from stackowl.channels.slack.consent import SlackConsentPrompter
from stackowl.tools.consent import ConsentRequest, ConsentScope


class _FakeClient:
    """Captures chat_postMessage / chat_update; can be made to fail on post."""

    def __init__(
        self, *, post_fails: bool = False, update_raises: bool = False, ts: str = "111.222"
    ) -> None:
        self.posts: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []
        self._post_fails = post_fails
        self._update_raises = update_raises
        self._ts = ts
        self.posted_event = asyncio.Event()

    async def chat_postMessage(self, **kwargs: object) -> dict[str, object]:
        if self._post_fails:
            raise RuntimeError("slack transport down")
        self.posts.append(kwargs)
        self.posted_event.set()
        return {"ok": True, "ts": self._ts, "channel": kwargs.get("channel")}

    async def chat_update(self, **kwargs: object) -> dict[str, object]:
        if self._update_raises:
            raise RuntimeError("update transport down")
        self.updates.append(kwargs)
        return {"ok": True}


class _FakeApp:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client


class _FakeAdapter:
    """Minimal stand-in exposing target_for_session + the bolt app/client."""

    def __init__(
        self,
        *,
        target: str | None = "C123",
        thread_ts: str | None = None,
        post_fails: bool = False,
        update_raises: bool = False,
    ) -> None:
        self._target = target
        self.client = _FakeClient(post_fails=post_fails, update_raises=update_raises)
        self._app = _FakeApp(self.client)
        self.posted_event = self.client.posted_event
        # Mirror the real adapter: per-channel thread map keyed by channel id.
        self._threads: dict[str, str] = {}
        if target is not None and thread_ts is not None:
            self._threads[target] = thread_ts

    def target_for_session(self, session_id: str) -> str | None:
        return self._target


def _blocks_of(post: dict[str, object]) -> list[dict]:
    blocks = post.get("blocks")
    assert isinstance(blocks, list)
    return blocks


def _buttons(post: dict[str, object]) -> list[dict]:
    out: list[dict] = []
    for block in _blocks_of(post):
        if block.get("type") == "actions":
            out.extend(block.get("elements", []))
    return out


def _action_for(post: dict[str, object], scope: str) -> str:
    for btn in _buttons(post):
        val = str(btn.get("action_id", ""))
        if val.endswith(f":{scope}"):
            return val
    raise AssertionError(f"no button for scope {scope} in {post}")


def _req(allow_relaxation: bool = True) -> ConsentRequest:
    return ConsentRequest(
        tool_name="danger",
        channel="slack",
        session_id="slack:abcd1234",
        summary="run the dangerous thing",
        allow_relaxation=allow_relaxation,
    )


# ---------------------------------------------------------------------------
# Round-trip: approve / deny resolve the Future
# ---------------------------------------------------------------------------


async def test_approve_resolves_session_when_relaxation_allowed() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.posted_event.wait()
        await prompter.handle_action(_action_for(adapter.client.posts[0], "session"))

    results = await asyncio.gather(
        asyncio.wait_for(prompter.prompt(_req()), timeout=5.0), _resolve()
    )
    assert results[0] is ConsentScope.SESSION


async def test_approve_resolves_once_for_excluded_tool() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.posted_event.wait()
        await prompter.handle_action(_action_for(adapter.client.posts[0], "once"))

    results = await asyncio.gather(
        asyncio.wait_for(prompter.prompt(_req(allow_relaxation=False)), timeout=5.0),
        _resolve(),
    )
    assert results[0] is ConsentScope.ONCE


async def test_deny_resolves_deny_session() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.posted_event.wait()
        await prompter.handle_action(_action_for(adapter.client.posts[0], "deny_session"))

    results = await asyncio.gather(
        asyncio.wait_for(prompter.prompt(_req()), timeout=5.0), _resolve()
    )
    assert results[0] is ConsentScope.DENY_SESSION


# ---------------------------------------------------------------------------
# Fail-closed paths → DENY
# ---------------------------------------------------------------------------


async def test_timeout_denies() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=0.05)
    assert await asyncio.wait_for(prompter.prompt(_req()), timeout=5.0) is ConsentScope.DENY


async def test_send_failure_denies() -> None:
    adapter = _FakeAdapter(post_fails=True)
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)
    assert await asyncio.wait_for(prompter.prompt(_req()), timeout=5.0) is ConsentScope.DENY


async def test_unresolved_target_denies_without_posting() -> None:
    """Slack-specific fail-closed: target_for_session returns None → DENY, no post."""
    adapter = _FakeAdapter(target=None)
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)
    assert await asyncio.wait_for(prompter.prompt(_req()), timeout=5.0) is ConsentScope.DENY
    assert adapter.client.posts == []  # never attempted a send


async def test_unknown_scope_in_action_denies() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.posted_event.wait()
        # find the rid, then send a bogus scope
        action = adapter.client.posts[0]
        rid = _action_for(action, "session").split(":")[1]
        await prompter.handle_action(f"consent:{rid}:bogus")

    results = await asyncio.gather(
        asyncio.wait_for(prompter.prompt(_req()), timeout=5.0), _resolve()
    )
    assert results[0] is ConsentScope.DENY


# ---------------------------------------------------------------------------
# Keyboard / Block Kit shape
# ---------------------------------------------------------------------------


async def test_exactly_two_buttons_session_mapping() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=0.05)
    await asyncio.wait_for(prompter.prompt(_req(allow_relaxation=True)), timeout=5.0)
    btns = _buttons(adapter.client.posts[0])
    assert len(btns) == 2
    ids = [str(b["action_id"]) for b in btns]
    assert ids[0].endswith(":session")
    assert ids[1].endswith(":deny_session")


async def test_exactly_two_buttons_once_mapping() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=0.05)
    await asyncio.wait_for(prompter.prompt(_req(allow_relaxation=False)), timeout=5.0)
    btns = _buttons(adapter.client.posts[0])
    assert len(btns) == 2
    ids = [str(b["action_id"]) for b in btns]
    assert ids[0].endswith(":once")
    assert ids[1].endswith(":deny_session")
    assert not any(i.endswith(":window") for i in ids)


async def test_post_targets_resolved_channel() -> None:
    adapter = _FakeAdapter(target="C-RESOLVED")
    prompter = SlackConsentPrompter(adapter, timeout_seconds=0.05)
    await asyncio.wait_for(prompter.prompt(_req()), timeout=5.0)
    assert adapter.client.posts[0]["channel"] == "C-RESOLVED"


async def test_post_threads_under_session_thread() -> None:
    adapter = _FakeAdapter(target="C1", thread_ts="999.000")
    prompter = SlackConsentPrompter(adapter, timeout_seconds=0.05)
    await asyncio.wait_for(prompter.prompt(_req()), timeout=5.0)
    assert adapter.client.posts[0].get("thread_ts") == "999.000"


async def test_request_ids_unique_and_non_sequential() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=0.02)
    await asyncio.wait_for(prompter.prompt(_req()), timeout=5.0)
    await asyncio.wait_for(prompter.prompt(_req()), timeout=5.0)
    rids = [str(_buttons(p)[0]["action_id"]).split(":")[1] for p in adapter.client.posts]
    assert len(set(rids)) == 2
    assert not all(r.isdigit() and len(r) <= 2 for r in rids)


# ---------------------------------------------------------------------------
# handle_action edge cases
# ---------------------------------------------------------------------------


async def test_action_for_unknown_request_is_ignored() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)
    # never raises for a rid that was never issued
    await prompter.handle_action("consent:999:once")


async def test_malformed_action_does_not_resolve() -> None:
    adapter = _FakeAdapter()
    prompter = SlackConsentPrompter(adapter, timeout_seconds=0.05)
    await prompter.handle_action("garbage")
    assert await asyncio.wait_for(prompter.prompt(_req()), timeout=5.0) is ConsentScope.DENY


async def test_action_resolves_then_updates_message() -> None:
    """On tap, the message is rewritten to the decision and the buttons dropped."""
    adapter = _FakeAdapter(target="C9")
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.posted_event.wait()
        await prompter.handle_action(_action_for(adapter.client.posts[0], "session"))

    results = await asyncio.gather(
        asyncio.wait_for(prompter.prompt(_req()), timeout=5.0), _resolve()
    )
    assert results[0] is ConsentScope.SESSION
    assert len(adapter.client.updates) == 1
    upd = adapter.client.updates[0]
    assert upd["channel"] == "C9"
    assert upd["ts"] == adapter.client._ts
    # buttons dropped: no actions block in the update
    blocks = upd.get("blocks")
    if isinstance(blocks, list):
        assert not any(b.get("type") == "actions" for b in blocks)
    # original action summary preserved in the rewritten text/section
    rendered = str(upd.get("text", "")) + str(upd.get("blocks", ""))
    assert "run the dangerous thing" in rendered


async def test_update_failure_does_not_lose_decision() -> None:
    """Fail-open UX: if chat_update RAISES, the consent decision is still returned."""
    adapter = _FakeAdapter(update_raises=True)
    prompter = SlackConsentPrompter(adapter, timeout_seconds=5.0)

    async def _resolve() -> None:
        await adapter.posted_event.wait()
        await prompter.handle_action(_action_for(adapter.client.posts[0], "once"))

    results = await asyncio.gather(
        asyncio.wait_for(prompter.prompt(_req(allow_relaxation=False)), timeout=5.0),
        _resolve(),
    )
    assert results[0] is ConsentScope.ONCE
    assert adapter.client.updates == []  # update raised, recorded nothing
