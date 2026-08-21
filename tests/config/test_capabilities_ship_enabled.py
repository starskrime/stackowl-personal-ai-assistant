"""Bakir's standing rule, enforced instead of remembered.

BAKIR, 2026-08-20: *"Make it default. Never ask me to enable anything — everything
should be enabled at system level."*

A rule that lives only in prose gets followed until the person who wrote it is not in
the room. This test walks the live settings tree and fails when a boolean capability
defaults to ``False``, so a feature cannot quietly ship dormant.

WHAT THIS IS NOT. It is not "every boolean must be True". Some defaults are OFF for
reasons the rule does not overrule, and each one is listed below WITH its reason —
because an allow-list without reasons is just a way to silence a test. Three kinds
earn a place:

* **Privacy boundaries.** ``tts.cloud_enabled`` and its siblings decide whether the
  user's voice, audio or prompts LEAVE THE MACHINE. Their own descriptions say so.
  "Enable everything" cannot mean "exfiltrate by default"; that is not a capability
  the operator declined to switch on, it is one they never agreed to.
* **Trust boundaries.** The MCP allow-* flags gate what third-party callers may do
  ACROSS a boundary. Presentation is never authorization, and neither is a default.
* **Not-a-capability.** Suppression flags, accessibility opt-ins and transport
  choices where ``False`` already means "more", not "less".

Everything else is a capability, and a capability ships on.
"""

from __future__ import annotations

import pydantic
import pytest

from stackowl.config.settings import Settings

#: path -> why this one is legitimately OFF. Adding an entry is a DECISION and the
#: reason is the whole point of the entry.
LEGITIMATELY_OFF: dict[str, str] = {
    "test_mode": "not a capability — it makes the platform pretend",
    # --- privacy: these send the user's data off the machine ---
    "tts.cloud_enabled": "sends the user's text off-machine; its own description says the text never leaves when off",
    "transcription.cloud_enabled": "sends the user's AUDIO off-machine",
    "image.cloud_enabled": "sends the user's prompt off-machine",
    # --- trust boundary: what strangers may do across MCP ---
    "mcp_server.enabled": "exposes this platform to external callers; opening a door is not a capability of ours",
    "mcp_server.allow_consequential": "lets an external caller run consequential tools; a default is not authorization",
    "mcp_server.allow_browser_writes": "lets an external caller drive a browser destructively",
    # --- not a capability: False already means MORE, or it is a preference ---
    "notifications.quiet_hours.enabled": "quiet hours OFF means MORE messages reach the user",
    "telegram_channel.quiet_hours.enabled": "quiet hours OFF means more messages reach the user, per channel",
    "discord_channel.suppress_evolution_events": "suppression OFF means more events, not fewer",
    "telegram_channel.suppress_evolution_events": "suppression OFF means more events reach the user, not fewer",
    "whatsapp_channel.suppress_evolution_events": "suppression OFF means more events reach the user, not fewer",
    "ui.reduced_motion": "an accessibility PREFERENCE; forcing it on removes motion the user may want",
    "session.thread_sessions_per_user": "a deliberate design choice — threads are SHARED by default, and its description says the sharing is the point",
    "telegram_channel.socket_mode": "a transport choice, not a capability",
    # --- credentials required: enabling without them only produces errors ---
    "discord_channel.enabled": "cannot function without credentials the operator must supply anyway",
    "whatsapp_channel.enabled": "cannot function without credentials the operator must supply anyway",
    "webhook.enabled": "binds a network port; an operator decision about exposure",
    # --- measured, deliberately reverted, with a stated unblock condition ---
    "task_loop.produce_replies": (
        "TURNED BACK OFF 2026-08-18 AFTER Bakir used it. It WORKS — claim-to-delivered "
        "measured at 9s and 23s on real turns — but it loses the instant acknowledgement "
        "the fast path sends and edits in place, and on a platform where a turn takes "
        "9-30s that ack is what tells the user their message landed at all. This is not a "
        "capability nobody switched on; it is one that was switched on, measured, and "
        "reverted for a named reason with a named unblock: the loop path must send its own "
        "ack first. Flipping it would ship a UX regression Bakir personally hit."
    ),
    # --- diagnostics that cost something per use ---
    "browser.enable_har_recording": "writes a full network trace per session — a diagnostic, and it costs disk",
    "browser.enable_screenshot_captions": "spends a model call per screenshot; a cost decision, not a locked capability",
}


def _capability_booleans() -> list[tuple[str, bool, str]]:
    """Every boolean in the settings tree, with its path and description."""
    found: list[tuple[str, bool, str]] = []

    def walk(model: type[pydantic.BaseModel], path: str = "") -> None:
        for name, field in model.model_fields.items():
            here = f"{path}.{name}" if path else name
            if isinstance(field.default, bool):
                found.append((here, field.default, field.description or ""))
            annotation = field.annotation
            if isinstance(annotation, type) and issubclass(
                annotation, pydantic.BaseModel
            ):
                walk(annotation, here)

    walk(Settings)
    return found


class TestEverythingShipsEnabled:
    def test_no_capability_defaults_to_off(self) -> None:
        """The rule itself. A new feature that ships dormant fails here, by name."""
        offenders = [
            (path, desc)
            for path, default, desc in _capability_booleans()
            if default is False and path not in LEGITIMATELY_OFF
        ]
        assert not offenders, (
            "these default to OFF, so the operator would have to switch them on — "
            "which is the thing Bakir asked never to have to do. Either flip the "
            "default, or add the path to LEGITIMATELY_OFF *with the reason it is not "
            "a capability*:\n"
            + "\n".join(f"  {p}\n      {d[:120]}" for p, d in offenders)
        )

    def test_the_allow_list_has_not_gone_stale(self) -> None:
        """An exemption for a setting that no longer exists is a comment pretending to
        be a rule. It also hides the case where a flag was flipped ON correctly and
        its exemption should have gone with it."""
        live = {path for path, _, _ in _capability_booleans()}
        stale = sorted(set(LEGITIMATELY_OFF) - live)
        assert not stale, f"exemptions for settings that no longer exist: {stale}"

    @pytest.mark.parametrize("path", sorted(LEGITIMATELY_OFF))
    def test_every_exemption_carries_a_real_reason(self, path: str) -> None:
        """The allow-list is only honest if each line says WHY. A one-word entry is
        how an allow-list becomes a mute button."""
        reason = LEGITIMATELY_OFF[path]
        assert len(reason) > 25, f"{path} is exempted without a real reason"

    def test_an_exempted_flag_is_still_actually_off(self) -> None:
        """If one of these gets flipped ON later, the exemption is now a lie about the
        code and must be removed rather than left to rot."""
        defaults = {path: default for path, default, _ in _capability_booleans()}
        wrong = [p for p in LEGITIMATELY_OFF if defaults.get(p) is not False]
        assert not wrong, (
            f"exempted as 'legitimately off' but actually ON — remove the exemption: {wrong}"
        )
