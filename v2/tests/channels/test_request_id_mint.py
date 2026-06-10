from __future__ import annotations

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
