"""``journal.attention.classify`` -- the one pure attention policy (AD-5).

Pure, synchronous, in-memory-only: a registry lookup with no DB/network I/O,
proven here by NOT awaiting it -- a coroutine would fail these assertions
outright.
"""

from __future__ import annotations

from stackowl.journal.attention import classify
from stackowl.journal.enums import AttentionClass, Intensity


class TestClassifyIsAPureRegistryLookup:
    def test_the_three_ambient_task_types_classify_ambient_with_no_intensity(self) -> None:
        for type_name in ("task.enqueued", "task.claimed", "task.finished"):
            assert classify(type_name) == (AttentionClass.AMBIENT, None)

    def test_the_give_up_type_classifies_needs_you_high(self) -> None:
        assert classify("task.dead_lettered") == (AttentionClass.NEEDS_YOU, Intensity.HIGH)

    def test_classify_is_synchronous(self) -> None:
        """Proxy for "no I/O on the write path": a coroutine object is not a
        tuple, so this fails loudly if `classify` is ever made async."""
        import inspect

        assert not inspect.iscoroutinefunction(classify)
        result = classify("task.enqueued")
        assert isinstance(result, tuple)
