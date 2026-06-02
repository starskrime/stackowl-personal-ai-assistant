"""ProcessCheckpoint — persist + reconcile, dead-PID and corrupt-file healing (E9)."""

from __future__ import annotations

import os

from stackowl.process.checkpoint import CheckpointEntry, ProcessCheckpoint


def _entry(pid: int | None, *, process_id: str = "p1") -> CheckpointEntry:
    return CheckpointEntry(
        process_id=process_id,
        pid=pid,
        command=["echo", "hi"],
        session_id="s1",
        created_at=1000.0,
        status="running",
    )


def test_save_then_load_roundtrip(tmp_path) -> None:
    ckpt = ProcessCheckpoint(path=tmp_path / "proc.json")
    ckpt.save([_entry(1234)])
    loaded = ckpt.load()
    assert len(loaded) == 1
    assert loaded[0].process_id == "p1"
    assert loaded[0].pid == 1234
    assert loaded[0].command == ["echo", "hi"]


def test_load_missing_file_is_empty(tmp_path) -> None:
    ckpt = ProcessCheckpoint(path=tmp_path / "absent.json")
    assert ckpt.load() == []


def test_load_corrupt_file_heals_to_empty(tmp_path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json at all", encoding="utf-8")
    ckpt = ProcessCheckpoint(path=path)
    # Self-healing: corrupt file → empty, never raises.
    assert ckpt.load() == []


def test_reconcile_dead_pid_marked_exited(tmp_path) -> None:
    ckpt = ProcessCheckpoint(path=tmp_path / "proc.json")
    # PID 999999999 is almost certainly not a live process.
    ckpt.save([_entry(999_999_999)])
    result = ckpt.reconcile()
    assert result.adopted == []
    assert len(result.exited) == 1
    assert result.exited[0].status == "exited"


def test_reconcile_live_pid_is_adopted(tmp_path) -> None:
    ckpt = ProcessCheckpoint(path=tmp_path / "proc.json")
    # Our own PID is, by definition, alive.
    ckpt.save([_entry(os.getpid())])
    result = ckpt.reconcile()
    assert len(result.adopted) == 1
    assert result.exited == []


def test_save_is_atomic_no_partial_on_replace(tmp_path) -> None:
    path = tmp_path / "proc.json"
    ckpt = ProcessCheckpoint(path=path)
    ckpt.save([_entry(1)])
    ckpt.save([_entry(2, process_id="p2")])
    # The second save fully replaced the first; no leftover temp files.
    loaded = ckpt.load()
    assert len(loaded) == 1
    assert loaded[0].process_id == "p2"
    stragglers = [p for p in path.parent.iterdir() if p.name.startswith(".proc_ckpt_")]
    assert stragglers == []
