from __future__ import annotations

from unittest.mock import patch

import pytest

from stackowl.channels.cli_adapter import CLIAdapter
from stackowl.channels.telegram.adapter import _mint_request_id  # extracted helper


def test_cli_request_ids_are_unique_and_non_empty() -> None:
    adapter = CLIAdapter(session_id="abc12345")
    ids = {adapter._next_request_id() for _ in range(1000)}
    assert len(ids) == 1000, "CLI request ids must be unique within a session"
    assert all(rid for rid in ids), "CLI request id must be non-empty"


def test_telegram_request_ids_unique_non_empty() -> None:
    ids = {_mint_request_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(rid for rid in ids)
    assert "" not in ids


def test_telegram_mint_raises_and_logs_on_empty_id() -> None:
    """The empty-id guard must raise ValueError AND log the failure."""
    with (
        patch("stackowl.channels.telegram.adapter.uuid4") as fake_uuid4,
        patch("stackowl.channels.telegram.adapter.log.gateway.error") as logerr,
    ):
        fake_uuid4.return_value.hex = ""
        with pytest.raises(ValueError):
            _mint_request_id()
        logerr.assert_called_once()


def test_cli_mint_raises_and_logs_on_empty_session() -> None:
    """The empty-session guard must raise ValueError AND log the failure.

    The ctor coerces a falsy session_id into a fresh uuid, so we force the
    degenerate state directly to exercise the reachable guard.
    """
    adapter = CLIAdapter(session_id="abc12345")
    adapter._session_id = ""  # degenerate state the guard defends against
    with patch("stackowl.channels.cli_adapter.log.gateway.error") as logerr:
        with pytest.raises(ValueError):
            adapter._next_request_id()
        logerr.assert_called_once()
