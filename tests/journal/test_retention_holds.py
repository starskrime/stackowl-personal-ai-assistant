"""``RetentionHoldRegistry`` -- register/duplicate-refusal/``held_cursors``
union (sync and async checkers)/``reset_for_tests`` (Story 2.11, AD-6).

Mirrors ``tests/journal/test_record_readers.py``'s shape for
``RecordReaderRegistry``, the sibling registry this one copies its class
shape from.
"""

from __future__ import annotations

import pytest

from stackowl.journal.retention_holds import (
    RetentionHoldRegistry,
    get_retention_hold_registry,
    reset_retention_holds_for_tests,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_retention_holds_for_tests()
    yield
    reset_retention_holds_for_tests()


class TestRegistration:
    async def test_a_duplicate_name_raises_and_the_first_stays_registered(self) -> None:
        registry = RetentionHoldRegistry()
        registry.register_hold_source("epic3_needs_you", lambda: frozenset({1}))
        with pytest.raises(ValueError, match="epic3_needs_you"):
            registry.register_hold_source("epic3_needs_you", lambda: frozenset({2}))
        # The FIRST checker stays registered -- a duplicate refusal must not
        # have replaced or removed it.
        assert await registry.held_cursors() == frozenset({1})

    def test_reset_for_tests_clears_every_registration(self) -> None:
        registry = RetentionHoldRegistry()
        registry.register_hold_source("a", lambda: frozenset())
        registry.reset_for_tests()
        # A second registration of the same name no longer raises -- the
        # first was actually cleared, not merely shadowed.
        registry.register_hold_source("a", lambda: frozenset())


class TestHeldCursors:
    async def test_no_sources_means_no_held_cursors(self) -> None:
        registry = RetentionHoldRegistry()
        assert await registry.held_cursors() == frozenset()

    async def test_a_sync_checker_contributes_its_cursors(self) -> None:
        registry = RetentionHoldRegistry()
        registry.register_hold_source("sync_source", lambda: frozenset({1, 2}))
        assert await registry.held_cursors() == frozenset({1, 2})

    async def test_an_async_checker_contributes_its_cursors(self) -> None:
        registry = RetentionHoldRegistry()

        async def _checker() -> frozenset[int]:
            return frozenset({3, 4})

        registry.register_hold_source("async_source", _checker)
        assert await registry.held_cursors() == frozenset({3, 4})

    async def test_multiple_sources_union(self) -> None:
        registry = RetentionHoldRegistry()

        async def _async_checker() -> frozenset[int]:
            return frozenset({10})

        registry.register_hold_source("sync_source", lambda: frozenset({1, 2}))
        registry.register_hold_source("async_source", _async_checker)
        registry.register_hold_source("empty_source", lambda: frozenset())
        assert await registry.held_cursors() == frozenset({1, 2, 10})


class TestTheModuleSingleton:
    async def test_get_retention_hold_registry_returns_the_same_instance(self) -> None:
        assert get_retention_hold_registry() is get_retention_hold_registry()

    async def test_reset_retention_holds_for_tests_clears_the_singleton(self) -> None:
        get_retention_hold_registry().register_hold_source(
            "x", lambda: frozenset({99})
        )
        assert await get_retention_hold_registry().held_cursors() == frozenset({99})
        reset_retention_holds_for_tests()
        assert await get_retention_hold_registry().held_cursors() == frozenset()
