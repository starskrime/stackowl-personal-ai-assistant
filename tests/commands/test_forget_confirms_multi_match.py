"""ESC-8 — `/memory forget` confirms only when it would remove MORE THAN ONE entry.

WHAT WAS FOUND. D08.1 rewrote `/memory` onto curated memory and dropped the
confirmation step with it: `_forget` called `CuratedMemory.remove()` directly and
reported the result. The match is a SUBSTRING and the reply reads "Removed 1
entry/entries", so `/memory forget deploy` removed EVERY entry mentioning deploy.
The curated files are the system prompt now, so a line lost that way changes how
the owl behaves, silently.

BAKIR'S CALL: gate exactly the dangerous case. A single unambiguous match is
still immediate — a curated line is one row in a small text file and trivially
re-added, so ceremony on the common case buys nothing. More than one match asks
first, and SHOWS what it would take, because the whole risk is that the substring
reached further than the user pictured.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import USER_TARGET, CuratedMemory

pytestmark = pytest.mark.asyncio


def _seed(*texts: str) -> CuratedMemory:
    mem = CuratedMemory()
    for t in texts:
        mem.add(USER_TARGET, t, "permanent")
    return mem


async def _forget(cmd_args: str) -> str:
    from stackowl.commands.memory_command import MemoryCommand

    return await MemoryCommand._forget_text(cmd_args, CuratedMemory())


class TestSingleMatchStaysImmediate:
    async def test_one_match_is_removed_without_asking(self) -> None:
        _seed("the deploy region is eu-west-1", "unrelated note")

        out = await _forget("deploy region")

        assert "✓" in out, out
        assert not any(
            "deploy region" in e.text for e in CuratedMemory().entries(USER_TARGET)
        )

    async def test_no_match_reports_a_miss(self) -> None:
        """A typo must not read as a successful deletion."""
        _seed("keep this")

        out = await _forget("nothing matches")

        assert "✗" in out, out
        assert len(CuratedMemory().entries(USER_TARGET)) == 1


class TestMultiMatchAsksFirst:
    async def test_two_matches_are_NOT_removed_without_confirmation(self) -> None:
        _seed("deploy region is eu-west-1", "deploy takes 9 minutes", "unrelated")

        out = await _forget("deploy")

        assert "2" in out, f"the count must be stated: {out!r}"
        assert len(CuratedMemory().entries(USER_TARGET)) == 3, (
            "entries were removed without confirmation"
        )

    async def test_it_SHOWS_what_it_would_remove(self) -> None:
        """The risk is that the substring reached further than the user pictured,
        so a bare count is not enough — the entries themselves have to be named."""
        _seed("deploy region is eu-west-1", "deploy takes 9 minutes")

        out = await _forget("deploy")

        assert "eu-west-1" in out and "9 minutes" in out, out

    async def test_confirming_removes_them_all(self) -> None:
        _seed("deploy region is eu-west-1", "deploy takes 9 minutes", "unrelated")

        out = await _forget("deploy YES")

        assert "✓" in out, out
        remaining = [e.text for e in CuratedMemory().entries(USER_TARGET)]
        assert remaining == ["unrelated"], remaining

    async def test_the_confirmation_token_is_not_treated_as_part_of_the_text(
        self,
    ) -> None:
        """`forget deploy YES` must search for "deploy", not "deploy YES" — else
        confirming would always report a miss."""
        _seed("deploy region", "deploy window")

        assert "✓" in await _forget("deploy YES")
        assert CuratedMemory().entries(USER_TARGET) == []
