"""A transport must not own the deadline a person is deciding against.

Bakir, 2026-09-10, after being told an approval had expired again: *"your approval
fix is stupid. It is still expiring."* He was right. The fix before this one was in
the Telegram prompter — the layer that was already generous — while the deadline
that actually bound sat in the socket bridge underneath it.

MEASURED the same hour: **five copies of one deadline, and they disagreed.**
`telegram` 1200s; `discord`, `slack`, `whatsapp` and the core→gateway IPC bridge
120s each. The IPC prompter WRAPS whichever channel prompter answers and is
constructed with no override, so the real budget on EVERY channel was two minutes
however long that channel advertised.

THE EVIDENCE IS A FIFTY-SECOND GAP. A `send_file` request at 03:30:48 was denied
at 03:32:48 — exactly 120.0s — and at 03:33:38 the gateway logged
`consent.handle_callback: resolved`. He tapped the button, the button worked, and
the core had already given up. From where he sits that is a tap that did nothing,
and no work inside the button's own layer could ever have reached it.

THE SHAPE is failure #3 in `CLAUDE.md` — two copies of one rule — with the twist
that made it invisible: the binding copy was the one that knows nothing about
users, so every discussion about how long a person should get happened in a file
that did not decide it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"

#: Every module that waits on a human consent decision.
_PROMPTER_MODULES = (
    "stackowl/runtime/socket_consent.py",
    "stackowl/channels/telegram/consent.py",
    "stackowl/channels/slack/consent.py",
    "stackowl/channels/discord/consent.py",
    "stackowl/channels/whatsapp/consent.py",
)


@pytest.mark.tripwire
def test_every_consent_prompter_resolves_to_the_same_deadline() -> None:
    """THE REGRESSION THAT MATTERS, asked of the live objects rather than the text."""
    from stackowl.channels.discord import consent as discord_consent
    from stackowl.channels.slack import consent as slack_consent
    from stackowl.channels.telegram import consent as telegram_consent
    from stackowl.channels.whatsapp import consent as whatsapp_consent
    from stackowl.runtime import socket_consent
    from stackowl.tools.consent import HUMAN_DECISION_TIMEOUT_SECONDS

    seen = {
        "socket": socket_consent._DEFAULT_TIMEOUT_SECONDS,      # noqa: SLF001
        "telegram": telegram_consent._DEFAULT_TIMEOUT_SECONDS,  # noqa: SLF001
        "slack": slack_consent._DEFAULT_TIMEOUT_SECONDS,        # noqa: SLF001
        "discord": discord_consent._DEFAULT_TIMEOUT_SECONDS,    # noqa: SLF001
        "whatsapp": whatsapp_consent._DEFAULT_TIMEOUT_SECONDS,  # noqa: SLF001
    }
    assert set(seen.values()) == {HUMAN_DECISION_TIMEOUT_SECONDS}, (
        "consent prompters disagree about how long a person gets, and the SHORTEST "
        f"one wins because the bridge wraps the rest: {seen}"
    )


@pytest.mark.tripwire
def test_the_bridge_is_never_stricter_than_the_ui_it_wraps() -> None:
    """Stated as the ASYMMETRY, because equality is not what protects him.

    The bridge wrapping the UI is the whole mechanism: if it is ever shorter, the
    UI's window becomes decoration again no matter what it says. This holds even
    if someone deliberately gives one channel longer.
    """
    from stackowl.channels.telegram import consent as telegram_consent
    from stackowl.runtime import socket_consent

    assert (
        socket_consent._DEFAULT_TIMEOUT_SECONDS  # noqa: SLF001
        >= telegram_consent._DEFAULT_TIMEOUT_SECONDS  # noqa: SLF001
    ), "the bridge gives up before the button does — his tap will do nothing again"


@pytest.mark.tripwire
def test_no_prompter_hardcodes_its_own_number() -> None:
    """The ratchet: a SIXTH copy must not appear.

    Reads the source rather than the value, because a new module that happens to
    write 1200.0 would pass the equality test today and drift tomorrow — which is
    exactly how these five came to disagree.
    """
    offenders: list[str] = []
    for rel in _PROMPTER_MODULES:
        path = _SRC / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            target = node.targets[0]
            if not (isinstance(target, ast.Name) and "TIMEOUT" in target.id.upper()):
                continue
            if isinstance(node.value, ast.Constant):
                offenders.append(f"{rel}:{node.lineno} {target.id} = {node.value.value}")
    assert not offenders, (
        "a consent deadline is written as a literal instead of asking the one "
        "source:\n" + "\n".join(f"  {o}" for o in offenders)
        + "\n\nImport HUMAN_DECISION_TIMEOUT_SECONDS from stackowl.tools.consent."
    )


def test_the_one_source_is_a_human_length_not_a_transport_length() -> None:
    """A vacuity control with a reason.

    If the shared value were ever set to a transport-ish number the tests above
    would all still pass — five modules agreeing on two minutes is precisely the
    defect, uniformly applied. A person answering a phone notification needs
    minutes, not seconds.
    """
    from stackowl.tools.consent import HUMAN_DECISION_TIMEOUT_SECONDS

    assert HUMAN_DECISION_TIMEOUT_SECONDS >= 600.0, (
        f"{HUMAN_DECISION_TIMEOUT_SECONDS}s is a machine's patience, not a person's"
    )
