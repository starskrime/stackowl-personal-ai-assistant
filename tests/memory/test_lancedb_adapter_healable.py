"""LanceDBAdapter HealableResource conformance + LanceDBHealthContributor shim
(ADR-6 self-heal, Task 2).

``ensure_available()`` drops the cached connection handle and reconnects,
letting failure propagate so the sweep's RecoveryActuator owns retry/backoff.
``available``/``unavailable_reason`` are cached connection-state reads (no
fresh probe per access), mirroring DbPool.

``LanceDBHealthContributor`` shims the adapter's existing ``health()``
(returns ``HealthReport``) into the ``HealthStatus`` shape the aggregator
expects. The critical guard here is that a real outage reported by
``health()`` must NOT be silently upgraded to "ok" — the exact mistake the
design doc flags for Kuzu's ``GraphContributor``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from stackowl.health.contributors import LanceDBHealthContributor
from stackowl.memory.bridge import HealthReport
from stackowl.memory.lancedb_adapter import LanceDBAdapter

pytestmark = pytest.mark.asyncio


def _raising_connect(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("boom: disk gone")


async def test_ensure_available_drops_and_reconnects_with_a_new_connection(
    tmp_path: Path,
) -> None:
    """A live connection is unconditionally replaced, not reused, on ensure_available."""
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance")
    first_conn = adapter._connect()  # type: ignore[attr-defined]
    assert adapter.available is True

    await adapter.ensure_available()

    assert adapter.available is True
    assert adapter._connection is not None  # type: ignore[attr-defined]
    assert adapter._connection is not first_conn  # type: ignore[attr-defined] — genuinely reconnected


async def test_ensure_available_propagates_and_marks_unavailable_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reconnect failure raises (no swallow) and available/unavailable_reason flip."""
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance")
    adapter._connect()  # type: ignore[attr-defined] — establish a real connection first
    assert adapter.available is True

    monkeypatch.setattr("lancedb.connect", _raising_connect)

    with pytest.raises(RuntimeError, match="boom: disk gone"):
        await adapter.ensure_available()

    assert adapter.available is False
    assert adapter.unavailable_reason is not None
    assert "boom" in adapter.unavailable_reason


async def test_available_and_unavailable_reason_reflect_connection_state(
    tmp_path: Path,
) -> None:
    """Cached-state reads (no fresh probe): unconnected → connected → dead."""
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance")
    # Never connected yet — cached state says unavailable, no reason recorded.
    assert adapter.available is False
    assert adapter.unavailable_reason is None

    adapter._connect()  # type: ignore[attr-defined]
    assert adapter.available is True
    assert adapter.unavailable_reason is None


async def test_register_on_recycled_is_a_noop_callback_registration(
    tmp_path: Path,
) -> None:
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance")
    # Must not raise — no downstream dependents cache the raw connection today.
    adapter.register_on_recycled(lambda: None)


async def test_health_contributor_reports_down_when_adapter_health_is_down(
    tmp_path: Path,
) -> None:
    """Anti-Kuzu-mistake guard: a real outage must surface as `down`, never `ok`."""
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance")
    adapter.health = AsyncMock(  # type: ignore[method-assign]
        return_value=HealthReport(
            name="memory.lancedb",
            status="down",
            details={"error": "RuntimeError: disk unreachable"},
            latency_ms=5.0,
        )
    )
    contributor = LanceDBHealthContributor(adapter)

    status = await contributor.health_check()

    assert status.status == "down"
    assert status.name == "lancedb"
    assert status.message is not None


async def test_health_contributor_reports_ok_when_adapter_health_is_ok(
    tmp_path: Path,
) -> None:
    """Sanity check for the opposite direction — a healthy probe must not be flagged down."""
    adapter = LanceDBAdapter(data_dir=tmp_path / "lance")
    adapter.health = AsyncMock(  # type: ignore[method-assign]
        return_value=HealthReport(
            name="memory.lancedb",
            status="ok",
            details={"has_table": True},
            latency_ms=2.0,
        )
    )
    contributor = LanceDBHealthContributor(adapter)

    status = await contributor.health_check()

    assert status.status == "ok"
    assert status.name == "lancedb"


async def test_health_contributor_name_matches_healers_dict_key() -> None:
    """contributor_name MUST match the "lancedb" key assembly.py registers in
    `healers`, or health_sweep's dict.get(status.name) lookup silently no-ops
    (the pre-existing bug the Task-1 embeddings healer has today)."""
    adapter = LanceDBAdapter(data_dir=Path("/tmp/unused"))
    contributor = LanceDBHealthContributor(adapter)
    assert contributor.contributor_name == "lancedb"
