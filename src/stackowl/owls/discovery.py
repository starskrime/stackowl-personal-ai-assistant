"""S6 — first-contact discovery nudge.

A single line the base assistant can offer (sparingly, never spammed) to surface
owl creation to a user who does something often. Minimal by design — a string
helper, not a subsystem. The assistant decides WHEN to use it; this only provides
the WHAT. Greeting/keyword-free so it is multilingual-safe to translate.
"""

from __future__ import annotations


def discovery_nudge() -> str:
    """Return the one-line offer to set up a dedicated assistant (owl)."""
    return (
        "Want me to set up a dedicated assistant for something you do often? "
        "Tell me what it should handle, or use /owl."
    )


if __name__ == "__main__":  # pragma: no cover — runnable self-check
    assert "/owl" in discovery_nudge()
    print("discovery self-check OK")
