"""RunTestsTool — structured pass/fail parsing, never raw stdout as the answer.

Runs against real stub commands (echo/python -c printing realistic pytest-
shaped output) rather than mocking the subprocess layer, since the parser
itself — not the spawn mechanics — is what's under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.pipeline.acceptance_authority import TestsPassed
from stackowl.tools.system.run_tests import RunTestsTool

_PYTEST_ALL_PASS = (
    "collected 3 items\n\n"
    "tests/test_a.py ...                                                  [100%]\n\n"
    "===================== 3 passed in 0.12s ======================\n"
)

_PYTEST_WITH_FAILURES = (
    "collected 3 items\n\n"
    "tests/test_a.py .F.                                                  [100%]\n\n"
    "=========================== FAILURES ===========================\n"
    "___________________________ test_b ______________________________\n"
    "    assert 1 == 2\n"
    "E   AssertionError\n\n"
    "=================== short test summary info ====================\n"
    "FAILED tests/test_a.py::test_b - AssertionError: assert 1 == 2\n"
    "===================== 1 failed, 2 passed in 0.15s ======================\n"
)


def _echo_command(text: str, tmp_path: Path) -> str:
    script = tmp_path / "stub.py"
    script.write_text(f"import sys\nsys.stdout.write({text!r})\n")
    return f"python3 {script}"


@pytest.mark.asyncio
async def test_all_passed_parses_structured_counts(tmp_path: Path) -> None:
    command = _echo_command(_PYTEST_ALL_PASS, tmp_path)

    result = await RunTestsTool()(command=command, workdir=str(tmp_path))

    assert result.success is True
    record = json.loads(result.output)
    assert record["framework"] == "summary_counts"
    assert record["total"] == 3
    assert record["passed"] == 3
    assert record["failed"] == 0
    assert record["all_passed"] is True
    assert record["failures"] == []


@pytest.mark.asyncio
async def test_failures_parsed_with_names_and_reasons(tmp_path: Path) -> None:
    script = tmp_path / "stub.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({_PYTEST_WITH_FAILURES!r})\n"
        "sys.exit(1)\n"
    )
    command = f"python3 {script}"

    result = await RunTestsTool()(command=command, workdir=str(tmp_path))

    assert result.success is True  # the RUN completed — tests failing isn't a tool failure
    record = json.loads(result.output)
    assert record["framework"] == "pytest"
    assert record["total"] == 3
    assert record["failed"] == 1
    assert record["all_passed"] is False
    assert record["failures"] == [
        {"name": "tests/test_a.py::test_b", "message": "AssertionError: assert 1 == 2"}
    ]


@pytest.mark.asyncio
async def test_unparseable_output_falls_back_honestly(tmp_path: Path) -> None:
    command = _echo_command("some custom test runner with no recognizable summary\n", tmp_path)

    result = await RunTestsTool()(command=command, workdir=str(tmp_path))

    assert result.success is True
    record = json.loads(result.output)
    assert record["framework"] == "unknown"
    assert record["total"] == 0
    assert record["all_passed"] is False  # never fabricate a pass when nothing was parsed
    assert "raw_tail" in record


@pytest.mark.asyncio
async def test_command_that_never_runs_is_a_tool_failure(tmp_path: Path) -> None:
    result = await RunTestsTool()(command="definitely-not-a-real-binary-xyz", workdir=str(tmp_path))

    assert result.success is False


@pytest.mark.asyncio
async def test_empty_command_refused(tmp_path: Path) -> None:
    result = await RunTestsTool()(command="   ", workdir=str(tmp_path))

    assert result.success is False
    assert result.side_effect_committed is False


def test_post_condition_declares_tests_passed_from_own_record() -> None:
    tool = RunTestsTool()
    from stackowl.tools.base import ToolResult

    result = ToolResult(
        success=True,
        output=json.dumps({"total": 3, "failed": 1, "errors": 0, "framework": "pytest"}),
        duration_ms=1.0,
    )

    declared = tool.post_condition({}, result)

    assert isinstance(declared, TestsPassed)
    assert declared.total == 3
    assert declared.failed == 1


def test_post_condition_none_when_no_tests_ran() -> None:
    tool = RunTestsTool()
    from stackowl.tools.base import ToolResult

    result = ToolResult(
        success=True,
        output=json.dumps({"total": 0, "failed": 0, "errors": 0, "framework": "unknown"}),
        duration_ms=1.0,
    )

    assert tool.post_condition({}, result) is None
