"""Every log line in this tree starts with `[something]`, and that is a regex class.

MEASURED 2026-09-09, on my own measurement, twice in one loop. Chasing a recorded
incident I ran `./scripts/log_since.sh --all '[loop] tick failed'`, got **0**, and was
one sentence from reporting that a number in the record was fabricated. The literal
pattern returns **2,974**. `[loop]` is a CHARACTER CLASS: it matches one character from
{l,o,p} followed by " tick failed", and no log line looks like that.

AND IT CUTS BOTH WAYS, which is what makes it dangerous rather than merely annoying:

    './scripts/log_since.sh --all "[cost] "'   ->  197,684
    './scripts/log_since.sh --all "cost] "'    ->   16,184

A **12x overcount**, because `[cost]` matches any one of c/o/s/t before a space and
that is most lines in the corpus. So the same mistake produces a false ZERO in one
direction and a false ORDER OF MAGNITUDE in the other, and neither looks wrong.

THE DENOMINATOR IS WHY THIS IS NOT EXOTIC. `grep -rhoE '"\\[[a-z_]+\\]' src/` finds
**5,145 log calls across 180 distinct prefixes** — essentially every message in the
tree. So the obvious, natural way to grep for any log line is exactly the broken way.

THE RECORDED CHECKS ARE CLEAN AND THAT IS THE POINT. All three closing checks passing a
bracketed pattern escape it (`\\[curated\\]`, `\\[resilient_round\\]`, `\\["`), and no
document Verification grep uses one. Somebody was careful, once, per check. The
INTERACTIVE path — what a loop types while measuring — had no protection at all, and
that is where both of this loop's errors happened. A convention held by care is held
until the day nobody is careful.

This corpus has already paid for this once: D09.4's closing check grepped `[skills]
nudge` and could never match. That fix corrected ONE check. This one warns from the
shared tool, so the next reader is told rather than expected to remember.

WARN, DO NOT REFUSE. A malformed date exits 2 because no window can be honoured; a
truncated window WARNS because the answer is still real, just narrower. This is the
second kind: a bracketed pattern may be a deliberate class, and breaking a working
check to prevent a likely mistake is how a gate gets worked around instead of read.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "log_since.sh"
_WARNING = "LOOKS LITERAL"


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    (tmp_path / "stackowl-2026-09-08.jsonl").write_text(
        '{"msg": "[loop] tick failed — the loop continues"}\n'
        '{"msg": "[cost] NeraAiRaw: $0.00"}\n'
    )
    (tmp_path / "stackowl.jsonl").write_text(
        '{"msg": "[loop] tick failed — the loop continues"}\n'
    )
    return tmp_path


def _run(logs: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SCRIPT), *args],
        cwd=_ROOT, capture_output=True, text=True, timeout=120,
        env={**os.environ, "STACKOWL_LOG_DIR": str(logs)},
    )


class TestTheWarningFires:
    @pytest.mark.tripwire
    def test_an_unescaped_bracketed_prefix_is_announced(self, logs: Path) -> None:
        """The exact call that cost this loop two wrong measurements."""
        out = _run(logs, "--all", "[loop] tick failed")

        assert _WARNING in out.stderr, out.stderr

    @pytest.mark.tripwire
    def test_it_still_answers_rather_than_refusing(self, logs: Path) -> None:
        """A truncated window warns and answers; so does this. Refusing would break a
        deliberate character class and teach the reader to route around the tool."""
        out = _run(logs, "--all", "[loop] tick failed")

        # THE EXIT CODE IS DELIBERATELY NOT PINNED HERE. `grep | wc -l` under
        # `set -o pipefail` exits 1 on a zero count, which is pre-existing and harmless
        # because every caller reads the number through `$(...)` and never the status.
        # Asserting it would be pinning behaviour this item did not decide to change.
        assert out.stdout.strip() == "0", out.stdout
        assert _WARNING in out.stderr

    @pytest.mark.tripwire
    def test_the_escaped_form_is_silent_AND_correct(self, logs: Path) -> None:
        """The control that makes the warning worth having: the right spelling must
        both find the lines and produce no noise. A warning that fires on correct usage
        is one nobody reads."""
        out = _run(logs, "--all", r"\[loop\] tick failed")

        assert out.stdout.strip() == "2", out.stdout
        assert _WARNING not in out.stderr, out.stderr

    @pytest.mark.tripwire
    def test_a_pattern_with_no_brackets_is_silent(self, logs: Path) -> None:
        """The other control. Most patterns carry no bracket at all and must be
        untouched — this is the majority path."""
        out = _run(logs, "--all", "tick failed — the loop continues")

        assert out.stdout.strip() == "2"
        assert _WARNING not in out.stderr


class TestTheLiveCorpusStaysClean:
    @pytest.mark.tripwire
    def test_no_closing_check_passes_an_unescaped_bracketed_prefix(self) -> None:
        """A ratchet, and it starts at zero — stated plainly rather than dressed up as
        a fix. All three bracketed patterns in the record are already escaped; this
        stops the next one arriving unnoticed."""
        import re
        import sys

        import yaml

        sys.path.insert(0, str(_ROOT / "scripts"))
        from progress_lint import entries_with_closing_checks as record_checks

        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
        offenders: list[str] = []
        for ident, raw in record_checks(data):
            command = "\n".join(
                ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")
            )
            for pattern in re.findall(r"log_since\.sh\s+\S+\s+'([^']*)'", command):
                if re.search(r"(?<!\\)\[[a-zA-Z_]+\]", pattern):
                    offenders.append(f"{ident}: {pattern!r}")
        assert not offenders, (
            "these checks pass a bracketed prefix to grep as a CHARACTER CLASS:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_sweep_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. The assertion above passes over an empty corpus, and the
        three escaped patterns are what prove the walk reaches them at all."""
        import re
        import sys

        import yaml

        sys.path.insert(0, str(_ROOT / "scripts"))
        from progress_lint import entries_with_closing_checks as record_checks

        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
        bracketed = 0
        for _ident, raw in record_checks(data):
            for pattern in re.findall(r"log_since\.sh\s+\S+\s+'([^']*)'", raw):
                bracketed += "[" in pattern
        assert bracketed >= 3, f"only {bracketed} bracketed patterns found; walk broken"
