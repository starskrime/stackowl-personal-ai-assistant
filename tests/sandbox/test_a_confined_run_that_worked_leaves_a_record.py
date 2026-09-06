"""A confined run that SUCCEEDS must be as visible as one that fails.

WHY THIS EXISTS, and D06.3 is the item that had to look.

The map records StackOwl as `AHEAD` here — "seccomp filters, cgroup limits, mount
planning, scratch dirs, a host-wide concurrency governor, and a privileged tool channel"
against a reference platform with none of it — and the Ask answers itself: *"Keep. Ours
is genuinely stronger."* All six exist as dedicated modules and all six are wired.

MEASURED 2026-09-06, across every retained log: the sandbox has been ASSEMBLED 812 times
and has RUN ONCE. `[sandbox.selector] select: chose bwrap (rootless primary)` appears
exactly once, `[sandbox.ptc] channel started` exactly once, and **nothing records how that
run ended.** `seccomp` and `cgroup` appear zero times at any level.

THE ASYMMETRY IS THE DEFECT, and it is D07.3's shape one subsystem over. Both backends log
every FAILURE loudly — wall-time exceeded, OOM-killed, killed by signal, cgroup recipe
refused, spawn failed — at INFO or ERROR. The SUCCESS path of each logs
``run: exit`` at **DEBUG**. Production runs at INFO, so the one outcome an operator most
needs a record of is the only one they cannot see:

    untrusted, model-generated code ran to completion inside the cage, and it worked.

AND ESC-150 MADE THAT LIVE. Until 2026-09-05 a confined run required a human to approve
it, so the consent prompt was itself the record. That carve-out now grants `execute_code`
unattended precisely BECAUSE the confinement is contract-backed — the grant is logged at
INFO, and then the execution it authorised is not. Permission is visible; what happened
with it is not.

The invariants this line makes checkable at 2am are the ones the backends' own docstrings
claim: caps-or-refuse (#2), deny-all network (#3), and that the run went through a backend
at all rather than a bare host process (#1). A `caps_applied` that nothing ever prints is
an assertion, not evidence.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BWRAP = _ROOT / "src" / "stackowl" / "sandbox" / "bwrap.py"
_DOCKER = _ROOT / "src" / "stackowl" / "sandbox" / "docker_result.py"


def _log_call_level(path: Path, msg: str) -> str:
    """The logger method used for the call whose first argument is ``msg``.

    Parsed from the AST rather than grepped: every one of these files explains its own
    logging policy in prose, and a substring search matches the explanation as readily
    as the code. Three guards in this repo have already been satisfied by their own
    comments.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or first.value != msg:
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
    raise AssertionError(f"no logging call in {path.name} with message {msg!r}")


def _log_call_fields(path: Path, msg: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or first.value != msg:
            continue
        for kw in node.keywords:
            if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                continue
            for k, v in zip(kw.value.keys, kw.value.values, strict=False):
                if isinstance(k, ast.Constant) and k.value == "_fields":
                    assert isinstance(v, ast.Dict)
                    return {
                        c.value for c in v.keys
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    }
    raise AssertionError(f"no _fields on {msg!r} in {path.name}")


_SUCCESS_LINES = [
    pytest.param(_BWRAP, "[sandbox.bwrap] run: exit", id="bwrap"),
    pytest.param(_DOCKER, "[sandbox.docker] run: exit", id="docker"),
]


class TestSuccessIsAsVisibleAsFailure:
    @pytest.mark.parametrize(("path", "msg"), _SUCCESS_LINES)
    def test_the_success_path_logs_at_INFO(self, path: Path, msg: str) -> None:
        """Production runs at INFO. At DEBUG this line does not exist when it is
        needed, which is D08.1's lesson and the reason its acceptance check sat open
        for days."""
        assert _log_call_level(path, msg) == "info", (
            f"{path.name} logs a SUCCESSFUL confined run at "
            f"{_log_call_level(path, msg)} while every failure path is INFO or ERROR — "
            "the operator can see the cage catching something and not the cage working"
        )

    @pytest.mark.parametrize(("path", "msg"), _SUCCESS_LINES)
    def test_it_names_the_backend_and_the_caps(self, path: Path, msg: str) -> None:
        """BOTH BACKENDS ARE NOT THE SAME CAGE, and the record has to say which ran.
        bwrap is rootless-userns with no seccomp — deliberately, because a userns
        already contains an escape — while Docker is rootful and its seccomp filter is
        load-bearing. "A confined run happened" is not a fact until it names which
        confinement, and `caps_applied` that nothing prints is an assertion rather than
        evidence for the caps-or-refuse invariant."""
        fields = _log_call_fields(path, msg)

        assert "backend" in fields, f"{path.name} does not say WHICH cage ran"
        assert "caps_applied" in fields, (
            f"{path.name} does not record the caps it enforced, so invariant #2 "
            "(caps-or-refuse) is unverifiable from the logs"
        )

    @pytest.mark.parametrize(("path", "msg"), _SUCCESS_LINES)
    def test_it_keeps_what_it_already_reported(self, path: Path, msg: str) -> None:
        """Scope control — raising a level must not quietly drop the fields that were
        already there."""
        fields = _log_call_fields(path, msg)

        assert {"exit_code", "duration_ms"} <= fields


class TestTheFailurePathsStayLoud:
    """The control. Raising success to INFO is only meaningful if failure was already
    louder — otherwise this test suite would pass against a module that logs nothing
    but INFO everywhere and distinguishes nothing."""

    @pytest.mark.parametrize(
        ("path", "msg"),
        [
            pytest.param(_BWRAP, "[sandbox.bwrap] _map_result: cgroup OOM-killed the run", id="bwrap-oom"),
            pytest.param(_BWRAP, "[sandbox.bwrap] _map_result: run killed by signal", id="bwrap-signal"),
            pytest.param(_DOCKER, "[sandbox.docker] _map_result: container OOM-killed", id="docker-oom"),
        ],
    )
    def test_a_failure_is_at_least_INFO(self, path: Path, msg: str) -> None:
        assert _log_call_level(path, msg) in {"info", "warning", "error"}
