"""Sandbox-substrate safety constants — the load-bearing secure-by-default rails.

Code execution is the KEYSTONE trust boundary (E11): a backend runs untrusted /
LLM-generated code, so EVERY default here is conservative-by-design. Two rules
are non-negotiable and encoded as constants so every enforcement site reads ONE
source of truth (ARCH-90 / placement-voting, mirroring
:mod:`stackowl.process.limits`):

* **Mandatory non-zero resource caps** — a backend that cannot enforce a cap must
  REFUSE, never run uncapped. The conservative defaults below are inherited by
  every :class:`~stackowl.sandbox.spec.ExecSpec` that does not override them.
* **Bounded captured output** — a chatty (or hostile) program cannot exhaust
  memory: stdout/stderr are each capped and truncation is accounted, never silent.

NO actual execution happens at this layer — these are the policy numbers the
backends (E11-S2 Docker / E11-S3 bwrap) will enforce, and the selector / spec
validation read here so the rails are defined once.
"""

from __future__ import annotations

# --- Mandatory resource caps (non-zero defaults; a backend that cannot enforce a
#     cap must REFUSE, never run uncapped). Conservative for a personal-assistant
#     host; capability-probed hosts may run smaller, never larger by default.

# Default memory ceiling for one run, in MiB.
DEFAULT_MEM_MIB = 2048
# Default CPU core allotment for one run.
DEFAULT_CPU_CORES = 2
# Default max process/thread count inside the sandbox (fork-bomb rail).
DEFAULT_PIDS = 256
# Default wall-clock lifetime for one run, in seconds (hard kill past this).
DEFAULT_WALL_TIME_S = 30
# Default writable scratch (tmpfs) size for the run, in MiB.
DEFAULT_FS_WRITE_MIB = 256

# Per-run timeout used by :class:`ExecSpec` when a caller does not specify one.
# Distinct from the cap's ``wall_time_s`` (a backend enforces the cap; the spec's
# ``timeout_s`` is the requested budget, clamped to the cap by a backend in S2/S3).
DEFAULT_TIMEOUT_S = 30

# --- Captured-output ceilings (honest truncation; never silent). Ported policy
#     numbers from the research ADR (MAX_STDOUT_BYTES / MAX_STDERR_BYTES).

# Max stdout bytes retained per run; older bytes dropped with truncation noted.
MAX_STDOUT_BYTES = 50_000
# Max stderr bytes retained per run; older bytes dropped with truncation noted.
MAX_STDERR_BYTES = 10_000

# --- Environment allowlist (allowlist-FROM-EMPTY). The child inherits NOTHING by
#     default except this minimal, secret-free set; a spec may extend it, never
#     start broader. Secrets/tokens never appear here.
DEFAULT_ENV_ALLOW: tuple[str, ...] = ("PATH", "HOME", "LANG")

# --- Global concurrency cap PLACEHOLDER (enforced by the registry in E11-S6).
#     A personal assistant rarely needs many parallel sandboxes; past this the
#     S6 registry will refuse (structured, never raising) so a caller cannot
#     exhaust the host with concurrent containers. Defined here so the number
#     lives with the other rails; NOT enforced at this layer.
MAX_CONCURRENT_SANDBOXES = 4
