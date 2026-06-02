"""SandboxSweepHandler + SandboxReaper — the GC seam for leaked sandbox artifacts (E11-S6).

Mirrors ``test_process_sweep_handler.py``: proves the JobHandler the scheduler
ACTUALLY dispatches (the factory registers it on the ``HandlerRegistry`` so
``scheduler.get("sandbox_sweep")`` resolves) and that ``execute`` drives the three
reap sources and reports the counts — self-healing (never raises into the scheduler
loop) when a reap fails.

The reap PRIMITIVES are exercised WITHOUT spawning real sandboxes/containers: the
scratch reap runs against a faked tmp home (an old dir is removed, a fresh dir +
the reserved ``seccomp`` dir are kept), and the docker/systemd reaps are mocked at
the subprocess boundary (asserting the reap commands issued for stale, the guard
for an absent tool, and never-raise on a failing rm).
"""

from __future__ import annotations

import pytest

from stackowl.sandbox.limits import SANDBOX_ARTIFACT_TTL_S
from stackowl.sandbox.reap import SandboxReaper
from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.sandbox_sweep import (
    SandboxSweepHandler,
    register_sandbox_sweep_handler,
)
from stackowl.scheduler.job import Job


class FakeClock:
    """Hand-advanced clock (ARCH-99) so the TTL is deterministic (no sleeping)."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._t = start

    def monotonic(self) -> float:  # pragma: no cover — unused by the reaper
        return self._t

    def now(self):  # noqa: ANN201
        from datetime import UTC, datetime

        return datetime.fromtimestamp(self._t, tz=UTC)

    def advance(self, seconds: float) -> None:
        self._t += seconds

    async def async_sleep(self, seconds: float) -> None:  # pragma: no cover — unused
        return None


def _job() -> Job:
    return Job(
        job_id="job-sandbox-sweep",
        handler_name="sandbox_sweep",
        schedule="every 10m",
        idempotency_key="k",
        last_run_at=None,
        next_run_at="2026-01-01T00:00:00Z",
        status="pending",
    )


@pytest.fixture(autouse=True)
def _clean_handler_registry():  # noqa: ANN202
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


# --------------------------------------------------------------- factory + schedule


def test_factory_registers_on_handler_registry() -> None:
    handler = register_sandbox_sweep_handler()
    resolved = HandlerRegistry.instance().get("sandbox_sweep")
    assert resolved is handler
    assert handler.handler_name == "sandbox_sweep"


def test_handler_name_is_sandbox_sweep() -> None:
    assert SandboxSweepHandler().handler_name == "sandbox_sweep"


# --------------------------------------------------------------- scratch reaping


def _seed_scratch(root, *, age_s: float, clock: FakeClock, name: str) -> None:
    """Create a scratch dir under ``root`` whose mtime is ``age_s`` in the past."""
    import os

    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text("print(1)", encoding="utf-8")
    mtime = clock.now().timestamp() - age_s
    os.utime(d, (mtime, mtime))


def test_reap_scratch_removes_stale_keeps_fresh_and_seccomp(tmp_path) -> None:
    clock = FakeClock()
    root = tmp_path / "sandbox"
    root.mkdir()
    # Stale (older than TTL) — must be reaped.
    _seed_scratch(root, age_s=SANDBOX_ARTIFACT_TTL_S + 100, clock=clock, name="sid-aaaa")
    # Fresh (a live run, ~10s old) — must be KEPT (never reap an in-flight run).
    _seed_scratch(root, age_s=10.0, clock=clock, name="sid-bbbb")
    # Reserved seccomp dir — must NEVER be reaped even if old.
    _seed_scratch(root, age_s=SANDBOX_ARTIFACT_TTL_S * 10, clock=clock, name="seccomp")

    reaper = SandboxReaper(clock=clock, scratch_root=root)
    reaped = reaper.reap_scratch()

    assert reaped == 1
    assert not (root / "sid-aaaa").exists()  # stale removed
    assert (root / "sid-bbbb").exists()  # fresh live-run dir kept
    assert (root / "seccomp").exists()  # reserved dir kept


def test_reap_scratch_missing_root_is_noop(tmp_path) -> None:
    reaper = SandboxReaper(clock=FakeClock(), scratch_root=tmp_path / "nope")
    assert reaper.reap_scratch() == 0  # never raises


# --------------------------------------------------------------- docker / systemd reaps


class _FakeCmd:
    """Records issued argv and replies from a scripted queue. No real subprocess."""

    def __init__(self, replies: list[tuple[bool, str]]) -> None:
        self._replies = list(replies)
        self.calls: list[list[str]] = []

    async def __call__(self, argv: list[str]) -> tuple[bool, str]:
        self.calls.append(argv)
        return self._replies.pop(0) if self._replies else (True, "")


def _reaper_with_tools(monkeypatch, clock: FakeClock, tmp_path) -> SandboxReaper:
    """A reaper whose docker/systemctl are 'present' (which() patched truthy)."""
    monkeypatch.setattr("stackowl.sandbox.reap.shutil.which", lambda _b: "/usr/bin/" + _b)
    return SandboxReaper(clock=clock, scratch_root=tmp_path / "sandbox")


def _docker_created(clock: FakeClock, *, age_s: float) -> str:
    """Render docker's CreatedAt for a container created ``age_s`` ago vs the clock."""
    from datetime import datetime

    dt = datetime.fromtimestamp(clock.now().timestamp() - age_s, tz=clock.now().tzinfo)
    # Docker's default format: '2026-06-02 11:22:33 +0000 UTC'.
    return dt.strftime("%Y-%m-%d %H:%M:%S %z") + " UTC"


@pytest.mark.asyncio
async def test_reap_containers_force_removes_stale(monkeypatch, tmp_path) -> None:
    clock = FakeClock()
    reaper = _reaper_with_tools(monkeypatch, clock, tmp_path)
    # aaaa = EXITED (young) → reap; bbbb = running but OLDER than TTL → reap.
    old = _docker_created(clock, age_s=SANDBOX_ARTIFACT_TTL_S + 100)
    young = _docker_created(clock, age_s=10.0)
    listing = (
        f"stackowl-sbx-aaaa\texited\t{young}\n"
        f"stackowl-sbx-bbbb\trunning\t{old}\n"
    )
    fake = _FakeCmd([
        (True, listing),  # ps -a listing (name\tstate\tcreated)
        (True, ""),  # rm aaaa
        (True, ""),  # rm bbbb
    ])
    monkeypatch.setattr(reaper, "_cmd", fake)

    reaped = await reaper.reap_containers()
    assert reaped == 2
    # The ps filter + a rm -f per stale container were issued.
    assert any("ps" in c and "name=stackowl-sbx-" in c for c in fake.calls)
    assert ["docker", "rm", "-f", "stackowl-sbx-aaaa"] in fake.calls
    assert ["docker", "rm", "-f", "stackowl-sbx-bbbb"] in fake.calls


@pytest.mark.asyncio
async def test_reap_containers_spares_live_running_young(monkeypatch, tmp_path) -> None:
    """LIVE-RUN SAFETY: a RUNNING container younger than the TTL is NEVER reaped."""
    clock = FakeClock()
    reaper = _reaper_with_tools(monkeypatch, clock, tmp_path)
    young = _docker_created(clock, age_s=10.0)  # an in-flight run, ~10s old
    listing = f"stackowl-sbx-live\trunning\t{young}\n"
    fake = _FakeCmd([(True, listing)])
    monkeypatch.setattr(reaper, "_cmd", fake)

    reaped = await reaper.reap_containers()
    assert reaped == 0  # the live run is spared
    assert not any("rm" in c for c in fake.calls)  # NO rm issued for the live container


@pytest.mark.asyncio
async def test_reap_containers_spares_running_with_unparseable_created(
    monkeypatch, tmp_path
) -> None:
    """FAIL-SAFE: a running container whose created stamp won't parse is SPARED."""
    reaper = _reaper_with_tools(monkeypatch, FakeClock(), tmp_path)
    listing = "stackowl-sbx-weird\trunning\t<not-a-timestamp>\n"
    fake = _FakeCmd([(True, listing)])
    monkeypatch.setattr(reaper, "_cmd", fake)

    reaped = await reaper.reap_containers()
    assert reaped == 0  # cannot prove a leak → spare
    assert not any("rm" in c for c in fake.calls)


@pytest.mark.asyncio
async def test_reap_containers_no_stale_issues_no_rm(monkeypatch, tmp_path) -> None:
    reaper = _reaper_with_tools(monkeypatch, FakeClock(), tmp_path)
    fake = _FakeCmd([(True, "\n")])  # empty listing
    monkeypatch.setattr(reaper, "_cmd", fake)

    reaped = await reaper.reap_containers()
    assert reaped == 0
    assert not any("rm" in c for c in fake.calls)  # NONE for fresh/empty


@pytest.mark.asyncio
async def test_reap_containers_absent_docker_is_noop(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("stackowl.sandbox.reap.shutil.which", lambda _b: None)
    reaper = SandboxReaper(clock=FakeClock(), scratch_root=tmp_path / "sandbox")
    called = False

    async def _boom(argv):  # noqa: ANN001, ANN202
        nonlocal called
        called = True
        return True, ""

    monkeypatch.setattr(reaper, "_cmd", _boom)
    assert await reaper.reap_containers() == 0  # guarded — no-op
    assert called is False  # never even shelled out


@pytest.mark.asyncio
async def test_reap_containers_never_raises_on_failing_rm(monkeypatch, tmp_path) -> None:
    reaper = _reaper_with_tools(monkeypatch, FakeClock(), tmp_path)
    fake = _FakeCmd([
        (True, "stackowl-sbx-aaaa\texited\t\n"),  # exited → stale → rm attempted
        (False, ""),  # rm FAILS
    ])
    monkeypatch.setattr(reaper, "_cmd", fake)
    # A failing rm is logged + counted as not-reaped; the method still returns.
    assert await reaper.reap_containers() == 0
    assert ["docker", "rm", "-f", "stackowl-sbx-aaaa"] in fake.calls


@pytest.mark.asyncio
async def test_reap_scopes_stops_stale_units(monkeypatch, tmp_path) -> None:
    reaper = _reaper_with_tools(monkeypatch, FakeClock(), tmp_path)
    # UNIT LOAD ACTIVE SUB — inactive/failed ActiveState ⇒ leaked ⇒ reap.
    listing = (
        "stackowl-sbx-aaaa.scope loaded inactive dead\n"
        "stackowl-sbx-bbbb.scope loaded failed   failed\n"
    )
    fake = _FakeCmd([(True, listing), (True, ""), (True, "")])
    monkeypatch.setattr(reaper, "_cmd", fake)

    reaped = await reaper.reap_scopes()
    assert reaped == 2
    assert ["systemctl", "--user", "stop", "stackowl-sbx-aaaa.scope"] in fake.calls
    assert ["systemctl", "--user", "stop", "stackowl-sbx-bbbb.scope"] in fake.calls


@pytest.mark.asyncio
async def test_reap_scopes_spares_live_active_scope(monkeypatch, tmp_path) -> None:
    """LIVE-RUN SAFETY: an ACTIVE scope (an in-flight run) is NEVER stopped."""
    reaper = _reaper_with_tools(monkeypatch, FakeClock(), tmp_path)
    listing = "stackowl-sbx-live.scope loaded active running\n"
    fake = _FakeCmd([(True, listing)])
    monkeypatch.setattr(reaper, "_cmd", fake)

    reaped = await reaper.reap_scopes()
    assert reaped == 0  # the live run's scope is spared
    assert not any("stop" in c for c in fake.calls)  # NO stop issued


@pytest.mark.asyncio
async def test_reap_scopes_spares_unparseable_state(monkeypatch, tmp_path) -> None:
    """FAIL-SAFE: a row missing its ActiveState column is SPARED."""
    reaper = _reaper_with_tools(monkeypatch, FakeClock(), tmp_path)
    listing = "stackowl-sbx-weird.scope\n"  # no LOAD/ACTIVE/SUB columns
    fake = _FakeCmd([(True, listing)])
    monkeypatch.setattr(reaper, "_cmd", fake)

    reaped = await reaper.reap_scopes()
    assert reaped == 0
    assert not any("stop" in c for c in fake.calls)


@pytest.mark.asyncio
async def test_reap_scopes_absent_systemctl_is_noop(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("stackowl.sandbox.reap.shutil.which", lambda _b: None)
    reaper = SandboxReaper(clock=FakeClock(), scratch_root=tmp_path / "sandbox")
    assert await reaper.reap_scopes() == 0  # non-systemd host guarded


# --------------------------------------------------------------- handler.execute


class _FakeReaper:
    """A reaper double the handler drives — fixed counts, optional raise."""

    def __init__(self, *, scratch=0, containers=0, scopes=0, raise_on=None) -> None:
        self._scratch, self._containers, self._scopes = scratch, containers, scopes
        self._raise_on = raise_on

    def reap_scratch(self) -> int:
        if self._raise_on == "scratch":
            raise RuntimeError("scratch boom")
        return self._scratch

    async def reap_containers(self) -> int:
        if self._raise_on == "containers":
            raise RuntimeError("docker boom")
        return self._containers

    async def reap_scopes(self) -> int:
        if self._raise_on == "scopes":
            raise RuntimeError("systemd boom")
        return self._scopes


@pytest.mark.asyncio
async def test_execute_drives_all_three_sources_and_reports_counts() -> None:
    handler = SandboxSweepHandler()
    handler._reaper = _FakeReaper(scratch=2, containers=1, scopes=3)  # type: ignore[assignment]
    res = await handler.execute(_job())
    assert res.success
    assert res.metadata == {"scratch": 2, "containers": 1, "scopes": 3}
    assert "scratch=2 containers=1 scopes=3" in (res.output or "")


@pytest.mark.asyncio
async def test_execute_self_heals_when_a_reap_raises() -> None:
    """A failing reap source is logged + the handler still returns success."""
    handler = SandboxSweepHandler()
    handler._reaper = _FakeReaper(scratch=1, containers=5, raise_on="containers")  # type: ignore[assignment]
    res = await handler.execute(_job())
    assert res.success  # never raises into the scheduler loop
    assert res.metadata["scratch"] == 1  # the source before the failure still ran
    assert res.metadata["containers"] == 0  # failed source reported zero
