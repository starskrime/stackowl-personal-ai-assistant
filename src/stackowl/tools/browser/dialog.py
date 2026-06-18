"""browser_dialog — accept or dismiss a pending JS dialog.

JS dialogs (alert/confirm/prompt/beforeunload) are captured by ``BrowserSession``
via ``page.on("dialog")`` and held in a bounded per-page queue (see
``PendingDialog``); each blocks the page until acted upon, and auto-dismisses on a
TTL so the page never hangs. This tool resolves a pending dialog by ``dialog_id``:
``accept`` (optionally supplying ``prompt_text`` for a prompt) or ``dismiss``.

Pending dialogs are surfaced to the model in ``browser_snapshot`` output, which is
where the model discovers a ``dialog_id`` to act on.

**Consequential — every call passes the consent gate, always-ask (never batched):**
accepting actuates page behavior (a `confirm()` the page is waiting on), and even
`dismiss` is gated here because the gate is per-tool; the safe default is to
confirm any dialog interaction with the user. Dialog message / prompt text are
never logged (they may carry sensitive page content).

Provenance / port-vs-build: see ``_bmad-output/research/tool-port-analysis.md``
(E2 ``browser_dialog`` row — HYBRID: response-only accept/dismiss model + a
pending-dialogs field surfaced in the snapshot; routed via the in-process engine's
``page.on("dialog")`` queue, not a sidecar supervisor).
"""

from __future__ import annotations

import contextlib
import time

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.browser._logging import url_path_only
from stackowl.tools.browser.tools import _audit_consequential, _err, _ok, _services_or_unavailable

_ACTIONS = ("accept", "dismiss")


class BrowserDialogTool(Tool):
    """Accept or dismiss a pending JS dialog by id (consequential)."""

    @property
    def name(self) -> str:
        return "browser_dialog"

    @property
    def description(self) -> str:
        # Plain-language summary — shown in the consent prompt to a non-engineer.
        return (
            "Respond to a pop-up dialog the web page is showing (alert/confirm/prompt). "
            "action='accept' confirms it (optionally typing prompt_text into a prompt); "
            "action='dismiss' cancels it. Find the dialog_id in browser_snapshot. "
            "Requires your approval each time."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "page_handle": {"type": "string"},
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "dialog_id": {"type": "string", "description": "Id of the pending dialog (from browser_snapshot)."},
                "prompt_text": {"type": "string", "description": "Text to enter when accepting a prompt dialog."},
            },
            "required": ["session_id", "action", "dialog_id"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",  # gated on every call (always-ask)
            commit_coupling="unconfirmed",
            toolset_group="browser",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY — never log the dialog message / prompt_text (may be sensitive).
        session_id = str(kwargs.get("session_id", ""))
        page_handle = kwargs.get("page_handle")
        action = str(kwargs.get("action", ""))
        dialog_id = str(kwargs.get("dialog_id", ""))
        prompt_text = str(kwargs.get("prompt_text", "")) if kwargs.get("prompt_text") is not None else ""
        log.tool.info(
            "browser_dialog.execute: entry",
            extra={"_fields": {"session_id": session_id, "action": action, "dialog_id": dialog_id}},
        )
        # 2. DECISION — validate action.
        if action not in _ACTIONS:
            return _err(f"Invalid action: {action!r} (expected accept|dismiss)", t0, tool="browser_dialog")
        # Self-healing: no browser substrate / dead session → structured result.
        runtime, sessions, err = _services_or_unavailable()
        if err:
            return _err(err, t0, tool="browser_dialog")
        try:
            sess, page, ph = await sessions.get_page(
                session_id, str(page_handle) if page_handle else None
            )
        except Exception as exc:
            return _err(f"browser session unavailable: {type(exc).__name__}: {exc}", t0, tool="browser_dialog")

        obs = sess.observers.get(ph)
        pending = obs.dialogs.get(dialog_id) if obs is not None else None
        if pending is None:
            return _err(f"Unknown or already-resolved dialog_id: {dialog_id!r}", t0, tool="browser_dialog")

        # 3. STEP — resolve the dialog. Cancel the TTL task and pop the entry
        # BEFORE awaiting accept/dismiss: the auto-dismiss task's later pop then
        # returns None and bails, closing the race window during the await.
        if pending.auto_task is not None:
            with contextlib.suppress(Exception):
                pending.auto_task.cancel()
        obs.dialogs.pop(dialog_id, None)
        try:
            if action == "accept":
                if pending.type == "prompt":
                    await pending.dialog.accept(prompt_text or pending.default_value)
                else:
                    await pending.dialog.accept()
            else:
                await pending.dialog.dismiss()
        except Exception as exc:
            return _err(f"dialog {action} failed: {type(exc).__name__}: {exc}", t0, tool="browser_dialog")

        _audit_consequential(
            "browser_dialog",
            url_path_only(page.url),
            {"session_id": session_id, "action": action, "dialog_type": pending.type},
        )
        # 4. EXIT
        log.tool.info(
            "browser_dialog.execute: exit",
            extra={"_fields": {"action": action, "dialog_type": pending.type}},
        )
        return _ok({"ok": True, "action": action, "dialog_type": pending.type}, t0, tool="browser_dialog")
