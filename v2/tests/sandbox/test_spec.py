"""Tests for the sandbox value models (ResourceCaps / ExecSpec / ExecResult)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.sandbox.limits import DEFAULT_ENV_ALLOW, MAX_STDERR_BYTES, MAX_STDOUT_BYTES
from stackowl.sandbox.spec import ExecResult, ExecSpec, ResourceCaps


class TestResourceCaps:
    def test_defaults_are_non_zero(self) -> None:
        caps = ResourceCaps()
        assert caps.mem_mib > 0
        assert caps.cpu_cores > 0
        assert caps.pids > 0
        assert caps.wall_time_s > 0
        assert caps.fs_write_mib > 0

    def test_is_frozen(self) -> None:
        caps = ResourceCaps()
        with pytest.raises(ValidationError):
            caps.mem_mib = 4096  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field",
        ["mem_mib", "cpu_cores", "pids", "wall_time_s", "fs_write_mib"],
    )
    def test_rejects_zero(self, field: str) -> None:
        with pytest.raises(ValidationError):
            ResourceCaps(**{field: 0})

    @pytest.mark.parametrize(
        "field",
        ["mem_mib", "cpu_cores", "pids", "wall_time_s", "fs_write_mib"],
    )
    def test_rejects_negative(self, field: str) -> None:
        with pytest.raises(ValidationError):
            ResourceCaps(**{field: -1})

    def test_accepts_tighter_caps(self) -> None:
        caps = ResourceCaps(mem_mib=512, cpu_cores=1)
        assert caps.mem_mib == 512
        assert caps.cpu_cores == 1


class TestExecSpec:
    def test_network_denied_by_default(self) -> None:
        assert ExecSpec(code="print(1)").network is False

    def test_env_allowlist_from_empty(self) -> None:
        spec = ExecSpec(code="print(1)")
        # Allowlist-from-empty: only the minimal secret-free set, nothing host-wide.
        assert spec.env_allow == DEFAULT_ENV_ALLOW
        assert "PATH" in spec.env_allow
        assert all("TOKEN" not in name and "SECRET" not in name for name in spec.env_allow)

    def test_language_python_only(self) -> None:
        assert ExecSpec(code="print(1)").language == "python"
        with pytest.raises(ValidationError):
            ExecSpec(code="puts 1", language="ruby")  # type: ignore[arg-type]

    def test_caps_default_present_and_non_zero(self) -> None:
        spec = ExecSpec(code="print(1)")
        assert spec.caps.mem_mib > 0

    def test_is_frozen(self) -> None:
        spec = ExecSpec(code="print(1)")
        with pytest.raises(ValidationError):
            spec.network = True  # type: ignore[misc]

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ExecSpec(code="print(1)", timeout_s=0)


class TestExecResult:
    def _caps(self) -> ResourceCaps:
        return ResourceCaps()

    def test_ok_provenance(self) -> None:
        res = ExecResult.ok(
            stdout="hi",
            stderr="",
            exit_code=0,
            backend_used="bwrap",
            network_enabled=False,
            caps_applied=self._caps(),
            duration_ms=12,
        )
        assert res.exit_reason == "ok"
        assert res.backend_used == "bwrap"
        assert res.network_enabled is False
        assert res.exit_code == 0
        assert res.caps_applied.mem_mib > 0
        assert res.stdout_truncated is False

    def test_timeout_factory(self) -> None:
        res = ExecResult.timed_out(
            stdout="partial",
            stderr="",
            backend_used="docker",
            network_enabled=True,
            caps_applied=self._caps(),
            duration_ms=30000,
        )
        assert res.exit_reason == "timeout"
        assert res.exit_code is None
        assert res.network_enabled is True

    def test_error_factory_denied(self) -> None:
        res = ExecResult.error(
            reason="denied",
            message="no sandbox backend available",
            backend_used="none",
            caps_applied=self._caps(),
        )
        assert res.exit_reason == "denied"
        assert res.exit_code is None
        assert "no sandbox" in res.stderr

    def test_stdout_truncation_accounted(self) -> None:
        big = "x" * (MAX_STDOUT_BYTES + 5000)
        res = ExecResult.ok(
            stdout=big,
            stderr="",
            exit_code=0,
            backend_used="bwrap",
            network_enabled=False,
            caps_applied=self._caps(),
            duration_ms=1,
        )
        assert res.stdout_truncated is True
        assert "dropped" in res.stdout
        assert len(res.stdout.encode("utf-8")) < len(big)

    def test_stderr_truncation_accounted(self) -> None:
        big = "e" * (MAX_STDERR_BYTES + 5000)
        res = ExecResult.ok(
            stdout="",
            stderr=big,
            exit_code=1,
            backend_used="bwrap",
            network_enabled=False,
            caps_applied=self._caps(),
            duration_ms=1,
        )
        assert res.stderr_truncated is True

    def test_no_truncation_under_cap(self) -> None:
        res = ExecResult.ok(
            stdout="small",
            stderr="small",
            exit_code=0,
            backend_used="bwrap",
            network_enabled=False,
            caps_applied=self._caps(),
            duration_ms=1,
        )
        assert res.stdout_truncated is False
        assert res.stderr_truncated is False
        assert res.stdout == "small"

    def test_is_frozen(self) -> None:
        res = ExecResult.ok(
            stdout="",
            stderr="",
            exit_code=0,
            backend_used="bwrap",
            network_enabled=False,
            caps_applied=self._caps(),
            duration_ms=1,
        )
        with pytest.raises(ValidationError):
            res.exit_code = 1  # type: ignore[misc]
