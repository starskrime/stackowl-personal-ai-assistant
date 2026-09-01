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


def test_the_capability_is_on_the_always_ask_list() -> None:
    """So the rule has an address. Without this the category is a label nothing
    consults — the decoration shape."""
    assert CODE_EXECUTION in _DEFAULT_ALWAYS_ASK_CATEGORIES


def test_execute_code_is_unchanged() -> None:
    """This item must not widen anything. execute_code was always-ask by name
    and stays always-ask by name; the category is added alongside, not instead."""
    assert "execute_code" in _DEFAULT_ALWAYS_ASK_TOOLS


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
