# Slash-command Plan B: /webhook Real CRUD + Live Wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/webhook register`/`disable` real config mutations (matching `/provider add`'s pattern) instead of instruction-printers, add live-reload for the sources dict, and fix the root defect discovered while planning this: `WebhookReceiver` is never constructed or started anywhere — the HTTP listener is completely dead today regardless of config.

**Architecture:** `WebhookReceiver` (`webhooks/receiver.py`) is a correct, tested `SupervisedTask` — it just has no caller. `scheduler_components.supervisor` (built in `scheduler/assembly.py`, started at `startup/orchestrator.py:~3187`) is the existing app-wide task supervisor already used for browser/dream-worker/notification-digest jobs — wiring the receiver into it (gated on `settings.webhook.enabled`) is the minimal fix, no new subsystem. Once running, `WebhookCommand.register`/`disable` write real config (reusing `/provider add`'s `config_helpers`/`store_secret` pattern) and emit a real `Settings()` via `settings_reloaded` (per Plan A's mechanism) — a new subscriber (`webhook_reload.py`, mirroring `provider_reload.py`) applies it to the running receiver in place.

**Tech Stack:** Python 3.13, aiohttp (webhook HTTP server), pydantic-settings, pytest + pytest-asyncio.

**Depends on:** Plan A (real-Settings-emit mechanism must exist before this plan's reload subscriber has anything meaningful to consume — Plan A's Task 1/2 pattern is reused verbatim here).

## Global Constraints

- Run tests with `uv run pytest <path>` — never the full suite (hangs on this box).
- `uv run ruff check src/` and `uv run mypy src/` clean on touched files.
- Never log a raw secret, generated token, or webhook shared secret — only service names / ref strings (this repo's sensitive-data rule).
- 4-point logging (entry/decision/step/exit) on every modified `execute()`/`handle()`-style method and the new `WebhookReceiver.apply_settings`.
- A raw token/secret is never written to YAML in plaintext — only the `store_secret()` resolver ref (`keychain:...` / `file:...`), same rule `/provider add` already follows.

---

### Task 1: Wire `WebhookReceiver` into the running process (root fix)

**Files:**
- Modify: `src/stackowl/startup/orchestrator.py` (near `scheduler_components.supervisor.start()`, `~line 3183-3189`)
- Test: new `tests/startup/test_webhook_receiver_wiring.py`

**Interfaces:**
- Consumes: `stackowl.webhooks.receiver.WebhookReceiver(scheduler: JobScheduler, settings: Settings, db: DbPool | None)`, `scheduler_components.scheduler: JobScheduler`, `scheduler_components.supervisor: Supervisor`, `Supervisor.register(task: SupervisedTask) -> None` (must be called before `.start()`).
- Produces: a `webhook_receiver: WebhookReceiver | None` local in `orchestrator.py`'s startup method — `None` when `settings.webhook.enabled` is `False`, else the constructed+registered instance. Task 2 needs this reference to wire the reload subscriber.

- [ ] **Step 1: Write the failing test**

```python
# tests/startup/test_webhook_receiver_wiring.py
"""WebhookReceiver must actually be registered with the app supervisor when
webhook.enabled is True — today NOTHING in the codebase ever constructs it,
so the HTTP listener never binds regardless of config (registered-but-
unreachable, the same class of bug this repo's memory notes call out
repeatedly)."""
from __future__ import annotations

from stackowl.supervisor.supervisor import Supervisor
from stackowl.webhooks.receiver import WebhookReceiver


def test_webhook_receiver_registers_on_supervisor_when_enabled():
    """Minimal, direct test of the registration contract this task adds —
    construct a receiver the same way orchestrator.py will, register it, and
    confirm the supervisor now holds it. This does NOT exercise the full
    orchestrator startup path (too many fixtures for one unit test); that is
    covered by the manual smoke check in Task 5."""
    from unittest.mock import MagicMock

    from tests._story_6_7_helpers import make_settings

    settings = make_settings(webhook={"enabled": True, "sources": {}})
    supervisor = Supervisor()
    receiver = WebhookReceiver(scheduler=MagicMock(), settings=settings, db=None)

    supervisor.register(receiver)

    assert any(
        isinstance(state.task, WebhookReceiver) for state in supervisor._tasks.values()
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/startup/test_webhook_receiver_wiring.py -v`
Expected: FAIL only if `make_settings()`'s `webhook` kwarg shape doesn't match — check `tests/_story_6_7_helpers.py::make_settings`'s actual signature first and adjust the call to match (it may take a full `Settings(**overrides)` merge rather than a `webhook=` kwarg directly). The assertion itself (registration + `isinstance` check) is what this task is actually testing, once the fixture call is correct it should pass immediately since `Supervisor.register` already exists and works — this test exists to lock in the *pattern* the orchestrator wiring in Step 3 must follow, not to fail first. If it passes on the first run, that's fine — proceed straight to Step 3 (this is one of the rare "test documents an existing correct building block" cases, not a red-green pair for new production code).

- [ ] **Step 3: Implement the orchestrator wiring**

In `src/stackowl/startup/orchestrator.py`, immediately before the existing block:

```python
        scheduler_task: asyncio.Task[None] | None = None
        if self._role != "gateway":
            scheduler_task = asyncio.create_task(
                scheduler_components.supervisor.start()
            )
```

insert:

```python
        # F145 — the webhook HTTP receiver was a fully-built SupervisedTask with
        # no caller anywhere in the codebase: registered-but-unreachable (the
        # listener never bound, regardless of `webhook.enabled`). Wired into the
        # SAME app supervisor already used for browser/dream-worker/notification
        # jobs — no new subsystem. GATEWAY skips this for the same reason it
        # skips the scheduler supervisor: the core owns the running instance.
        webhook_receiver = None
        if self._role != "gateway" and self._settings.webhook.enabled:
            from stackowl.webhooks.receiver import WebhookReceiver

            webhook_receiver = WebhookReceiver(
                scheduler=scheduler_components.scheduler,
                settings=self._settings,
                db=db_pool,
            )
            scheduler_components.supervisor.register(webhook_receiver)
            log.info(
                "[startup] gateway: webhook receiver registered",
                extra={"_fields": {"port": self._settings.webhook.port}},
            )
```

Then the existing `scheduler_task = asyncio.create_task(scheduler_components.supervisor.start())` line follows unchanged — `register()` must run before `.start()` per `Supervisor`'s own contract ("Must be called before start()"). Confirm `db_pool` is the correct in-scope variable name at this point in the file (it is referenced a few lines above this insertion point in the durable-task-recovery block already read during planning).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/startup/test_webhook_receiver_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Run the broader startup test surface**

Run: `uv run pytest tests/startup/ -v -k "webhook or scheduler_components or orchestrator"`
Expected: all PASS — confirm this insertion didn't break the `gateway`-role skip path (check for an existing test asserting `scheduler_task is None` when `role == "gateway"`; if none exists, that's a pre-existing gap, not this task's concern).

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/startup/orchestrator.py tests/startup/test_webhook_receiver_wiring.py
git commit -m "fix(webhook): wire WebhookReceiver into the app supervisor — was never started"
```

---

### Task 2: Live-reload subscriber for the running receiver

**Files:**
- Create: `src/stackowl/startup/webhook_reload.py`
- Modify: `src/stackowl/webhooks/receiver.py` (add `apply_settings` method, promote `Settings` import out of `TYPE_CHECKING`)
- Modify: `src/stackowl/startup/orchestrator.py` (subscribe next to the provider/identity `settings_reloaded` subscriptions)
- Test: new `tests/startup/test_webhook_reload.py` (mirrors `tests/startup/test_provider_reload.py` if it exists — check first with `find tests -iname "*provider_reload*"`, follow its exact shape)

**Interfaces:**
- Consumes: `stackowl.config.settings.Settings` (real object, per Plan A).
- Produces: `make_webhook_reload_handler(receiver: WebhookReceiver) -> Callable[[Any], None]` — importable from `stackowl.startup.webhook_reload`, same shape as `make_settings_reload_handler` in `provider_reload.py`. `WebhookReceiver.apply_settings(settings: Settings) -> None` — new public method.

- [ ] **Step 1: Write the failing test**

```python
# tests/startup/test_webhook_reload.py
"""Mirrors tests/startup/test_provider_reload.py's shape for the analogous
webhook subscriber — type-guards on Settings, ignores dict payloads, never
raises out of the handler."""
from __future__ import annotations

from unittest.mock import MagicMock

from stackowl.startup.webhook_reload import make_webhook_reload_handler


def test_webhook_reload_applies_settings_payload():
    receiver = MagicMock()
    handler = make_webhook_reload_handler(receiver)

    from stackowl.config.settings import Settings
    settings = Settings()
    handler(settings)

    receiver.apply_settings.assert_called_once_with(settings)


def test_webhook_reload_ignores_dict_payload():
    receiver = MagicMock()
    handler = make_webhook_reload_handler(receiver)

    handler({"source": "acme"})

    receiver.apply_settings.assert_not_called()


def test_webhook_reload_never_raises_on_apply_error():
    receiver = MagicMock()
    receiver.apply_settings.side_effect = RuntimeError("boom")
    handler = make_webhook_reload_handler(receiver)

    from stackowl.config.settings import Settings
    handler(Settings())  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/startup/test_webhook_reload.py -v`
Expected: FAIL — `stackowl.startup.webhook_reload` doesn't exist yet.

- [ ] **Step 3: Implement `webhook_reload.py`**

```python
"""Reload handler that hot-applies a new Settings to the live WebhookReceiver.

Subscribed to the ``settings_reloaded`` event in the gateway lifecycle, same
shape as :mod:`stackowl.startup.provider_reload`. The event is emitted by TWO
producers with DIFFERENT payloads: :class:`stackowl.config.watcher.ConfigWatcher`
emits the new ``Settings`` object; the webhook/provider/config slash commands
(after Plan A) ALSO emit a real ``Settings`` object now — this handler simply
type-guards defensively in case a future producer still emits a dict.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stackowl.config.settings import Settings
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.webhooks.receiver import WebhookReceiver


def make_webhook_reload_handler(receiver: WebhookReceiver) -> Callable[[Any], None]:
    """Build the ``settings_reloaded`` handler bound to ``receiver``."""

    def _on_settings_reloaded(payload: Any) -> None:
        if not isinstance(payload, Settings):
            log.webhook.debug(
                "[webhook] reload: ignoring non-Settings payload",
                extra={"_fields": {"payload_type": type(payload).__name__}},
            )
            return
        try:
            receiver.apply_settings(payload)
        except Exception as exc:
            log.webhook.error(
                "[webhook] reload: applying settings failed",
                exc_info=exc,
            )

    return _on_settings_reloaded
```

Add to `webhooks/receiver.py` (near `stop()`):

```python
    def apply_settings(self, settings: Settings) -> None:
        """Hot-swap the sources dict the running receiver reads per-request.

        Only ``sources`` is genuinely hot-reload-capable (schema-declared in
        webhook_settings.py) — bind_address/port/the top-level enabled flag
        require a real restart to take effect (a brand-new listener bind),
        which this method does NOT attempt.
        """
        old_count = len(self._settings.webhook.sources)
        self._settings = settings
        log.webhook.info(
            "[webhook] receiver.apply_settings: sources refreshed",
            extra={
                "_fields": {
                    "old_count": old_count,
                    "new_count": len(settings.webhook.sources),
                }
            },
        )
```

`Settings` needs importing in `receiver.py` outside the `TYPE_CHECKING` block now (it's used at runtime, not just for annotations) — change the existing `if TYPE_CHECKING:` import of `Settings` to a top-level import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/startup/test_webhook_reload.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the subscription in orchestrator.py**

In the existing block:

```python
            event_bus.subscribe(
                "settings_reloaded", make_settings_reload_handler(provider_registry)
            )
            event_bus.subscribe(
                "settings_reloaded", make_identity_reload_handler(identity_resolver)
            )
```

add, right after (still inside the same `if self._settings.settings_watch:` guard, and only when a receiver actually exists):

```python
            if webhook_receiver is not None:
                from stackowl.startup.webhook_reload import make_webhook_reload_handler

                event_bus.subscribe(
                    "settings_reloaded", make_webhook_reload_handler(webhook_receiver)
                )
```

This requires `webhook_receiver` (from Task 1) to be defined *before* this `if self._settings.settings_watch:` block runs — confirm the ordering in the actual file (Task 1's insertion point is a few lines above this block per the orchestrator excerpt read during planning; if the real file has them in the opposite order, move Task 1's block above this one).

- [ ] **Step 6: Run the full startup test surface**

Run: `uv run pytest tests/startup/test_webhook_reload.py tests/startup/test_webhook_receiver_wiring.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/startup/webhook_reload.py src/stackowl/webhooks/receiver.py src/stackowl/startup/orchestrator.py tests/startup/test_webhook_reload.py
git commit -m "feat(webhook): live-reload subscriber, mirrors provider_reload.py"
```

---

### Task 3: `/webhook register` — real CRUD write

**Files:**
- Modify: `src/stackowl/commands/webhook_command.py` (`_WEBHOOK_META`, `_register`, imports, `__init__`)
- Modify: `src/stackowl/commands/assembly.py` (pass `event_bus` to `WebhookCommand`)
- Test: `tests/journeys/commands/test_webhook_command.py`, `tests/commands/test_webhook_meta.py`

**Interfaces:**
- Consumes: `stackowl.commands.config_helpers.{config_path, load_yaml, save_yaml}`, `stackowl.config.secret_writer.store_secret`, `stackowl.config.settings.Settings`, `stackowl.events.bus.EventBus` — same imports `/provider add` already uses.
- Produces: `WebhookCommand.__init__(db, settings, event_bus)` — new `event_bus` param. `WebhookCommand._register(source: str, extra_args: list[str], state: PipelineState) -> str` — signature changes from `_register(self, source: str, state: PipelineState)` to also take the remaining arg tokens (`timestamp_header=`/`delivery_id_header=`/`secret=`/`replay_tolerance_s=`), since it must now parse them instead of ignoring everything after `source`.

- [ ] **Step 1: Write the failing test**

Add to `tests/journeys/commands/test_webhook_command.py`:

```python
async def test_webhook_register_writes_real_config(tmp_path, monkeypatch, db):
    """/webhook register must write stackowl.yaml, not just print instructions."""
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setattr(
        "stackowl.commands.config_helpers.config_path", lambda: config_file
    )
    settings = make_settings()  # existing helper in this test file
    cmd = WebhookCommand(db=db, settings=settings)

    result = await cmd.handle(
        "register acme timestamp_header=X-Ts-Header", make_state()
    )

    assert "✓" in result
    from stackowl.commands.config_helpers import load_yaml
    data = load_yaml(config_file)
    assert data["webhook"]["enabled"] is True
    assert data["webhook"]["sources"]["acme"]["timestamp_header"] == "X-Ts-Header"
    assert data["webhook"]["sources"]["acme"]["secret"].startswith(("keychain:", "file:"))


async def test_webhook_register_requires_anti_replay_mechanism(db):
    """Neither timestamp_header nor delivery_id_header given → clear rejection,
    never a silent guess at a vendor-specific header name."""
    cmd = WebhookCommand(db=db, settings=make_settings())

    result = await cmd.handle("register acme", make_state())

    assert "✗" in result
    assert "timestamp_header" in result
    assert "delivery_id_header" in result


async def test_webhook_register_auto_generates_secret_and_shows_it_once(tmp_path, monkeypatch, db):
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setattr(
        "stackowl.commands.config_helpers.config_path", lambda: config_file
    )
    cmd = WebhookCommand(db=db, settings=make_settings())

    result = await cmd.handle(
        "register acme delivery_id_header=X-Delivery-Id", make_state()
    )

    assert "won't be shown again" in result.lower() or "shown once" in result.lower()
    from stackowl.commands.config_helpers import load_yaml
    data = load_yaml(config_file)
    ref = data["webhook"]["sources"]["acme"]["secret"]
    assert ref.startswith(("keychain:", "file:"))


async def test_webhook_register_with_supplied_secret_never_echoes_it(tmp_path, monkeypatch, db):
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setattr(
        "stackowl.commands.config_helpers.config_path", lambda: config_file
    )
    cmd = WebhookCommand(db=db, settings=make_settings())

    result = await cmd.handle(
        "register acme delivery_id_header=X-Delivery-Id secret=my-raw-secret-value",
        make_state(),
    )

    assert "my-raw-secret-value" not in result
```

(Use whatever `make_settings`/`make_state`/`db` fixtures this test file already defines or imports — it's in the same `tests/journeys/commands/` directory as `test_memory_delete_prefix.py`, which imports them from `tests._story_6_7_helpers`; follow that same pattern.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/journeys/commands/test_webhook_command.py -v -k register`
Expected: FAIL — `_register` today only ever returns the print-instructions string, never writes YAML.

- [ ] **Step 3: Implement**

Update imports in `webhook_command.py`:

```python
import secrets
from typing import Any

from stackowl.commands.config_helpers import config_path, load_yaml, save_yaml
from stackowl.config.secret_writer import store_secret
from stackowl.config.settings import Settings
from stackowl.events.bus import EventBus
```

Update `_WEBHOOK_META`'s `register` `SubCommand` args to document the new grammar:

```python
        SubCommand(
            name="register",
            summary="Register a new webhook source",
            description=(
                "You add a webhook source. At least one anti-replay mechanism "
                "(timestamp_header or delivery_id_header) is required — the "
                "sending service's docs will name its header. A shared secret is "
                "auto-generated and shown once if you don't supply one."
            ),
            args=(
                Arg(name="source", summary="webhook source name"),
                Arg(
                    name="timestamp_header=<H>",
                    required=False,
                    summary="signed-timestamp header name",
                ),
                Arg(
                    name="delivery_id_header=<H>",
                    required=False,
                    summary="delivery-id header name",
                ),
                Arg(name="secret=<RAW>", required=False, summary="shared secret (auto-generated if omitted)"),
                Arg(
                    name="replay_tolerance_s=<N>",
                    required=False,
                    summary="max signed-timestamp age, seconds (default 300)",
                ),
            ),
        ),
```

Update `__init__` to accept an event bus (needed for the live-reload emit, same as `/provider`):

```python
    def __init__(
        self,
        db: DbPool | None = None,
        settings: Settings | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._bus = event_bus
```

Replace the `sub == "register"` branch in `handle`:

```python
        if sub == "register":
            if not rest:
                return "webhook register: missing <source>\n\n" + usage
            return await self._register(rest[0], rest[1:], state)
```

Replace `_register` entirely:

```python
    async def _register(self, source: str, extra_args: list[str], state: PipelineState) -> str:
        log.webhook.info(
            "[webhook] command.register: entry",
            extra={"_fields": {"source": source, "extra_args_count": len(extra_args)}},
        )
        timestamp_header: str | None = None
        delivery_id_header: str | None = None
        raw_secret: str | None = None
        replay_tolerance_s = 300
        for token in extra_args:
            if token.startswith("timestamp_header="):
                timestamp_header = token[len("timestamp_header="):]
            elif token.startswith("delivery_id_header="):
                delivery_id_header = token[len("delivery_id_header="):]
            elif token.startswith("secret="):
                raw_secret = token[len("secret="):]
            elif token.startswith("replay_tolerance_s="):
                try:
                    replay_tolerance_s = int(token[len("replay_tolerance_s="):])
                except ValueError:
                    return f"✗ replay_tolerance_s must be an integer, got {token!r}"
            else:
                return f"✗ Unrecognized argument: {token!r}"

        if not timestamp_header and not delivery_id_header:
            return (
                "✗ webhook register requires an anti-replay mechanism — set "
                "timestamp_header=<H> (preferred, signed-timestamp window) or "
                "delivery_id_header=<H> (sender delivery-id). Check the sending "
                "service's docs for its header name — StackOwl cannot guess it."
            )

        path = config_path()
        data = load_yaml(path)
        webhook_cfg = data.setdefault("webhook", {})
        sources = webhook_cfg.setdefault("sources", {})
        was_already_enabled = bool(webhook_cfg.get("enabled", False)) and bool(sources)

        secret_shown_once: str | None = None
        if raw_secret is None:
            raw_secret = secrets.token_urlsafe(32)
            secret_shown_once = raw_secret
        _description, secret_ref = store_secret(f"stackowl-webhook-{source}", raw_secret)

        source_entry: dict[str, Any] = {
            "enabled": True,
            "secret": secret_ref,
            "replay_tolerance_s": replay_tolerance_s,
        }
        if timestamp_header:
            source_entry["timestamp_header"] = timestamp_header
        if delivery_id_header:
            source_entry["delivery_id_header"] = delivery_id_header

        sources[source] = source_entry
        webhook_cfg["enabled"] = True
        save_yaml(path, data)

        # F-81-style verified-persist re-read before claiming success.
        reloaded = load_yaml(path)
        if source not in reloaded.get("webhook", {}).get("sources", {}):
            log.webhook.error(
                "[webhook] command.register: write did not persist",
                extra={"_fields": {"source": source}},
            )
            return f"✗ Webhook '{source}' was not saved — check file permissions/disk."

        if self._bus is not None:
            try:
                self._bus.emit("settings_reloaded", Settings())
            except Exception as exc:
                log.webhook.error(
                    "[webhook] command.register: immediate reload failed",
                    exc_info=exc,
                    extra={"_fields": {"source": source}},
                )

        log.webhook.info(
            "[webhook] command.register: exit — registered",
            extra={"_fields": {"source": source, "first_source": not was_already_enabled}},
        )
        lines = [f"✓ Webhook '{source}' registered."]
        if was_already_enabled:
            lines.append("Live now — no restart needed.")
        else:
            lines.append(
                "This is the first webhook source — restart is required to "
                "start the listener."
            )
        if secret_shown_once:
            lines.append(
                f"Shared secret (save now, shown once): {secret_shown_once}"
            )
            lines.append("Give this to the sending service to sign requests.")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/journeys/commands/test_webhook_command.py -v -k register`
Expected: all PASS.

- [ ] **Step 5: Update `assembly.py`'s registration to pass `event_bus`**

In `src/stackowl/commands/assembly.py`, change:

```python
    _safe_register(registry, "webhook", lambda: WebhookCommand(db=deps.db, settings=deps.settings))
```

to:

```python
    _safe_register(
        registry,
        "webhook",
        lambda: WebhookCommand(db=deps.db, settings=deps.settings, event_bus=deps.event_bus),
    )
```

- [ ] **Step 6: Run the full webhook command test file**

Run: `uv run pytest tests/journeys/commands/test_webhook_command.py tests/commands/test_webhook_meta.py -v`
Expected: all PASS (update any pre-existing test in these files that asserted the OLD print-instructions behavior for `register` — check `tests/commands/test_webhook_meta.py` first since it likely tests the metadata/usage text, not behavior, and may need its `register` example/description text updated to match the new args).

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/commands/webhook_command.py src/stackowl/commands/assembly.py tests/journeys/commands/test_webhook_command.py tests/commands/test_webhook_meta.py
git commit -m "feat(webhook): /webhook register writes real config instead of printing instructions"
```

---

### Task 4: `/webhook disable` — real CRUD write

**Files:**
- Modify: `src/stackowl/commands/webhook_command.py` (`_disable`, module docstring)
- Test: `tests/journeys/commands/test_webhook_command.py`

**Interfaces:**
- Consumes: same `config_helpers`/`Settings` imports as Task 3 (already added).
- Produces: `WebhookCommand._disable(source: str, state: PipelineState) -> str` — same signature, now actually flips `sources[source].enabled = False` in YAML instead of only printing instructions + an audit row.

- [ ] **Step 1: Write the failing test**

```python
async def test_webhook_disable_writes_real_config(tmp_path, monkeypatch, db):
    config_file = tmp_path / "stackowl.yaml"
    monkeypatch.setattr(
        "stackowl.commands.config_helpers.config_path", lambda: config_file
    )
    from stackowl.commands.config_helpers import save_yaml
    save_yaml(config_file, {
        "webhook": {"enabled": True, "sources": {"acme": {"enabled": True, "secret": "keychain:x", "delivery_id_header": "X-Id"}}}
    })
    cmd = WebhookCommand(db=db, settings=make_settings())

    result = await cmd.handle("disable acme", make_state())

    assert "✓" in result
    from stackowl.commands.config_helpers import load_yaml
    data = load_yaml(config_file)
    assert data["webhook"]["sources"]["acme"]["enabled"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/journeys/commands/test_webhook_command.py::test_webhook_disable_writes_real_config -v`
Expected: FAIL — today `_disable` only prints instructions and writes an audit row, never touches the source's `enabled` flag.

- [ ] **Step 3: Implement**

Replace `_disable`:

```python
    async def _disable(self, source: str, state: PipelineState) -> str:
        assert self._db is not None
        log.webhook.info(
            "[webhook] command.disable: entry", extra={"_fields": {"source": source}}
        )
        path = config_path()
        data = load_yaml(path)
        sources = data.get("webhook", {}).get("sources", {})
        if source not in sources:
            return f"✗ Webhook '{source}' not found"
        sources[source]["enabled"] = False
        save_yaml(path, data)

        reloaded = load_yaml(path)
        if reloaded.get("webhook", {}).get("sources", {}).get(source, {}).get("enabled") is not False:
            log.webhook.error(
                "[webhook] command.disable: write did not persist",
                extra={"_fields": {"source": source}},
            )
            return f"✗ Webhook '{source}' was not disabled — check file permissions/disk."

        await write_audit(
            self._db,
            event_type="webhook_disabled",
            target=source,
            actor=state.session_id or "user",
            details={"reason": "user_requested"},
        )
        if self._bus is not None:
            try:
                self._bus.emit("settings_reloaded", Settings())
            except Exception as exc:
                log.webhook.error(
                    "[webhook] command.disable: immediate reload failed",
                    exc_info=exc,
                    extra={"_fields": {"source": source}},
                )
        log.webhook.info(
            "[webhook] command.disable: exit — disabled",
            extra={"_fields": {"source": source}},
        )
        return f"✓ Webhook '{source}' disabled — live now."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/journeys/commands/test_webhook_command.py::test_webhook_disable_writes_real_config -v`
Expected: PASS.

- [ ] **Step 5: Run the full file, update module docstring**

Update the module docstring at the top of `webhook_command.py` — remove:

```
The command intentionally never *writes* config or secrets at runtime: editing
``stackowl.yaml`` and managing secrets are user operations.  It only emits
instructions and records an audit-log entry for disables.
```

replace with:

```
``register``/``disable`` write real config: register creates a new source
(auto-generating a secret via ``store_secret`` if none supplied), disable
flips ``enabled: false``. Both verify the write persisted before claiming
success, and emit an immediate ``settings_reloaded`` (see
``startup/webhook_reload.py``) so a running receiver picks up the change
without a restart — except the very first source ever registered, which
needs a restart to bind the listener in the first place (see
``startup/orchestrator.py``'s webhook wiring).
```

Run: `uv run pytest tests/journeys/commands/test_webhook_command.py tests/commands/test_webhook_meta.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/commands/webhook_command.py tests/journeys/commands/test_webhook_command.py
git commit -m "feat(webhook): /webhook disable writes real config instead of printing instructions"
```

---

### Task 5: Full-plan verification

- [ ] **Step 1: Run every test file touched in this plan**

```bash
uv run pytest tests/startup/test_webhook_receiver_wiring.py tests/startup/test_webhook_reload.py tests/journeys/commands/test_webhook_command.py tests/commands/test_webhook_meta.py tests/test_c7_f132_webhook_replay.py -v
```
Expected: all PASS.

- [ ] **Step 2: Lint + type-check**

```bash
uv run ruff check src/stackowl/webhooks/receiver.py src/stackowl/startup/webhook_reload.py src/stackowl/startup/orchestrator.py src/stackowl/commands/webhook_command.py src/stackowl/commands/assembly.py
uv run mypy src/stackowl/webhooks/receiver.py src/stackowl/startup/webhook_reload.py src/stackowl/startup/orchestrator.py src/stackowl/commands/webhook_command.py src/stackowl/commands/assembly.py
```
Expected: clean on both.

- [ ] **Step 3: Manual smoke check (documented, not automated — needs a live process)**

Not run in CI; note for the human reviewer: start the app with `webhook.enabled: false`, run `/webhook register acme delivery_id_header=X-Test-Id`, confirm the response says "restart required", restart, confirm `curl -X POST http://127.0.0.1:8766/webhook/acme` no longer 404s/connection-refuses. Then `/webhook register another delivery_id_header=X-Test-Id-2` on the now-running receiver and confirm the response says "live now" with no restart needed, and the new source is immediately reachable without restarting.
