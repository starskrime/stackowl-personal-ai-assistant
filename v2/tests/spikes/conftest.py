"""Spike-test conftest.

Adds a ``--runspike`` CLI flag as an equivalent of the ``STACKOWL_RUN_SPIKES=1``
environment variable. Either form opts in to running expensive ADR spike tests
that are otherwise skipped by default.
"""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--runspike",
        action="store_true",
        default=False,
        help="Run integration spike tests (equivalent to STACKOWL_RUN_SPIKES=1).",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Mirror the ``--runspike`` flag into the environment for module-level guards."""
    if config.getoption("--runspike"):
        os.environ["STACKOWL_RUN_SPIKES"] = "1"
