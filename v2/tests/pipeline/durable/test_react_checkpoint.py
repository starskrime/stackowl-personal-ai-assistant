"""ReActCheckpoint — serialize/deserialize round-trip + error handling (S1).

Tests:
* Round-trip preserves all fields exactly (iteration, messages, tool_call_records).
* Nested/complex message dicts (content-block lists) survive round-trip.
* Malformed JSON raises ReActCheckpointDecodeError (not silently returns None).
* Schema validation failure (missing required field) raises ReActCheckpointDecodeError.
* Empty messages / empty tool_call_records are round-trip-safe.
* Frozen model cannot be mutated in place (immutability check).
"""

from __future__ import annotations

import json

import pytest

from stackowl.exceptions import CheckpointSchemaError
from stackowl.pipeline.durable.react_checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    ReActCheckpoint,
    ReActCheckpointDecodeError,
    deserialize,
    serialize,
)

# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


def test_round_trip_preserves_iteration() -> None:
    cp = ReActCheckpoint(iteration=3, messages=[], tool_call_records=[])
    assert deserialize(serialize(cp)).iteration == 3


def test_round_trip_preserves_messages() -> None:
    msgs = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    cp = ReActCheckpoint(iteration=1, messages=msgs, tool_call_records=[])
    restored = deserialize(serialize(cp))
    assert restored.messages == msgs


def test_round_trip_preserves_tool_call_records() -> None:
    records = [
        {"id": "call_abc", "name": "read_file", "args": {"path": "/tmp/x"}, "result": "contents", "failed": False},
        {"id": None, "name": "shell", "args": {"cmd": "ls"}, "result": "a\nb\n", "failed": False},
    ]
    cp = ReActCheckpoint(iteration=2, messages=[], tool_call_records=records)
    restored = deserialize(serialize(cp))
    assert restored.tool_call_records == records


def test_round_trip_nested_message_content_blocks() -> None:
    """Anthropic can send content as a list of typed blocks — must survive."""
    msgs = [
        {"role": "assistant", "content": [
            {"type": "text", "text": "thinking..."},
            {"type": "tool_use", "id": "toolu_01", "name": "search", "input": {"q": "foo"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01", "content": "results"},
        ]},
    ]
    cp = ReActCheckpoint(iteration=1, messages=msgs, tool_call_records=[])
    restored = deserialize(serialize(cp))
    assert restored.messages == msgs


def test_round_trip_zero_iteration() -> None:
    cp = ReActCheckpoint(iteration=0)
    assert deserialize(serialize(cp)).iteration == 0


def test_round_trip_empty_checkpoint() -> None:
    cp = ReActCheckpoint(iteration=0, messages=[], tool_call_records=[])
    restored = deserialize(serialize(cp))
    assert restored.iteration == 0
    assert restored.messages == []
    assert restored.tool_call_records == []


def test_serialize_output_is_valid_json() -> None:
    cp = ReActCheckpoint(iteration=5, messages=[{"role": "user", "content": "hi"}])
    blob = serialize(cp)
    parsed = json.loads(blob)
    assert parsed["iteration"] == 5
    assert parsed["messages"] == [{"role": "user", "content": "hi"}]


def test_serialize_is_deterministic() -> None:
    """Same checkpoint always yields identical blob (sort_keys=True)."""
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]
    cp = ReActCheckpoint(iteration=2, messages=msgs)
    assert serialize(cp) == serialize(cp)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_deserialize_malformed_json_raises() -> None:
    with pytest.raises(ReActCheckpointDecodeError, match="not valid JSON"):
        deserialize("not-json{{")


def test_deserialize_empty_string_raises() -> None:
    with pytest.raises(ReActCheckpointDecodeError):
        deserialize("")


def test_deserialize_missing_iteration_raises() -> None:
    blob = json.dumps({"messages": [], "tool_call_records": []})
    with pytest.raises(ReActCheckpointDecodeError, match="schema validation"):
        deserialize(blob)


def test_deserialize_wrong_type_raises() -> None:
    blob = json.dumps({"iteration": "not-an-int", "messages": [], "tool_call_records": []})
    with pytest.raises(ReActCheckpointDecodeError):
        deserialize(blob)


def test_deserialize_negative_iteration_raises() -> None:
    blob = json.dumps({"iteration": -1, "messages": [], "tool_call_records": []})
    with pytest.raises(ReActCheckpointDecodeError):
        deserialize(blob)


def test_deserialize_messages_not_list_raises() -> None:
    blob = json.dumps({"iteration": 0, "messages": "not-a-list", "tool_call_records": []})
    with pytest.raises(ReActCheckpointDecodeError):
        deserialize(blob)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_frozen_model_cannot_be_mutated() -> None:
    cp = ReActCheckpoint(iteration=1)
    with pytest.raises(Exception):  # pydantic ValidationError or TypeError
        cp.iteration = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# serialize fail-loud (no silent str-coercion via default=str)
# ---------------------------------------------------------------------------


def test_serialize_non_json_serializable_raises_decode_error() -> None:
    """serialize() must raise ReActCheckpointDecodeError on non-JSON-serializable values.

    Pydantic's frozen model accepts Any-typed dict fields at construction time,
    so a messages entry containing a datetime or set bypasses field validation.
    serialize() must then fail loudly rather than silently coercing via
    default=str (which would produce a lossy, non-round-trip-safe blob).
    """
    from datetime import datetime

    cp = ReActCheckpoint(
        iteration=0,
        messages=[{"role": "user", "content": datetime(2025, 1, 1)}],  # not JSON-serializable
        tool_call_records=[],
    )
    with pytest.raises(ReActCheckpointDecodeError, match="not JSON-serializable"):
        serialize(cp)


# ---------------------------------------------------------------------------
# schema_version — versioned durable contract (Winston)
# ---------------------------------------------------------------------------


def test_schema_version_round_trips() -> None:
    """serialize() includes schema_version and deserialize() restores it."""
    cp = ReActCheckpoint(iteration=2, messages=[{"role": "user", "content": "x"}])
    assert cp.schema_version == CHECKPOINT_SCHEMA_VERSION
    blob = serialize(cp)
    assert '"schema_version"' in blob
    restored = deserialize(blob)
    assert restored.schema_version == CHECKPOINT_SCHEMA_VERSION


def test_legacy_blob_without_version_loads_as_v1() -> None:
    """A pre-versioning blob (no schema_version field) loads as version 1 (back-compat)."""
    blob = json.dumps({"iteration": 1, "messages": [], "tool_call_records": []})
    cp = deserialize(blob)
    assert cp.schema_version == 1


def test_unknown_future_version_raises_checkpoint_schema_error() -> None:
    """A blob from a newer build (version > current) fails loud, not silently."""
    blob = json.dumps(
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION + 1,
            "iteration": 0,
            "messages": [],
            "tool_call_records": [],
        }
    )
    with pytest.raises(CheckpointSchemaError) as exc_info:
        deserialize(blob)
    assert exc_info.value.found_version == CHECKPOINT_SCHEMA_VERSION + 1
    assert exc_info.value.current_version == CHECKPOINT_SCHEMA_VERSION


def test_non_int_version_raises_checkpoint_schema_error() -> None:
    """A non-integer schema_version is rejected loud."""
    blob = json.dumps(
        {"schema_version": "garbage", "iteration": 0, "messages": [], "tool_call_records": []}
    )
    with pytest.raises(CheckpointSchemaError):
        deserialize(blob)
