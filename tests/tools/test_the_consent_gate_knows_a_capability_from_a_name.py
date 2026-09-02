"""Consent gated a tool NAME while the capability ran freely under another.

MEASURED 2026-09-01 across the retained logs, both halves of one contradiction:

* ``execute_code`` was refused **26 times** — every one on the RCA lane, every
  one "[consent] autonomous grant REFUSED — this is always-ask and no human is
  attached". It is in ``_DEFAULT_ALWAYS_ASK_TOOLS`` on a recorded decision: the
  E11/E12/E13 party reviews, "code execution, GUI control, and Home-Assistant
  locks/alarms are never relaxed".
* ``shell`` invoked a general-purpose interpreter **110 times out of 153 (72%)**
  — ``python3 - <<PY``, ``node -e`` — unattended, unprompted, and unrecorded as
  code execution.

``shell``'s only consent gate is ``is_catastrophic``, which looks for ``rm -rf
/`` and fork bombs. A heredoc of arbitrary Python is not that shape, so it ran
silently. The recorded decision was NAMED and never ENFORCED, and the refusal
bought nothing: the model reached the identical effect through ``shell`` on the
next round while the RCA lost its evidence tool and a turn.

THE ROOT CAUSE IS THE KEY, NOT THE POLICY. The gate asked "what is this tool
called" where the risk belongs to "what does this call DO". Nothing anywhere
computed the second question, so the two paths could not have agreed even in
principle — this is the "two copies of one rule" shape with one copy missing
entirely.

WHAT SHIPS HERE, AND WHAT DOES NOT. :data:`CODE_EXECUTION` and
:func:`launches_an_interpreter` are the one source for "does this call execute
code", and the category joins the always-ask set — which changes NOTHING for
``execute_code`` (already always-ask by name) and gives the rule a single
address. The shell path is INSTRUMENTED, not gated: gating it would refuse the
self-heal loop its main evidence tool, and which way to close the gap is a risk
decision recorded as ESC-98. What was missing under either answer is that the
platform could not SEE the divergence. Now it can count it.
"""

from __future__ import annotations

import pytest

from stackowl.tools.consent import (
    _DEFAULT_ALWAYS_ASK_CATEGORIES,
    _DEFAULT_ALWAYS_ASK_TOOLS,
    CODE_EXECUTION,
    launches_an_interpreter,
)

# Command shapes taken verbatim from the live shell corpus.
_REAL_CODE_EXECUTION = [
    "python3 - <<PY\nimport sqlite3\nPY",
    "python3 gmail_assist.py digest 24",
    "python3 downloads/rca_count.py",
    "cd downloads && node -e 'console.log(1)'",
]
_REAL_ORDINARY = [
    "ls -lah /home/boss/.stackowl/logs/",
    "grep -n 'def cmd_get' gmail_assist.py | head -30",
    "cd downloads && for f in t1 t2; do sed 's/,/\\n/g' $f.txt | awk NF; done",
    "git status",
]


@pytest.mark.parametrize("command", _REAL_CODE_EXECUTION)
def test_a_real_interpreter_invocation_is_code_execution(command: str) -> None:
    """These four ran unattended through ``shell`` while ``execute_code`` was
    being refused on the same lanes."""
    assert launches_an_interpreter(command) is True


@pytest.mark.parametrize("command", _REAL_ORDINARY)
def test_an_ordinary_command_is_not_code_execution(command: str) -> None:
    """The expensive direction. Over-classifying makes the signal useless and,
    if this is ever enforced, would gate ``ls``."""
    assert launches_an_interpreter(command) is False


def test_bash_itself_is_not_the_signal() -> None:
    """``bash``/``sh`` are deliberately absent from the vocabulary: the shell
    tool IS bash, so including them would classify 100% of commands and the
    measurement would carry no information."""
    assert launches_an_interpreter("bash -c 'ls'") is False
    assert launches_an_interpreter("sh script.sh") is False


def test_an_interpreter_cannot_hide_behind_a_separator() -> None:
    """A pipeline, a chain or a command substitution must not conceal it — an
    evasion that works is worse than no classifier, because the count would read
    as safety."""
    for command in (
        "ls && python3 x.py",
        "echo hi | python3 -",
        "$(node -e 1)",
        "`python3 -c 'print(1)'`",
        "true; ruby -e 'puts 1'",
    ):
        assert launches_an_interpreter(command) is True, command


def test_a_path_qualified_interpreter_is_still_one() -> None:
    assert launches_an_interpreter("/usr/bin/python3 -c 'print(1)'") is True


def test_a_lookalike_name_is_not_an_interpreter() -> None:
    """``python-config`` and ``noderunner`` are not interpreters; matching on a
    substring would classify them and inflate the count."""
    assert launches_an_interpreter("python3-config --includes") is False
    assert launches_an_interpreter("noderunner start") is False


def test_an_empty_or_broken_command_classifies_false() -> None:
    """Never raises: a classification failure must not cost a command its run."""
    assert launches_an_interpreter("") is False
    assert launches_an_interpreter(None) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# One source for the rule                                                      #
# --------------------------------------------------------------------------- #


def test_the_capability_is_NOT_gated_anymore_and_that_was_decided() -> None:
    """RESOLVED 2026-09-02. This item shipped the capability classifier and put
    the question to Bakir, because which way to close the asymmetry is a
    risk-appetite judgement and not something evidence can settle: gate the shell
    path too, or relax `execute_code` to match it.

    He chose relax. So the category leaves the always-ask set WITH the tool —
    leaving the category gated while the tool is not would have re-created the
    same asymmetry one level down, refused by name and allowed by class."""
    assert CODE_EXECUTION not in _DEFAULT_ALWAYS_ASK_CATEGORIES
    assert "execute_code" not in _DEFAULT_ALWAYS_ASK_TOOLS


def test_the_classifier_SURVIVES_the_relaxation() -> None:
    """The classifier is not dead code now that it gates nothing — it LABELS.
    Visibility is the only compensating control left after the gate went, and a
    control nobody can see is not one."""
    from stackowl.tools.consent import is_code_execution, launches_an_interpreter

    assert launches_an_interpreter("python3 -c 'import os'") is True
    assert launches_an_interpreter("ls -la") is False
    assert is_code_execution("execute_code", None) is True
    assert is_code_execution("shell", CODE_EXECUTION) is True
    assert is_code_execution("read_file", "read") is False


def test_an_unattended_code_run_is_never_SILENT() -> None:
    """The trade, pinned. The gate is gone; the record is not. If this field
    disappears, unattended code execution becomes invisible and the relaxation
    stops being a trade and becomes a hole."""
    import inspect

    from stackowl.tools import consent

    src = inspect.getsource(consent)
    assert '"code_execution": is_code_execution(' in src, (
        "the autonomous-grant line no longer names code execution — nothing "
        "records what the relaxation lets through"
    )


def test_the_shell_path_reports_the_capability_it_exercises() -> None:
    """Structural, over the source: the INFO line is the ONLY evidence that would
    close ESC-98, and a DEBUG line could never close it because production runs
    at INFO. Pinned so the escalation cannot be answered from a dead query."""
    import inspect

    from stackowl.tools.system import shell

    source = inspect.getsource(shell)
    assert "launches_an_interpreter(rendered)" in source, (
        "the shell path no longer classifies its own capability — the "
        "divergence goes back to being invisible"
    )
    assert 'log.tool.info(\n            "shell.execute: code execution' in source, (
        "the evidence line is not at INFO and could never close ESC-98"
    )
