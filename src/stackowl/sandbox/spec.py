"""Sandbox value models — the request (:class:`ExecSpec` + :class:`ResourceCaps`)
and the provenance-carrying outcome (:class:`ExecResult`).

All three are FROZEN Pydantic models (immutable value objects — a spec/result is
passed across the trust boundary and must never be mutated in place). The models
encode the secure-by-default policy declaratively so it cannot be forgotten by a
backend:

* :class:`ResourceCaps` — EVERY cap is MANDATORY and non-zero (a ``<= 0`` value is
  rejected at construction). A backend that cannot enforce a cap must REFUSE the
  run rather than execute uncapped — the cap is a promise, not a hint.
* :class:`ExecSpec` — ``network`` DENIES by default; ``env_allow`` is an
  allowlist-FROM-EMPTY (the child inherits nothing but the minimal secret-free
  set). Python-only for the MVP (``language`` is a one-value ``Literal``).
* :class:`ExecResult` — carries full PROVENANCE so a reader can audit exactly what
  ran and under which guarantees: which ``backend_used``, whether the network was
  actually ``network_enabled``, the ``caps_applied``, and a structured
  ``exit_reason``. Captured streams are capped with honest truncation flags.

NO execution lives here — these are pure data. The backends (E11-S2/S3) build an
:class:`ExecResult` via the factory helpers (:meth:`ExecResult.ok` /
:meth:`ExecResult.timed_out` / :meth:`ExecResult.error`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from stackowl.sandbox.limits import (
    DEFAULT_CPU_CORES,
    DEFAULT_ENV_ALLOW,
    DEFAULT_FS_WRITE_MIB,
    DEFAULT_MEM_MIB,
    DEFAULT_PIDS,
    DEFAULT_TIMEOUT_S,
    DEFAULT_WALL_TIME_S,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
)

__all__ = ["ExecResult", "ExecSpec", "ExitReason", "ResourceCaps"]

# The structured terminal reason for a run. NOT a free-form string so callers can
# branch deterministically (ok vs a denial vs a resource kill vs sandbox failure).
ExitReason = Literal["ok", "timeout", "oom", "killed", "sandbox_error", "denied"]


class ResourceCaps(BaseModel):
    """Mandatory, non-zero resource ceilings for one sandboxed run.

    Every field is REQUIRED to be ``> 0`` (validated below). A backend that cannot
    enforce one of these MUST refuse the run — there is no "uncapped" state. The
    conservative defaults come from :mod:`stackowl.sandbox.limits` so the numbers
    live in one place; a caller may tighten them, and a capability-aware backend
    may run smaller, but nothing here is ever zero/unlimited.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mem_mib: int = DEFAULT_MEM_MIB
    cpu_cores: int = DEFAULT_CPU_CORES
    pids: int = DEFAULT_PIDS
    wall_time_s: int = DEFAULT_WALL_TIME_S
    fs_write_mib: int = DEFAULT_FS_WRITE_MIB

    @field_validator("mem_mib", "cpu_cores", "pids", "wall_time_s", "fs_write_mib")
    @classmethod
    def _must_be_positive(cls, value: int, info: ValidationInfo) -> int:
        """Reject ``<= 0`` — caps are mandatory and non-zero (no uncapped runs)."""
        if value <= 0:
            field = info.field_name or "cap"
            raise ValueError(
                f"resource cap '{field}' must be a positive non-zero value "
                f"(got {value}); a sandbox is never run uncapped"
            )
        return value


class ExecSpec(BaseModel):
    """A request to run code in a sandbox — secure-by-default.

    ``network`` is DENIED unless explicitly opted in (and a backend additionally
    has to support network egress, else the selector routes elsewhere or refuses).
    ``env_allow`` is an allowlist-FROM-EMPTY: the child sees only these variables
    (default: the minimal secret-free :data:`DEFAULT_ENV_ALLOW`), never the host's
    full environment. ``language`` is Python-only for the MVP.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    language: Literal["python"] = "python"
    # DENY-by-default: the run gets no network unless this is explicitly True AND a
    # network-capable backend is selected.
    network: bool = False
    caps: ResourceCaps = Field(default_factory=ResourceCaps)
    # Allowlist-from-empty: only these env names are forwarded to the child.
    env_allow: tuple[str, ...] = DEFAULT_ENV_ALLOW
    timeout_s: int = DEFAULT_TIMEOUT_S
    # Optional input piped to the child's stdin.
    stdin: str | None = None
    # Correlation id for the consent prompt + audit trail (set by the execute_code
    # tool from the turn's session; backends log it, never use it for isolation).
    session_id: str = ""

    @field_validator("timeout_s")
    @classmethod
    def _timeout_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(f"timeout_s must be positive non-zero (got {value})")
        return value

    @field_validator("env_allow")
    @classmethod
    def _env_allow_no_secret_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Fail-closed: refuse any env name that looks credential-bearing (F162).

        ``env_allow`` forwards the HOST value of each named variable into the
        sandbox, so a credential-named var would smuggle a secret across the trust
        boundary. The guard is the central log-redaction predicate
        (:func:`stackowl.infra.observability._is_sensitive`) PLUS substring checks
        for the core credential words (``secret`` / ``token`` / ``password`` /
        ``passwd`` / ``pwd`` / ``api_key`` / ``apikey`` / ``credential``). The
        sandbox is deliberately STRICTER than the log redactor (over-restriction is
        the safe direction for a forwarded-value guard); the log path is unchanged,
        so the two never silently drift in the LENIENT direction. Only the
        OFFENDING NAMES are surfaced (never any value).

        SEC-2 — beyond credential NAMES, an exact-name (case-insensitive) denylist
        rejects env-based code-injection / leak vectors that are not themselves
        credential-named: the dynamic-loader hooks ``LD_PRELOAD`` /
        ``LD_LIBRARY_PATH`` / ``DYLD_INSERT_LIBRARIES`` (inject a library into the
        child), ``BASH_ENV`` / ``IFS`` (alter shell load/parse), and
        ``AWS_ACCESS_KEY_ID`` (the non-secret-named half of an AWS pair whose
        SECRET half is already caught). These are fail-closed too.
        """
        # Imported lazily to avoid a module-import cycle (observability imports the
        # trace context; this keeps spec.py free of that edge at import time).
        from stackowl.infra.observability import _is_sensitive

        # Substring tokens (lowercased name): credential words that the pattern-based
        # log predicate (which is anchored/glob) may miss, e.g. ``DB_PASSWORD``.
        _CREDENTIAL_SUBSTRINGS = (
            "secret", "token", "password", "passwd", "pwd",
            "api_key", "apikey", "credential",
        )
        # SEC-2 — env-based code-injection / leak vectors that are NOT credential-
        # NAMED but are dangerous to forward: the dynamic-loader hooks
        # (LD_PRELOAD/LD_LIBRARY_PATH/DYLD_INSERT_LIBRARIES) let a host value inject
        # a library into the child, BASH_ENV/IFS alter how a shell loads/parses, and
        # AWS_ACCESS_KEY_ID is the (non-secret-named) half of an AWS credential pair
        # whose SECRET half is already caught. EXACT-name, case-insensitive (these
        # are not credential-suffix globs). Fail-closed.
        _DENIED_EXACT_NAMES = frozenset({
            "ld_preload", "ld_library_path", "bash_env", "ifs",
            "dyld_insert_libraries", "aws_access_key_id",
        })
        offending = [
            name
            for name in value
            if _is_sensitive(name)
            or any(tok in name.lower() for tok in _CREDENTIAL_SUBSTRINGS)
            or name.lower() in _DENIED_EXACT_NAMES
        ]
        if offending:
            raise ValueError(
                "env_allow refuses secret-named / injection-vector variable(s) "
                f"{sorted(offending)} — a credential-named host var (matches a "
                "secret/redaction pattern) or a code-injection vector "
                "(LD_PRELOAD/LD_LIBRARY_PATH/DYLD_INSERT_LIBRARIES/BASH_ENV/IFS/"
                "AWS_ACCESS_KEY_ID) must never be forwarded into the sandbox"
            )
        return value


class ExecResult(BaseModel):
    """The provenance-carrying outcome of one run. Frozen; built via factories.

    A backend NEVER raises for an operational failure — it returns one of these
    instead, so a caller always learns what happened (``exit_reason``) and under
    what guarantees (``backend_used`` / ``network_enabled`` / ``caps_applied``).
    Captured streams are capped to :data:`MAX_STDOUT_BYTES` /
    :data:`MAX_STDERR_BYTES` with ``stdout_truncated`` / ``stderr_truncated``
    flagging an honest, non-silent truncation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stdout: str
    stderr: str
    exit_code: int | None
    exit_reason: ExitReason
    backend_used: str
    network_enabled: bool
    caps_applied: ResourceCaps
    duration_ms: int
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    # ------------------------------------------------------------- construction
    @staticmethod
    def _cap(text: str, limit: int) -> tuple[str, bool]:
        """Cap ``text`` to ``limit`` bytes (utf-8), keeping the most recent tail.

        Returns ``(capped_text, truncated)``. The TAIL is retained (the end of a
        program's output is what a reader usually needs) and the loss is flagged —
        never a silent drop. Never raises.
        """
        raw = text.encode("utf-8", errors="replace")
        if len(raw) <= limit:
            return text, False
        kept = raw[-limit:].decode("utf-8", errors="replace")
        dropped = len(raw) - len(raw[-limit:])
        return f"...[{dropped} earlier bytes dropped]...\n{kept}", True

    @classmethod
    def ok(
        cls,
        *,
        stdout: str,
        stderr: str,
        exit_code: int,
        backend_used: str,
        network_enabled: bool,
        caps_applied: ResourceCaps,
        duration_ms: int,
    ) -> ExecResult:
        """A run that completed (the program's own exit code is preserved)."""
        capped_out, out_trunc = cls._cap(stdout, MAX_STDOUT_BYTES)
        capped_err, err_trunc = cls._cap(stderr, MAX_STDERR_BYTES)
        return cls(
            stdout=capped_out,
            stderr=capped_err,
            exit_code=exit_code,
            exit_reason="ok",
            backend_used=backend_used,
            network_enabled=network_enabled,
            caps_applied=caps_applied,
            duration_ms=duration_ms,
            stdout_truncated=out_trunc,
            stderr_truncated=err_trunc,
        )

    @classmethod
    def timed_out(
        cls,
        *,
        stdout: str,
        stderr: str,
        backend_used: str,
        network_enabled: bool,
        caps_applied: ResourceCaps,
        duration_ms: int,
    ) -> ExecResult:
        """A run killed for exceeding its wall-time budget."""
        capped_out, out_trunc = cls._cap(stdout, MAX_STDOUT_BYTES)
        capped_err, err_trunc = cls._cap(stderr, MAX_STDERR_BYTES)
        return cls(
            stdout=capped_out,
            stderr=capped_err,
            exit_code=None,
            exit_reason="timeout",
            backend_used=backend_used,
            network_enabled=network_enabled,
            caps_applied=caps_applied,
            duration_ms=duration_ms,
            stdout_truncated=out_trunc,
            stderr_truncated=err_trunc,
        )

    @classmethod
    def error(
        cls,
        *,
        reason: ExitReason,
        message: str,
        backend_used: str,
        caps_applied: ResourceCaps,
        network_enabled: bool = False,
        duration_ms: int = 0,
    ) -> ExecResult:
        """A run that could not complete — a denial or a sandbox failure.

        ``reason`` is the structured cause (``denied`` / ``sandbox_error`` /
        ``oom`` / ``killed``); ``message`` is surfaced on ``stderr`` so the caller
        sees an explanation. ``exit_code`` is ``None`` (no program exit happened).
        """
        capped_err, err_trunc = cls._cap(message, MAX_STDERR_BYTES)
        return cls(
            stdout="",
            stderr=capped_err,
            exit_code=None,
            exit_reason=reason,
            backend_used=backend_used,
            network_enabled=network_enabled,
            caps_applied=caps_applied,
            duration_ms=duration_ms,
            stdout_truncated=False,
            stderr_truncated=err_trunc,
        )
