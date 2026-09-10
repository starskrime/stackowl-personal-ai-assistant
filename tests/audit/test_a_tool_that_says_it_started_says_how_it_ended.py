"""312 shell commands ran and not one outcome was ever written down.

DEBT-296. `shell.execute: entry` is logged at INFO; `shell.execute: exit` — carrying
success, returncode, output size, artifact path and duration — was logged at DEBUG,
and this deployment has written **zero DEBUG records in 677,108**. So the corpus says
the agent started a shell command 312 times and finished none. The reasoning for INFO
was already inside that same function, eight lines below the DEBUG call, explaining
why a DIFFERENT line there had to be INFO: a per-line level convention teaches nothing
to the line above it.

The second half is the same contract broken by IDENTITY rather than level.
`_ok`/`_err` in the browser package logged `f"{tool}.execute: exit"` with `tool`
defaulting to `"browser_tool"` — the parameter's own name wearing a tool's clothes.
Forty call sites omitted the keyword, so **1,235 exit records were filed under
`browser_tool`, a name with ZERO entries** because no tool is called that, and
`browser_eval_js` showed 48 entries against 1 exit.

AND THE INSTRUMENT IS THE POINT. Two static scans were written for this and both were
wrong: function-scoped reported 66 of 66 tools as offenders, module-scoped reported
five of which four were false. Only pairing entry against exit over the CORPUS
distinguished them, because most tools log their exit through a helper whose message
is an f-string no AST walk can read. `scripts/four_point_pairs.py` carries both halves
and these tests ratchet the half that is checkable without logs.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from four_point_pairs import (  # noqa: E402
    declared_severities,
    entry_exit_levels,
    mutating_without_a_loud_outcome,
    unpaired,
)

_BROWSER = _ROOT / "src" / "stackowl" / "tools" / "browser"

#: Tools allowed to log a loud entry and a quiet exit. EMPTY, and it must stay that
#: way — an entry is a promise that an outcome is coming.
_ALLOWED_UNPAIRED: frozenset[str] = frozenset()


class TestEveryLoudEntryHasALoudExit:
    @pytest.mark.tripwire
    def test_no_tool_announces_a_start_it_never_finishes(self) -> None:
        offenders = set(unpaired()) - _ALLOWED_UNPAIRED
        assert not offenders, (
            "these tools log `X.execute: entry` at INFO+ and their exit is DEBUG or "
            f"absent, so production records the start and never the outcome: {sorted(offenders)}"
        )

    @pytest.mark.tripwire
    def test_the_allowlist_is_not_hiding_a_tool_that_now_pairs(self) -> None:
        """A stale exemption rots into permission. None are granted today."""
        assert set(unpaired()) >= _ALLOWED_UNPAIRED, (
            "an exemption names a tool that no longer needs one — delete it"
        )

    @pytest.mark.tripwire
    def test_the_walk_sees_a_real_population(self) -> None:
        """Vacuity control. A walk that silently stopped finding tools would pass
        every assertion above."""
        levels = entry_exit_levels()
        assert len(levels) >= 45, f"only {len(levels)} tools log an entry — the walk broke"
        loud = [t for t, i in levels.items() if i["entry"] in {"info", "warning", "error"}]
        assert len(loud) >= 40, f"only {len(loud)} log it at INFO+ — the walk broke"

    @pytest.mark.tripwire
    def test_it_resolves_an_exit_that_no_literal_anywhere_spells(self) -> None:
        """THE DISCRIMINATION CONTROL, and without it the test above is worthless.

        `browser_navigate`'s exits are logged by `_ok`/`_err` as `f"{tool}.execute:
        exit"`, so the string `browser_navigate.execute: exit` exists in no source
        file. A walk that could not follow the keyword would report it unpaired —
        which is exactly what the second of this item's two failed scans did."""
        info = entry_exit_levels()["browser_type"]
        assert info["entry"] == "info"
        assert "info" in info["exits"], (
            "the forwarded-name resolution is broken: a tool whose exit exists only "
            "inside an f-string helper now reads as having none"
        )


class TestATOOLTHATCHANGESSOMETHINGSAYSSO:
    """DEBT-298. Four tools CHANGED FILES and left no record in production.

    `write_file`, `edit`, `apply_patch` and `undo_write` logged BOTH entry and exit at
    DEBUG, and this deployment has written zero DEBUG records in 677,108 — so the
    corpus holds 0 entries and 0 exits for each of them while `~/.stackowl/undo` holds
    17 snapshots proving the writes happened. The one message each DOES emit at INFO+
    is `path traversal denied`: a REFUSED write on the record, a SUCCESSFUL one absent.

    The rule is DERIVED from the platform's own vocabulary rather than from a list.
    `ToolManifest.action_severity` is already `Literal["read", "write",
    "consequential"]` and 56 tools declare it, so "does this change anything?" is a
    question the platform answers about itself — nothing had ever asked it about
    LOGGING."""

    @pytest.mark.tripwire
    def test_a_tool_that_changes_something_records_its_outcome_loudly(self) -> None:
        offenders = mutating_without_a_loud_outcome()
        assert not offenders, (
            "these tools declare `write` or `consequential` and log their outcome only "
            f"at DEBUG, so production has no record they ever ran: {offenders}"
        )

    @pytest.mark.tripwire
    def test_the_severity_walk_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. A walk that stopped finding `action_severity` would report
        zero offenders forever, which is indistinguishable from a clean tree."""
        severities = declared_severities()
        assert len(severities) >= 50, f"only {len(severities)} tools declare a severity"
        changing = [t for t, v in severities.items() if v != "read"]
        assert len(changing) >= 15, f"only {len(changing)} declare write/consequential"

    @pytest.mark.tripwire
    def test_read_is_exempt_BY_THE_DECLARATION_not_by_judgement(self) -> None:
        """DISCRIMINATION CONTROL, and it pins the scope decision rather than leaving it
        to prose. `read_file` logs entry AND exit at DEBUG exactly as the four did, and
        is deliberately NOT swept in: its volume cannot be measured precisely because it
        is unlogged, and promoting an unmeasured rate is how a channel gets filtered
        instead of read. If this ever fails, `read_file` has changed severity and the
        exemption has to be re-argued."""
        assert declared_severities().get("read_file") == "read"
        assert "read_file" not in mutating_without_a_loud_outcome()


class TestTheHelperCannotInventAToolName:
    def _helper_source(self, name: str) -> ast.FunctionDef:
        tree = ast.parse((_BROWSER / "tools.py").read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        raise AssertionError(f"{name} is gone from browser/tools.py")

    @pytest.mark.tripwire
    @pytest.mark.parametrize("helper", ["_ok", "_err"])
    def test_tool_is_required_with_no_default(self, helper: str) -> None:
        """The mutant this kills is the defect itself: `tool: str = "browser_tool"`.

        There is no honest default — the helper cannot know which tool called it, and
        the one it had produced 1,235 records under a name nothing enters by. Same
        move DEBT-289 made for `remedy`: an argument with a plausible default is an
        argument that gets forgotten, silently."""
        fn = self._helper_source(helper)
        names = [a.arg for a in fn.args.kwonlyargs]
        assert "tool" in names, f"{helper} no longer takes a tool name"
        default = fn.args.kw_defaults[names.index("tool")]
        assert default is None, (
            f"{helper}'s `tool` has a default again — that default is what filed 1,235 "
            "exits under a name no tool has"
        )

    @pytest.mark.tripwire
    def test_every_call_site_names_its_tool(self) -> None:
        """A MODULE THAT DEFINES ITS OWN `_err` IS A DIFFERENT HELPER, and matching by
        NAME instead of by binding is a bug this guard was born with.

        `browse.py` has a local `_err` that takes no `tool` and logs nothing — it is a
        documented pre-loop refusal, not the shared exit helper. The scripted edit that
        fixed the 32 real sites matched `_err(` by name and added the keyword to those
        eight too; **mypy caught it** ("Unexpected keyword argument"), which would
        otherwise have been a TypeError on every `browser_browse` refusal. The guard
        must resolve the same way the edit should have."""
        sites, missing, skipped = 0, [], []
        for path in sorted(_BROWSER.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            local = {
                n.name
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name in {"_ok", "_err"}
            }
            if local and path.name != "tools.py":
                skipped.append(path.name)
                continue
            for n in ast.walk(tree):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
                    continue
                if n.func.id not in {"_ok", "_err"}:
                    continue
                sites += 1
                if not any(k.arg == "tool" for k in n.keywords):
                    missing.append(f"{path.name}:{n.lineno}")
        assert sites >= 30, f"only {sites} call sites found — the walk broke, not the code"
        assert skipped == ["browse.py"], (
            "the set of modules shadowing the shared helper changed — re-read them "
            f"before trusting this guard: {skipped}"
        )
        assert not missing, f"these returns would file their outcome anonymously: {missing}"


class TestTheHighestPrivilegeToolSaysWhatItDid:
    @pytest.mark.tripwire
    def test_the_shell_exit_is_visible_in_production(self) -> None:
        """Arbitrary command execution is the one tool whose outcome an auditor will
        certainly want, and it is the one that never recorded one."""
        from stackowl.tools.system import shell

        src = inspect.getsource(shell)
        i = src.index('"shell.execute: exit"')
        call = src.rfind("log.tool.", 0, i)
        level = src[call:i].split("log.tool.")[-1].split("(")[0]
        assert level in {"info", "warning", "error", "critical"}, (
            f"shell.execute: exit is logged at {level!r}; production runs at INFO and "
            "this deployment has never written a DEBUG record"
        )
