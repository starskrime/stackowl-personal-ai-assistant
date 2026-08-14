"""Pipeline steps reach the live memory half through a narrow accessor.

D08.2 slice A, second seam. `ConversationStore` exists as a type; this is the
first move of real consumers onto it.

`turn_persist` and `classify` are the two places memory is touched on a normal
turn — `store`, `retrieve`, `recent_conversation_turns` — and all three are live
half. Until now both reached them through `services.memory_bridge`, typed
`MemoryBridge | None`, which also exposes `stage`, `recall`, `delete`,
`list_staged` and `find_committed_by_prefix`: the retired extraction pipeline's
surface, over a table with 0 rows and no writers.

`StepServices.conversation_store` is a PROPERTY over the existing
`memory_bridge` field, not a second field. Two fields holding one object is two
copies of one fact, and they drift — the shape this programme keeps finding. One
source; the narrow view asks it.
"""

from __future__ import annotations


def test_it_hands_back_the_very_same_bridge() -> None:
    """A view, not a copy — so there is nothing to fall out of sync."""
    from stackowl.memory.bridge import NullMemoryBridge
    from stackowl.pipeline.services import StepServices

    bridge = NullMemoryBridge()
    services = StepServices(memory_bridge=bridge)

    assert services.conversation_store is bridge


def test_it_is_none_when_memory_is_disabled() -> None:
    """Every live caller already guards on None; the narrow view must not
    invent an object and break that guard."""
    from stackowl.pipeline.services import StepServices

    assert StepServices().conversation_store is None


def test_what_it_hands_back_satisfies_the_protocol() -> None:
    from stackowl.memory.bridge import ConversationStore, NullMemoryBridge
    from stackowl.pipeline.services import StepServices

    services = StepServices(memory_bridge=NullMemoryBridge())
    assert isinstance(services.conversation_store, ConversationStore)


def test_the_two_live_steps_no_longer_name_the_wide_bridge() -> None:
    """The seam is only worth drawing if consumers actually move onto it.

    Deliberately asserted against the SOURCE: the point is which interface these
    steps are written against, and that is a property of the text, not of any
    runtime value. Both files may still import MemoryBridge for typing — what
    must not survive is reaching the wide object to do live-half work.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "stackowl" / "pipeline"
    for rel in ("turn_persist.py", "steps/classify.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "services.conversation_store" in text, (
            f"{rel} should take the live half from services.conversation_store"
        )
        assert "services.memory_bridge" not in text, (
            f"{rel} still reaches services.memory_bridge, which exposes the "
            "retired fact half"
        )
