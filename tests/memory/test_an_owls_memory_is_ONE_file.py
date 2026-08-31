"""An owl's curated memory must live where the PROMPT looks for it.

MEASURED 2026-08-31, across four days of production logs. ``assemble.py`` reads
the owl block with ``snapshot_for_prompt(state.owl_name, ...)`` — the owl's
routing NAME. The writer builds its filename from whatever spelling reached
``path_for``. The two do not agree, and the log says so: over 4 days the prompt
froze exactly eleven targets, all of them routing names::

    user 958 · rca_gatherer 494 · verifier 430 · hypothesis 409 · secretary 167
    jobmarket 125 · mailbutler 20 · scout 15 · headhunter 11 · syshealth 6
    archivist 4

while these files sat in ~/.stackowl/memory and were read into NO prompt, ever::

    Falcon.md  falcon.md   (scout)       Friday.md   (secretary)
    hawkeye.md (verifier)  Collector.md  (archivist)
    Brain.md  Fury.md  agent.md  owl.md  own.md  sysdesign.md  sysfup.md

They are not empty. Falcon.md carries a live-verified Gmail API gotcha and a
completed trash batch; falcon.md carries the LinkedIn guest-API findings. Both
are exactly the kind of hard-won operational knowledge the store exists for, and
the platform has never once shown them to the owl that learned them. Meanwhile
``scout`` was read 15 times and rendered 0 characters every time.

TWO CAUSES, AND THE FIX HAS TO COVER BOTH:

* DISPLAY NAME vs ROUTING NAME. ``scout``'s display_name is ``Falcon``, so the
  model naturally writes to "Falcon" — the name it is called by. Same for
  Friday/secretary, Hawkeye/verifier, Collector/archivist.
* CASE. ``falcon.md`` and ``Falcon.md`` are two files on a case-sensitive
  filesystem, and ``infer_target`` finds BOTH for the word "falcon" — two hits,
  so it routes to the user file instead. `curated.py` already carries a comment
  admitting the pair exists; nothing resolved it.

THE FIX IS AT ``path_for``, which is the single seam: ``entries()`` reads through
it and ``_write()`` writes through it, so one resolution covers every read and
every write. Identity beats spelling — the alias map wins before any file is
consulted, or an existing ``Falcon.md`` would keep capturing the writes.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import USER_TARGET, CuratedMemory


def _mem(tmp_path, aliases: dict[str, str] | None = None) -> CuratedMemory:  # noqa: ANN001
    mem = CuratedMemory(root=tmp_path)
    if aliases is not None:
        mem.use_target_aliases(lambda: aliases)
    return mem


def _file(tmp_path, name: str, text: str = "[permanent] x") -> None:  # noqa: ANN001
    (tmp_path / f"{name}.md").write_text(text + "\n", encoding="utf-8")


# ------------------------------------------------------------ display names


def test_the_display_name_resolves_to_the_owls_routing_name(tmp_path) -> None:  # noqa: ANN001
    """The live case: the prompt reads scout.md, so a write to "Falcon" must
    land in scout.md and not mint a file nothing reads."""
    mem = _mem(tmp_path, {"scout": "scout", "falcon": "scout"})
    assert mem.path_for("Falcon").name == "scout.md"


def test_the_routing_name_still_works_unchanged(tmp_path) -> None:  # noqa: ANN001
    mem = _mem(tmp_path, {"scout": "scout", "falcon": "scout"})
    assert mem.path_for("scout").name == "scout.md"


def test_IDENTITY_BEATS_AN_EXISTING_FILE(tmp_path) -> None:  # noqa: ANN001
    """The ordering that makes the fix work at all. Falcon.md exists on the live
    box; if an existing file won, every future write would keep landing in the
    file the prompt never reads and nothing would ever converge."""
    _file(tmp_path, "Falcon")
    mem = _mem(tmp_path, {"scout": "scout", "falcon": "scout"})
    assert mem.path_for("Falcon").name == "scout.md"


def test_a_routing_name_is_never_overridden_by_someone_elses_nickname(tmp_path) -> None:  # noqa: ANN001
    """If an owl's display_name collides with another owl's routing name, the
    ROUTING NAME wins — an owl must never lose its own file to a nickname."""
    mem = _mem(tmp_path, {"scout": "scout", "secretary": "secretary"})
    assert mem.path_for("scout").name == "scout.md"


# ------------------------------------------------------------------- case


def test_a_case_variant_finds_the_file_that_already_exists(tmp_path) -> None:  # noqa: ANN001
    """Works with no alias map at all, which matters because the memory dir is
    also read by the CLI and by tests that construct their own store."""
    _file(tmp_path, "jobmarket")
    assert _mem(tmp_path).path_for("Jobmarket").name == "jobmarket.md"


def test_an_exact_file_is_preferred_over_a_case_variant(tmp_path) -> None:  # noqa: ANN001
    _file(tmp_path, "falcon")
    _file(tmp_path, "Falcon")
    assert _mem(tmp_path).path_for("Falcon").name == "Falcon.md"


def test_an_AMBIGUOUS_case_match_changes_nothing(tmp_path) -> None:  # noqa: ANN001
    """Two files differing only in case and neither an exact match: guessing
    which one the operator meant is how memory gets silently misfiled."""
    _file(tmp_path, "falcon")
    _file(tmp_path, "FALCON")
    assert _mem(tmp_path).path_for("Falcon").name == "Falcon.md"


def test_a_brand_new_owl_still_gets_a_file(tmp_path) -> None:  # noqa: ANN001
    """Resolution must never be a gate. An owl created a minute ago has no file
    and may not be in the alias map yet; its first write must still land."""
    assert _mem(tmp_path, {}).path_for("newowl").name == "newowl.md"


def test_the_user_target_is_untouched(tmp_path) -> None:  # noqa: ANN001
    mem = _mem(tmp_path, {"user": "somehow-wrong"})
    assert mem.path_for(USER_TARGET).name == "USER.md"


def test_a_path_traversal_target_still_RAISES(tmp_path) -> None:  # noqa: ANN001
    """Resolution runs before validation, so it must not become a way past it."""
    with pytest.raises(ValueError):
        _mem(tmp_path, {"../../etc/passwd": "../../etc/passwd"}).path_for("../../etc/passwd")


def test_a_broken_alias_lookup_costs_NOTHING(tmp_path) -> None:  # noqa: ANN001
    """The lookup reads a live registry. If it raises, memory must still work —
    degraded to the old spelling-based behaviour, never broken."""
    def _boom() -> dict[str, str]:
        raise RuntimeError("registry unavailable")

    mem = CuratedMemory(root=tmp_path)
    mem.use_target_aliases(_boom)
    assert mem.path_for("scout").name == "scout.md"


# ---------------------------------------------------- what the model is offered


def test_known_targets_offers_each_owl_ONCE(tmp_path) -> None:  # noqa: ANN001
    """Both falcon files exist today, so the word "falcon" in a fact matches TWO
    targets — and ``infer_target`` routes to the user on anything but exactly one
    hit. The owl's own note goes to the user file. Deduping by identity is what
    makes inference able to hit an owl at all."""
    _file(tmp_path, "falcon")
    _file(tmp_path, "Falcon")
    _file(tmp_path, "scout")
    mem = _mem(tmp_path, {"scout": "scout", "falcon": "scout"})
    assert mem.known_targets() == [USER_TARGET, "scout"]


def test_inference_now_reaches_the_owl_instead_of_the_user(tmp_path) -> None:  # noqa: ANN001
    _file(tmp_path, "falcon")
    _file(tmp_path, "Falcon")
    _file(tmp_path, "scout")
    mem = _mem(tmp_path, {"scout": "scout", "falcon": "scout"})
    assert mem.infer_target("Falcon should stop retrying blocked hosts") == "scout"


def test_two_DIFFERENT_owls_named_in_one_fact_still_fall_back(tmp_path) -> None:  # noqa: ANN001
    """Dedupe must collapse spellings of ONE owl, never two real owls."""
    _file(tmp_path, "scout")
    _file(tmp_path, "secretary")
    mem = _mem(tmp_path, {"scout": "scout", "secretary": "secretary"})
    assert mem.infer_target("scout told secretary about it") == USER_TARGET


# ------------------------------------------------------- read and write agree


def test_a_write_under_the_display_name_is_READ_BACK_under_the_routing_name(tmp_path) -> None:  # noqa: ANN001
    """The end-to-end contract, and the one that was broken in production: the
    prompt asks for `scout`, so a fact remembered about "Falcon" has to be there."""
    mem = _mem(tmp_path, {"scout": "scout", "falcon": "scout"})
    mem.add("Falcon", "the guest API ignores the remote filter", "permanent")

    rendered = mem.snapshot_for_prompt("scout", conversation_id="c1")
    assert "guest API" in rendered
    assert (tmp_path / "scout.md").exists()
    assert not (tmp_path / "Falcon.md").exists()


# ------------------------------------------------------------- the alias map


def test_the_alias_map_carries_both_spellings() -> None:
    from stackowl.memory.curated import build_target_aliases

    assert build_target_aliases([("scout", "Falcon"), ("secretary", "Friday")]) == {
        "falcon": "scout", "scout": "scout",
        "friday": "secretary", "secretary": "secretary",
    }


def test_a_routing_name_OUTRANKS_someone_elses_display_name() -> None:
    """An owl losing its own memory file to another owl's nickname is a worse
    failure than a nickname not resolving."""
    from stackowl.memory.curated import build_target_aliases

    aliases = build_target_aliases([("scout", "secretary"), ("secretary", "Friday")])
    assert aliases["secretary"] == "secretary"


def test_an_owl_with_no_display_name_is_still_addressable() -> None:
    """mailbutler and syshealth both carry display_name '' on the live box."""
    from stackowl.memory.curated import build_target_aliases

    assert build_target_aliases([("mailbutler", "")]) == {"mailbutler": "mailbutler"}
