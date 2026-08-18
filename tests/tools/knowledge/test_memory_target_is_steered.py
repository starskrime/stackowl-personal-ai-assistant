"""The memory tool must say WHEN to use each target, not just that they exist.

MEASURED 2026-08-18 on Bakir's live files. USER.md was 97% full and only ONE of its
six entries was a fact about him::

    56  [permanent]     Bakir prefers root-cause fixes over patches.     <- the user
   186  [until_changed] English tutor correction rules                    <- an owl's
   270  [until_changed] sysfup angles delivered (an owl he DISABLED)      <- an owl's
   155  [until_changed] sysdesign curriculum log                          <- an owl's
   311  [until_changed] Mailbutler OAuth config                           <- an owl's
   345  [until_changed] Email butler cron job details                     <- an owl's

WHY, and it is not carelessness. ``target`` defaults to "user", and the description
listed the options without a rule for choosing between them. The model took the
default, every time. The default happens to be the SCARCEST file (1,375 chars
against an owl's 2,200) and the only one injected into EVERY owl's prompt — so a
Mailbutler credential path is carried by sysdesign, by the verifier, by every owl
that runs, forever.

WHAT THIS FIXES is the decision point: the description now states the rule. A fact
about the USER goes to user; a fact about a JOB, an owl, a credential or a schedule
goes to that owl's own file. One sentence of prompt surface at the exact place the
choice is made, which is cheaper than any amount of consolidation afterwards.
"""

from __future__ import annotations

from stackowl.tools.knowledge.memory import MemoryTool


def _target_description() -> str:
    return str(MemoryTool().parameters["properties"]["target"]["description"])


class TestTheDescriptionTeachesTheChoice:
    def test_it_names_what_belongs_to_the_user(self) -> None:
        d = _target_description().lower()

        assert "user" in d

    def test_it_says_an_owls_OWN_work_goes_to_that_owl(self) -> None:
        """The missing half. Listing the options never told the model which to
        pick, so it always took the default."""
        d = _target_description().lower()

        assert "owl" in d
        assert any(w in d for w in ("job", "schedule", "credential", "config", "setup"))

    def test_it_warns_that_user_is_shared_and_scarce(self) -> None:
        """The cost is invisible at the call site: USER.md rides in EVERY owl's
        prompt and has the smaller budget. The model cannot weigh that unless it
        is told."""
        d = _target_description().lower()

        assert "every" in d or "shared" in d or "all owls" in d

    def test_it_stays_short_enough_to_ship_every_call(self) -> None:
        """This is prompt surface on every request (Law 2). A rule the model
        actually reads beats a paragraph it skims."""
        assert len(_target_description()) < 420
