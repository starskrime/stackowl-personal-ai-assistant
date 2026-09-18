"""Shared fixtures for ``tests/journal/``."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from stackowl.journal.health import reset_for_tests
from stackowl.journal.narrator import reset_name_resolvers_for_tests


@pytest.fixture(autouse=True)
def _reset_journal_health() -> Generator[None]:
    """``journal/health.py``'s consecutive-failure state is process-global (it
    must survive across the many transactions a real boot makes) -- reset it
    around EVERY test in this package, not just the ones that read it, so a
    test that forces a ``record()`` failure (``test_registry_and_leak_guard.py``,
    ``test_health_contributor.py``) can never leak a degraded streak into a
    later, unrelated test."""
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_journal_name_resolvers() -> Generator[None]:
    """``journal/narrator.py``'s resolver registry is the same kind of
    process-global, module-level state ``journal/health.py``'s is -- reset it
    around EVERY test in this package so one test's ``register_name_resolver``
    call (or a real subsystem wiring test) can never leak a registration into
    a later, unrelated test."""
    reset_name_resolvers_for_tests()
    yield
    reset_name_resolvers_for_tests()
