"""D04.3 — the platform read a secret file without ever looking at its mode.

D04.3 asks for a credential POOL with rotation. Measured 2026-09-05, the premise
does not hold here: ONE provider is enabled, with ONE key, and across **502,215 log
records in 9 files** there is not a single rate-limit or auth failure — the only
matches were output-token counts like `(640in/429out tokens)`. A pool for a
deployment with one key and no failures is decoration.

What the measuring found instead is smaller and real. Secrets are referenced as
`file:/…/.secrets/<name>.key`, and `SecretResolver._from_file` read whatever was
there. Today those files are correct — `700` on the directory, `0600` on every key —
so **this guard is quiet on the operator's box, which is what a healthy one looks
like**. The failure it exists for is the one nobody would see: a restore, a `cp`, an
editor writing a new file, or a backup extracted without modes, leaving a key
readable by everyone on the machine. The platform would read it, work perfectly, and
say nothing, forever.

IT WARNS, IT DOES NOT REFUSE, on the principle D18.9 settled: **fail closed when
refusing PREVENTS the harm; warn when the harm has already happened.** A
world-readable key is already exposed to every process on the box; refusing to start
does not un-expose it, and it takes the platform down to report something it cannot
fix. So the operator is told, loudly, with the path and the mode.

THE CHECK IS POSIX-ONLY AND SAYS SO. Mode bits do not carry the same meaning on
Windows, and `scripts/boundaries/b4.py` — wired into the gate in D18.7 — exists to
stop exactly that assumption being made silently.
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

from stackowl.config.secret_resolver import SecretResolver


def _write(tmp_path, mode: int) -> str:
    path = tmp_path / "provider.key"
    path.write_text("sk-secret-value\n", encoding="utf-8")
    path.chmod(mode)
    return str(path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_a_locked_down_secret_is_read_in_silence(tmp_path, caplog) -> None:
    """0600 is correct, and correct must be quiet.

    A guard that also fires on the healthy case is one its reader learns to ignore —
    the finding D18.7 recorded when a cross-platform checker sat unread for months
    because it flagged correct code.
    """
    path = _write(tmp_path, 0o600)
    with caplog.at_level(logging.WARNING):
        assert SecretResolver.resolve(f"file:{path}") == "sk-secret-value"
    assert not [r for r in caplog.records if "readable" in r.getMessage()]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_a_readable_secret_is_announced(tmp_path, caplog, mode: int) -> None:
    """Group- or world-readable → a warning naming the file and the mode.

    Parametrised across the shapes a bad copy actually produces: a default umask
    (0644), a group-share (0640), an other-only oddity (0604), and the worst case.
    """
    path = _write(tmp_path, mode)
    with caplog.at_level(logging.WARNING):
        assert SecretResolver.resolve(f"file:{path}") == "sk-secret-value"

    warned = [r.getMessage() for r in caplog.records if "readable" in r.getMessage()]
    assert warned, f"mode {mode:o} was read without a word"
    assert "provider.key" in warned[0], "the warning must name the file"
    assert oct(mode)[-3:] in warned[0], "the warning must state the mode it found"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_the_secret_is_still_returned_when_the_mode_is_wrong(tmp_path) -> None:
    """It warns; it does not refuse.

    Refusing would take the platform down to report an exposure that already
    happened and that stopping cannot undo — D18.9's rule, applied.
    """
    path = _write(tmp_path, 0o644)
    assert SecretResolver.resolve(f"file:{path}") == "sk-secret-value"


def test_an_unreadable_mode_never_breaks_the_read(tmp_path, monkeypatch) -> None:
    """The instrument must not become the failure.

    If the mode cannot be determined — a filesystem without POSIX bits, a stat that
    raises — the secret must still resolve. "I could not check" and "it is exposed"
    are different claims, and this repo has paid for reporting the first as the
    second.
    """
    path = _write(tmp_path, 0o600)

    def _boom(*_args, **_kwargs):
        raise OSError("no stat here")

    monkeypatch.setattr(os, "stat", _boom)
    assert SecretResolver.resolve(f"file:{path}") == "sk-secret-value"
