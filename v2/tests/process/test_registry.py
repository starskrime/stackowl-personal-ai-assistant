"""ProcessRegistry — start/poll/kill/close, cap, TTL, scoping, sweep, zombie reap.

Every subprocess is ``sys.executable -c "..."`` so the suite runs on Win + POSIX.
The fake clock (ARCH-99) drives every TTL/deadline deterministically. A real short
child process exercises real spawn/reap/kill — only time is faked, never the OS.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.process.checkpoint import ProcessCheckpoint
from stackowl.process.registry import ProcessRegistry, ProcessRegistryError

from .conftest import FakeClock, py


async def _await_exit(reg: ProcessRegistry, handle, *, session_id="s1", tries=200):
    """Poll until the handle reaches a terminal state (bounded, never sleeps long)."""
    for _ in range(tries):
        polled = await reg.poll(handle.process_id, session_id)
        if polled is not None and not polled.is_running:
            return polled
        await asyncio.sleep(0.02)
    raise AssertionError("process did not exit in time")


def _registry(clock: FakeClock, tmp_path, **kw) -> ProcessRegistry:
    return ProcessRegistry(
        clock=clock,
        checkpoint=ProcessCheckpoint(path=tmp_path / "proc.json"),
        **kw,
    )


async def test_start_then_poll_reaps_zombie(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path)
    handle = await reg.start(py("print('hi')"), session_id="s1")
    assert handle.is_running or handle.status in {"exited", "running"}
    polled = await _await_exit(reg, handle)
    # Eager-reap ran: exit_code set, status exited, transport awaited (no zombie).
    assert polled.status == "exited"
    assert polled.exit_code == 0
    assert polled.transport.returncode == 0
    out, _err = reg.read_log(handle.process_id, "s1")
    assert "hi" in out


async def test_nonzero_exit_is_failed(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path)
    handle = await reg.start(py("import sys; sys.exit(3)"), session_id="s1")
    polled = await _await_exit(reg, handle)
    assert polled.status == "failed"
    assert polled.exit_code == 3


async def test_concurrency_cap_refuses_structured(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path, max_processes=2)
    # Two long-lived sleepers fill the cap.
    h1 = await reg.start(py("import time; time.sleep(30)"), session_id="s1")
    h2 = await reg.start(py("import time; time.sleep(30)"), session_id="s1")
    with pytest.raises(ProcessRegistryError) as exc:
        await reg.start(py("print(1)"), session_id="s1")
    assert exc.value.reason == "too_many_processes"
    # Kill is always allowed — frees a slot.
    assert await reg.kill(h1.process_id, "s1") is True
    h3 = await reg.start(py("print(1)"), session_id="s1")
    assert h3 is not None
    await reg.kill(h2.process_id, "s1")
    await reg.kill(h3.process_id, "s1")


async def test_kill_reaps_and_is_idempotent(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path)
    handle = await reg.start(py("import time; time.sleep(30)"), session_id="s1")
    assert await reg.kill(handle.process_id, "s1") is True
    assert handle.status == "killed"
    assert handle.transport.returncode is not None  # zombie reaped
    # Kill of an already-terminal process is a no-op SUCCESS (self-healing).
    assert await reg.kill(handle.process_id, "s1") is True


async def test_mandatory_ttl_auto_kill_via_fake_clock(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path, max_lifetime_seconds=100.0)
    handle = await reg.start(py("import time; time.sleep(30)"), session_id="s1")
    # Not yet past the deadline — sweep leaves it running.
    counts = await reg.sweep()
    assert counts["auto_killed"] == 0
    # Advance past the MANDATORY max lifetime — the sweep auto-kills it.
    clock.advance(101.0)
    counts = await reg.sweep()
    assert counts["auto_killed"] == 1
    assert handle.status == "killed"


async def test_session_scoping_hides_other_sessions(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path)
    h_a = await reg.start(py("import time; time.sleep(30)"), session_id="A")
    await reg.start(py("import time; time.sleep(30)"), session_id="B")
    # Session A sees only its own process by default.
    a_view = reg.list(session_id="A")
    assert {h.session_id for h in a_view} == {"A"}
    # A cannot poll B's process (scoping returns None).
    b_proc = reg.list(session_id="B")[0]
    assert await reg.poll(b_proc.process_id, "A") is None
    # all=True is the audited cross-session view.
    everyone = reg.list(session_id="A", all=True)
    assert {h.session_id for h in everyone} == {"A", "B"}
    for h in everyone:
        await reg.kill(h.process_id, h.session_id)
    assert h_a.status == "killed"


async def test_dead_handle_prune_after_ttl(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path, dead_prune_seconds=50.0)
    handle = await reg.start(py("print('bye')"), session_id="s1")
    await _await_exit(reg, handle)
    # Still within the prune window — retained so the agent can poll it.
    assert await reg.sweep() == {"auto_killed": 0, "pruned": 0, "evicted": 0}
    assert await reg.poll(handle.process_id, "s1") is not None
    # Advance past the dead-handle prune TTL — sweep drops it.
    clock.advance(60.0)
    counts = await reg.sweep()
    assert counts["pruned"] == 1
    assert await reg.poll(handle.process_id, "s1") is None


async def test_aggregate_buffer_eviction(clock, tmp_path) -> None:
    # Tiny aggregate cap forces eviction of the oldest process's capture.
    reg = _registry(clock, tmp_path, aggregate_buffer_bytes=8)
    h_old = await reg.start(py("print('A' * 50)"), session_id="s1")
    clock.advance(1.0)
    h_new = await reg.start(py("print('B' * 50)"), session_id="s1")
    await _await_exit(reg, h_old)
    await _await_exit(reg, h_new)
    counts = await reg.sweep()
    assert counts["evicted"] >= 1
    # The OLDEST process's buffer was released first (truncation recorded).
    assert h_old.stdout_buffer.truncated is True


async def test_write_stdin_to_running_process(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path)
    handle = await reg.start(
        py("import sys; data = sys.stdin.readline(); print('got:' + data.strip())"),
        session_id="s1",
    )
    assert await reg.write_stdin(handle.process_id, "ping\n", "s1") is True
    polled = await _await_exit(reg, handle)
    assert polled.exit_code == 0
    out, _ = reg.read_log(handle.process_id, "s1")
    assert "got:ping" in out


async def test_clear_all_terminates_everything(clock, tmp_path) -> None:
    reg = _registry(clock, tmp_path)
    h1 = await reg.start(py("import time; time.sleep(30)"), session_id="s1")
    h2 = await reg.start(py("import time; time.sleep(30)"), session_id="s2")
    cleared = await reg.clear_all()
    assert cleared == 2
    assert h1.status == "killed"
    assert h2.status == "killed"


async def test_reconcile_dead_pid_starts_clean(clock, tmp_path) -> None:
    # Pre-seed a checkpoint with a dead pid; reconcile must not adopt it.
    ckpt = ProcessCheckpoint(path=tmp_path / "proc.json")
    from stackowl.process.checkpoint import CheckpointEntry

    ckpt.save([CheckpointEntry(
        process_id="ghost", pid=999_999_999, command=["x"],
        session_id="s1", created_at=1.0, status="running",
    )])
    reg = ProcessRegistry(clock=clock, checkpoint=ckpt)
    reg.reconcile()
    assert reg.list(all=True) == []
