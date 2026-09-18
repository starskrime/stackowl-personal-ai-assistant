"""The one attention policy (AD-5): does a registered event type need the
owner, or is it ambient. Pure, synchronous, in-memory-only -- no DB/network
I/O, so it is safe to call from a live write-path transaction. ``recorder.py``
reuses the already-fetched ``EventTypeSpec`` directly rather than calling this
a second time on the same type; this is the standalone public entry point for
any other caller (tests, a future consumer) that wants the classification
without already holding a spec.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.journal.enums import AttentionClass, Intensity
from stackowl.journal.registry import get_registry


def classify(type_name: str) -> tuple[AttentionClass, Intensity | None]:
    """Return the registered ``(attention_class, intensity)`` for ``type_name``.

    Purely a registry lookup -- the classification itself was already decided
    at registration time (``EventTypeSpec.attention_class``/``.intensity``,
    validated there). Raises :class:`~stackowl.exceptions.JournalEventTypeUnregisteredError`
    via ``get_registry().get()`` if the type is unknown, same as every other
    registry read.
    """
    # 1. ENTRY
    log.journal.debug(
        "[journal] attention.classify: entry", extra={"_fields": {"type": type_name}},
    )
    # 2. DECISION / 3. STEP -- one registry lookup; no branching of its own,
    # the decision was already made at registration time.
    spec = get_registry().get(type_name)
    result = (spec.attention_class, spec.intensity)
    # 4. EXIT
    log.journal.debug(
        "[journal] attention.classify: exit",
        extra={"_fields": {
            "type": type_name, "attention_class": spec.attention_class.value,
        }},
    )
    return result
