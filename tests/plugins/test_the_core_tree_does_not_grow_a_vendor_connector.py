"""D16.4 — third-party products ship as plugins, not in the core tree.

THE RULE, adopted from the reference platform, whose stated reason is maintenance
load rather than quality: observability backends, vendor SaaS connectors and
analytics ship as **standalone plugin repos**. The in-tree provider set is
formally **closed**.

THE DESIGN ALREADY AGREED, in June. `IntegrationRegistry.register()`'s own
docstring reads "Open for extension: Epic 10 plugins can call register() at
import time", and `/connect` tells the operator "No integrations registered.
**Install an integration plugin first.**"

AND THE TREE DOES THE OPPOSITE. Measured 2026-09-04: FOUR vendor-specific modules
live in `src/stackowl/integrations/` — `gmail.py`, `gmail_settings.py`,
`google_calendar.py`, `google_oauth.py` — and THREE core modules register an
integration at boot: `cli/app.py`, `commands/assembly.py`,
`startup/orchestrator.py`. It is live, not dormant: the Gmail OAuth token on disk
was refreshed 2026-09-02.

I GOT BOTH NUMBERS WRONG FIRST, and the tests are what corrected them. I read the
package with `ls | tail -8`, which truncated the listing, and concluded the vendor
set was the google pair — it is four. And I grepped for `register()` only INSIDE
the integrations package and concluded nothing in `src/` calls it — three modules
do. Writing the assertion before believing the measurement is what caught both.

Whether any of it moves out is the operator's call (ESC-133): it is user-facing
capability with live credentials.

SO THIS GUARD PINS THE SET RATHER THAN JUDGING IT. It cannot decide whether a
given module is "third-party" — that is a semantic question, and four static
screens have already been built and deleted in this repo for failing exactly that
kind of distinction. What it *can* do is notice the set CHANGING, which is when
the question needs asking. Adding a FIFTH vendor module to `integrations/`
should cost a conversation, not a quiet commit.

Set equality in both directions, for the reason D16.2 records: this repo has had
an allowlist rot the other way, when deleting six modules left three of their
entries in the owner-scope list.
"""

from __future__ import annotations

import pathlib

import pytest

_INTEGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl" / "integrations"

#: The in-tree integration modules, and what each is. The GENERIC machinery stays;
#: the VENDOR-SPECIFIC four are the exception D16.4 names, kept pending ESC-133.
_GENERIC = {
    "__init__.py",
    "base.py",              # the IntegrationAdapter ABC — the surface itself
    "registry.py",          # where a plugin registers, at import time
    "oauth_manager.py",     # shared encrypted token storage, vendor-neutral
    "integration_assembler.py",  # brief sections from whatever is connected
    "settings.py",
}
_VENDOR_SPECIFIC = {
    "gmail.py",
    "gmail_settings.py",
    "google_calendar.py",
    "google_oauth.py",
}

#: The vendor adapter classes. Core must not CONSTRUCT one — that is the precise,
#: checkable form of "do not wire a third-party product into core", and it is
#: what the first version of this test failed to measure.
_VENDOR_ADAPTERS = ("GmailAdapter", "GoogleCalendarAdapter")


def _modules() -> set[str]:
    return {p.name for p in _INTEGRATIONS.glob("*.py")}


@pytest.mark.tripwire
def test_the_in_tree_integration_set_is_closed() -> None:
    actual = _modules()
    expected = _GENERIC | _VENDOR_SPECIFIC

    added = actual - expected
    assert not added, (
        f"new module(s) in the core integrations package: {sorted(added)}.\n"
        "D16.4: a third-party connector ships as a PLUGIN — IntegrationRegistry."
        "register() is open for exactly that, and /connect already tells the "
        "operator to install one. If this is GENERIC machinery rather than a "
        "vendor connector, add it to _GENERIC with the reason."
    )

    removed = expected - actual
    assert not removed, (
        f"this list names module(s) that no longer exist: {sorted(removed)}.\n"
        "If ESC-133 was answered and the vendor pair moved out, delete them from "
        "_VENDOR_SPECIFIC here — a list that rots is how three dead entries "
        "survived the owner-scope allowlist."
    )


@pytest.mark.tripwire
def test_the_vendor_specific_set_never_grows() -> None:
    """The one that matters. Generic machinery may grow; the vendor set may not.

    A second in-tree connector is the moment the policy is being abandoned, and it
    would otherwise look like an ordinary feature commit.
    """
    assert len(_VENDOR_SPECIFIC) == 4, (
        "the in-tree vendor set is CLOSED at the four modules that predate the "
        "policy — gmail and google_calendar, each with its own settings/oauth "
        "sidecar (ESC-133 asks whether even those move out). A fifth means the "
        "rule was abandoned rather than amended."
    )


@pytest.mark.tripwire
def test_no_core_module_constructs_a_vendor_adapter() -> None:
    """The precise form of the rule — and a correction of this test's first version.

    THE FIRST VERSION WAS UNSOUND, in exactly the way this repo keeps refusing in
    other people's screens. It asked whether a file contained the string
    "IntegrationRegistry" AND the string ".register(" anywhere, and reported three
    core "registrars": cli/app.py, commands/assembly.py, startup/orchestrator.py.
    None of them registers an integration. They register health CONTRIBUTORS
    (`agg.register(DbContributor(...))`), COMMANDS (`registry.register(factory())`)
    and owls. Co-occurrence in a file is not a call, and that false positive was
    recorded in the map, the design doc, progress.yml and a commit message before
    the next item caught it.

    What replaces it is a fact with one meaning: does any core module CONSTRUCT a
    vendor adapter? Nothing does — `GmailAdapter` and `GoogleCalendarAdapter` have
    no reference anywhere outside their own modules and their tests. The vendor
    code is in-tree but UNWIRED, which is why `/connect` correctly reports "No
    integrations registered. Install an integration plugin first."
    """
    src_root = _INTEGRATIONS.parent
    constructors: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.parent == _INTEGRATIONS:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for cls in _VENDOR_ADAPTERS:
            if f"{cls}(" in text:
                constructors.append(f"{path.relative_to(src_root).as_posix()}:{cls}")

    assert not constructors, (
        f"core module(s) constructing a vendor adapter: {sorted(constructors)}.\n"
        "D16.4: a third-party connector ships as a PLUGIN. IntegrationRegistry."
        "register() is open for exactly that, and /connect already tells the "
        "operator to install one."
    )
