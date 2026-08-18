"""The shell tool runs bash where bash exists.

MEASURED 2026-08-18, from the tool-failure logging added the same night. Bakir's
Gmail setup kept failing and the cause was finally visible::

    /bin/sh: 1: Syntax error: "(" unexpected

``create_subprocess_shell`` runs ``/bin/sh -c``. On this box — and on every Debian
or Ubuntu host — ``/bin/sh`` is **dash**, not bash. The model writes bash, because
that is what a shell command looks like to anyone who has ever used one: process
substitution, ``[[ ]]``, arrays, ``source``. Dash rejects all of it with a syntax
error that names neither bash nor dash, so the agent could not tell WHY its
perfectly ordinary command was invalid, and retried variations of a command that
was never going to run.

CROSS-PLATFORM BY CONSTRUCTION. bash is looked up rather than assumed: a host
without it (Alpine, Windows) falls back to the previous behaviour exactly. The
platform must run on all hardware, so a hard-coded ``/bin/bash`` would be trading
one host's bug for another's.
"""

from __future__ import annotations

from stackowl.tools.system.shell import _preferred_shell_executable


class TestBashIsPreferredWhenPresent:
    def test_it_finds_bash_on_a_host_that_has_it(self, monkeypatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}"
                            if name == "bash" else None)

        assert _preferred_shell_executable() == "/usr/bin/bash"

    def test_a_host_without_bash_keeps_the_old_behaviour(self, monkeypatch) -> None:
        """None means "let create_subprocess_shell pick", i.e. exactly what
        happened before this change. Alpine and Windows must not regress."""
        monkeypatch.setattr("shutil.which", lambda name: None)

        assert _preferred_shell_executable() is None

    def test_a_lookup_failure_degrades_instead_of_raising(self, monkeypatch) -> None:
        """This runs on every shell call. An exception here would turn a working
        tool into a broken one on a host with an unusual PATH."""
        def _boom(name: str) -> str:
            raise OSError("PATH unreadable")

        monkeypatch.setattr("shutil.which", _boom)

        assert _preferred_shell_executable() is None
