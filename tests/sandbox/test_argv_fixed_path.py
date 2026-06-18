"""SEC-2 / F162 — the sandbox argv uses a FIXED sanitized PATH, never the host's.

Pure argv-builder tests (no process spawned): assert that both backends set
``PATH`` to the fixed :data:`SANDBOX_PATH` and never forward the host PATH value.
"""

from __future__ import annotations

from pathlib import Path

from stackowl.sandbox.argv import BwrapArgvBuilder
from stackowl.sandbox.docker_argv import DockerArgvBuilder
from stackowl.sandbox.limits import SANDBOX_PATH
from stackowl.sandbox.spec import ExecSpec


def _env_pairs(argv: list[str], flags: tuple[str, ...]) -> dict[str, str]:
    """Extract NAME=value (docker --env) or (name, value) (bwrap --setenv) pairs."""
    pairs: dict[str, str] = {}
    i = 0
    while i < len(argv):
        if argv[i] in flags:
            if argv[i] == "--setenv":  # bwrap: name then value as two tokens
                pairs[argv[i + 1]] = argv[i + 2]
                i += 3
                continue
            name, _, value = argv[i + 1].partition("=")  # docker: NAME=value
            pairs[name] = value
            i += 2
            continue
        i += 1
    return pairs


def test_bwrap_sets_fixed_sanitized_path(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/HOST/POISONED/bin:/evil")
    spec = ExecSpec(code="print(1)", env_allow=("PATH", "LANG"))
    argv = BwrapArgvBuilder().build(spec, Path("/tmp/ws"))
    env = _env_pairs(argv, ("--setenv",))
    assert env.get("PATH") == SANDBOX_PATH
    assert "/HOST/POISONED/bin" not in env.get("PATH", "")


def test_docker_sets_fixed_sanitized_path(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/HOST/POISONED/bin:/evil")
    spec = ExecSpec(code="print(1)", env_allow=("PATH", "LANG"))
    argv = DockerArgvBuilder().build(
        spec=spec,
        image="img",
        container_name="cont",
        code_dir=Path("/tmp/ws"),
        seccomp_profile=Path("/tmp/seccomp.json"),
    )
    env = _env_pairs(argv, ("--env", "-e"))
    assert env.get("PATH") == SANDBOX_PATH
    assert "/HOST/POISONED/bin" not in env.get("PATH", "")
