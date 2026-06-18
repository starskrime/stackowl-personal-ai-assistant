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
        assert all("TOKEN" not in name and "SECRET" not in name for name in spec.env_allow)

    def test_default_env_allow_does_not_forward_host_path(self) -> None:
        # F162 — the sandbox uses a FIXED sanitized PATH, never the host's PATH
        # value. So PATH is no longer in the default forwarded allowlist.
        assert "PATH" not in DEFAULT_ENV_ALLOW

    @pytest.mark.parametrize(
        "secret_name",
        [
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
            "GITHUB_TOKEN",
            "DB_PASSWORD",
            "my_secret",
            "service_key",
            "KEY_PRIMARY",
        ],
    )
    def test_env_allow_refuses_secret_named_var(self, secret_name: str) -> None:
        # F162 — env_allow is fail-closed: a name matching a redaction pattern
        # (apikey/token/secret/password/*_key/key_*) is REFUSED at construction so
        # a token-bearing host var can never be forwarded into the sandbox.
        with pytest.raises(ValidationError, match="secret"):
            ExecSpec(code="print(1)", env_allow=("LANG", secret_name))

    def test_env_allow_accepts_safe_names(self) -> None:
        spec = ExecSpec(code="print(1)", env_allow=("LANG", "TZ", "LC_ALL"))
        assert spec.env_allow == ("LANG", "TZ", "LC_ALL")

    @pytest.mark.parametrize(
        "danger_name",
        [
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "BASH_ENV",
            "IFS",
            "DYLD_INSERT_LIBRARIES",
            "AWS_ACCESS_KEY_ID",
            # Case-insensitive, exact-name match.
            "ld_preload",
            "Bash_Env",
        ],
    )
    def test_env_allow_refuses_code_injection_vectors(self, danger_name: str) -> None:
        # SEC-2 — env-based code-injection / leak vectors are denied even though
        # they are not credential-NAMED: forwarding LD_PRELOAD/BASH_ENV/IFS/etc.
        # into the child lets a host value alter how the sandboxed process loads
        # or interprets code. Fail-closed at construction.
        with pytest.raises(ValidationError):
            ExecSpec(code="print(1)", env_allow=("LANG", danger_name))

    def test_env_allow_does_not_overmatch_dangerous_substrings(self) -> None:
        # Exact-name (not suffix-glob): a benign var whose name merely CONTAINS a
        # denied token (e.g. "MY_IFS_CONFIG") is not falsely rejected by SEC-2.
        spec = ExecSpec(code="print(1)", env_allow=("LANG", "MY_IFS_CONFIG"))
        assert "MY_IFS_CONFIG" in spec.env_allow

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
