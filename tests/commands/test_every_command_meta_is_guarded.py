"""The declared-vs-dispatched rule must be enforced by construction, not by memory.

WHY THIS EXISTS — D14.1 asked whether the command registry is "derived everywhere", and
the answer is YES for the registry and NO for the rule that keeps it honest.

MEASURED 2026-09-06. Every consumer genuinely derives: `grep` for a hardcoded command list
across `src/` returns NOTHING, and the four consumers (Slack slash bridge, Telegram adapter,
`/help`, `find_command`) all read the registry. The Telegram menu builder filters nothing by
surface — it only coerces names to Telegram's limits and drops duplicates.

**So the map's Ask is answered NO.** It proposes `cli_only`/`gateway_only` flags to "remove
per-channel special-casing", and there is no per-channel special-casing here to remove.
Adding those flags would be a seat for a problem this codebase does not have — exactly the
dead scaffolding the standing rule forbids.

WHAT THE MEASUREMENT FOUND INSTEAD. The rule "the subcommands declared in meta are exactly
the ones `handle()` implements" — stated in `test_tier_meta.py` as its "honesty guarantee" —
is enforced by THIRTEEN hand-written per-command files. Of 34 command classes, 14 declare
subcommands (the only ones where the rule means anything), and 13 of those 14 had a guard.
`/preferences` did not.

Nothing had gone wrong because of it. Declared and dispatched agree there, which is exactly
why nobody noticed: a rule kept by remembering to write a file is one case short the moment
someone forgets, and the fourteenth file would leave the identical hole for the fifteenth
command. That is this codebase's most-recorded shape — the same rule, one case short — so
the fix is the check below rather than another file.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import stackowl.commands as commands_pkg
from stackowl.commands.base import SlashCommand

_ROOT = Path(__file__).resolve().parents[2]
_TESTS = _ROOT / "tests" / "commands"


def _command_classes() -> list[tuple[str, type[SlashCommand]]]:
    """Every concrete SlashCommand in the package, with its module name.

    Discovered rather than listed: a hand-maintained roster here would be the very
    thing this file exists to abolish.
    """
    out: list[tuple[str, type[SlashCommand]]] = []
    seen: set[str] = set()
    for mod_info in pkgutil.iter_modules(commands_pkg.__path__):
        try:
            mod = importlib.import_module(f"stackowl.commands.{mod_info.name}")
        except Exception:  # pragma: no cover - an unimportable module is another test's job
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ != mod.__name__:
                continue
            if not (issubclass(obj, SlashCommand) and obj is not SlashCommand):
                continue
            if obj.__name__ in seen:
                continue
            seen.add(obj.__name__)
            out.append((mod_info.name, obj))
    return out


def _metas() -> list[tuple[str, type[SlashCommand], object]]:
    rows = []
    for mod_name, cls in _command_classes():
        try:
            rows.append((mod_name, cls, cls().meta))
        except Exception:  # pragma: no cover - needs constructor args; skip
            continue
    return rows


def _has_meta_guard(module_name: str) -> bool:
    """A `tests/commands/test_<name>_meta.py` for this module, under either spelling."""
    stem = module_name.removesuffix("_command")
    return any(
        (_TESTS / f"test_{candidate}_meta.py").exists()
        for candidate in (module_name, stem)
    )


class TestTheRuleIsEnforcedForEveryCommandThatNeedsIt:
    @pytest.mark.tripwire
    def test_every_command_with_subcommands_has_a_meta_guard(self) -> None:
        """THE STRUCTURAL FIX. Thirteen files enforced this rule and the fourteenth
        command slipped through — not by breaking it, but by never being checked."""
        missing = [
            mod for mod, _cls, meta in _metas()
            if getattr(meta, "subcommands", ()) and not _has_meta_guard(mod)
        ]

        assert not missing, (
            "these commands declare subcommands and have no tests/commands/"
            f"test_*_meta.py asserting declared == dispatched: {missing}"
        )

    def test_the_check_is_looking_at_a_real_population(self) -> None:
        """VACUITY CONTROL. If discovery silently returned nothing — an import error, a
        renamed base class — the assertion above would pass by measuring an empty set,
        which is how a zero over a zero denominator reads as a clean bill of health."""
        rows = _metas()

        assert len(rows) >= 30, f"only discovered {len(rows)} commands"
        assert sum(1 for _m, _c, meta in rows if meta.subcommands) >= 10


class TestTheInvariantsThatHoldForEveryCommand:
    """Generic locks. All three hold across 34 commands as of 2026-09-06, so these are
    regression guards rather than findings — but they are the assertions the thirteen
    per-command files each make privately, hoisted to where they cover everything."""

    def test_no_command_declares_a_duplicate_subcommand_name(self) -> None:
        offenders = {
            mod: [s.name for s in meta.subcommands]
            for mod, _cls, meta in _metas()
            if len({s.name for s in meta.subcommands}) != len(meta.subcommands)
        }

        assert not offenders, f"duplicate subcommand names shadow each other: {offenders}"

    def test_every_subcommand_carries_a_summary(self) -> None:
        """The summary is what `/help` and autocomplete render; an empty one shows a
        bare token with no explanation."""
        offenders = [
            f"{mod}:{s.name}" for mod, _cls, meta in _metas()
            for s in meta.subcommands if not (s.summary or "").strip()
        ]

        assert not offenders, offenders

    def test_declaring_subcommands_means_verb_grammar(self) -> None:
        """`grammar` drives autocomplete: "verb" offers the subcommands, the others do
        not. A command with subcommands under any other grammar has them unreachable by
        completion."""
        offenders = {
            mod: meta.grammar for mod, _cls, meta in _metas()
            if meta.subcommands and meta.grammar != "verb"
        }

        assert not offenders, offenders
