"""A boot warning that is always wrong teaches everyone to ignore boot warnings.

MEASURED 2026-09-01: ``[startup] browser_probe: missing system libraries
['libx11-xcb'] — install with 'sudo apt install -y ... libx11-xcb1 ...'`` had
fired on EVERY boot — **644 times in the retained logs** — and the library was
installed the whole time::

    ldconfig -p | grep -c 'libx11-xcb'   ->  0     <- what the code searched for
    ldconfig -p | grep -c 'libX11-xcb'   ->  2     <- what is actually installed

TWO VOCABULARIES, ONE CASE-SENSITIVE COMPARISON. ``_REQUIRED_LIBS_LINUX`` holds
DEBIAN PACKAGE-name prefixes (``libx11-xcb1``, lowercase by Debian policy),
because that is what the remediation message tells the operator to install.
``ldconfig -p`` prints SONAMEs, which are not lowercase: ``libX11-xcb.so.1``.
``libgtk-3`` and ``libasound`` passed only because their SONAME happens to be
lowercase as well — by luck, not design. That is why the fix is case-insensitive
matching for the whole class rather than respelling one entry.

WHAT IT COST, STATED HONESTLY: nothing at runtime. ``BrowserProbeResult.ready``
requires only the binary, and the probe's own docstring says libs are advisory —
163 browser_navigate turns in the same period prove the browser was never
blocked. What it cost was TRUST. It put a false instruction into the operator's
boot log 644 times, and it cost THIS session real time: the warning was picked up
as a suspected cause of the browser's 40.5% navigation-failure rate and had to be
measured and refuted before the real cause (no per-host memory) was found.

THE FIX DOES NOT AUTO-INSTALL, and that is deliberate rather than an oversight.
The probe already auto-fetches the 622 MB browser binary, which is the platform's
own dependency. System packages need root, which the platform does not have and
should not assume; naming them for the operator is the correct behaviour. What
was broken was the DETECTION, and only that is changed.
"""

from __future__ import annotations

import pytest

from stackowl.startup import browser_probe
from stackowl.startup.browser_probe import _REQUIRED_LIBS_LINUX, _check_lib

pytestmark = pytest.mark.asyncio

# The real shape of `ldconfig -p` on this box, trimmed. Note the capital X.
_LDCONFIG_OUTPUT = """\
\tlibgtk-3.so.0 (libc6,AArch64) => /lib/aarch64-linux-gnu/libgtk-3.so.0
\tlibX11-xcb.so.1 (libc6,AArch64) => /lib/aarch64-linux-gnu/libX11-xcb.so.1
\tlibX11-xcb.so (libc6,AArch64) => /lib/aarch64-linux-gnu/libX11-xcb.so
\tlibasound.so.2 (libc6,AArch64) => /lib/aarch64-linux-gnu/libasound.so.2
"""


@pytest.fixture
def _ldconfig(monkeypatch):  # noqa: ANN202
    """Pin both the lookup and the output, so the test does not depend on the box."""
    monkeypatch.setattr(browser_probe.shutil, "which", lambda _n: "/sbin/ldconfig")
    monkeypatch.setattr(browser_probe.os, "popen", lambda _c: _Fake(_LDCONFIG_OUTPUT))


class _Fake:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


async def test_a_capitalised_soname_is_found(_ldconfig) -> None:  # noqa: ANN001
    """The exact 644-times bug: the package name is lowercase, the SONAME is not."""
    assert await _check_lib("libx11-xcb") is True


@pytest.mark.parametrize("lib", list(_REQUIRED_LIBS_LINUX))
async def test_every_required_library_is_found(lib: str, _ldconfig) -> None:  # noqa: ANN001
    """All three, so a future entry whose SONAME case differs cannot regress —
    two of these passed only by luck before."""
    assert await _check_lib(lib) is True


async def test_a_genuinely_absent_library_is_still_reported(_ldconfig) -> None:  # noqa: ANN001
    """The expensive direction. Case-insensitivity must not turn the check into
    one that always passes — that would replace a false alarm with a blind spot,
    which is strictly worse."""
    assert await _check_lib("libsomething-not-here") is False


async def test_it_assumes_ok_when_it_cannot_verify(monkeypatch) -> None:  # noqa: ANN001
    """No ldconfig means no answer, and the probe must not invent one — libs are
    advisory, so a false 'missing' is worse than an unknown."""
    monkeypatch.setattr(browser_probe.shutil, "which", lambda _n: None)
    assert await _check_lib("libx11-xcb") is True


async def test_an_os_error_assumes_ok(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(browser_probe.shutil, "which", lambda _n: "/sbin/ldconfig")

    def _boom(_cmd: str) -> object:
        raise OSError("no fork available")

    monkeypatch.setattr(browser_probe.os, "popen", _boom)
    assert await _check_lib("libx11-xcb") is True


async def test_the_passing_case_is_observable(_ldconfig, caplog) -> None:  # noqa: ANN001
    """The absence of a warning is not evidence the check RAN — that ambiguity is
    what let a permanently-false result sit unnoticed for 644 boots. INFO,
    because production runs at INFO."""
    import logging

    probe = browser_probe.BrowserProbe()
    with caplog.at_level(logging.INFO):
        assert await probe._check_libs() is True
    assert any(
        "all required system libraries present" in r.getMessage() for r in caplog.records
    )


async def test_a_real_miss_still_warns_with_the_install_command(monkeypatch, caplog) -> None:  # noqa: ANN001
    """The remediation must survive: when something IS missing, the operator is
    told the package to install, because system packages need root and the
    platform neither has it nor should assume it."""
    import logging

    monkeypatch.setattr(browser_probe.shutil, "which", lambda _n: "/sbin/ldconfig")
    monkeypatch.setattr(browser_probe.os, "popen", lambda _c: _Fake("\tlibnothing.so\n"))
    probe = browser_probe.BrowserProbe()
    with caplog.at_level(logging.WARNING):
        assert await probe._check_libs() is False
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "missing system libraries" in msg and "apt install" in msg


def test_the_list_says_which_vocabulary_it_is() -> None:
    """Structural. The entries are PACKAGE names matched against SONAMEs, and a
    reader who does not know that will 'fix' the case in the wrong direction."""
    import inspect

    source = inspect.getsource(browser_probe)
    header = source.split("_REQUIRED_LIBS_LINUX = (")[0][-600:]
    assert "PACKAGE" in header and "SONAME" in header, (
        "the two vocabularies this comparison spans are not named where the list "
        "is defined"
    )
