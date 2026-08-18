"""Every registered tool must be findable by the words a user would search.

BAKIR asked for the thin-description warning to be fixed. The warning is real, not
cosmetic: ``tool_search._score_entry`` adds ``_W_DESC_INCLUDES`` for every query
term found in a tool's description, so a six-word description can only ever match
six words. A capability the model cannot find is, from the model's side,
a capability that does not exist — the "registered but unreachable" shape this tree
keeps paying for, in its search form.

THIS TEST IS THE ACTUAL FIX. Rewriting eleven descriptions fixes eleven examples;
the twelfth arrives with the next tool and reintroduces the problem, because the
registry only WARNS and a warning in a log nobody greps changes nothing. Asserting
the invariant over the whole registry is what makes it stay fixed — the same
"fix the architecture, not the example" rule this project already runs on.

WHY NOT JUST RAISE IN THE REGISTRY. Registration happens at import time across
every entry point, including ones that must not die on a cosmetic problem (a
plugin, a test harness, the CLI). A hard failure there would trade a search-quality
issue for a boot failure. A test fails the build instead, which is where this
belongs.
"""

from __future__ import annotations

import logging

from stackowl.tools.registry import _MIN_DESCRIPTION_WORDS, ToolRegistry


def _thin() -> list[tuple[str, int, str]]:
    logging.disable(logging.WARNING)  # the registry warns per tool; we assert instead
    try:
        registry = ToolRegistry.with_defaults()
        out = []
        for tool in registry.all():
            desc = (tool.description or "").strip()
            words = len(desc.split())
            if words < _MIN_DESCRIPTION_WORDS:
                out.append((tool.name, words, desc))
        return out
    finally:
        logging.disable(logging.NOTSET)


class TestEveryToolIsSearchable:
    def test_no_registered_tool_has_a_thin_description(self) -> None:
        """The invariant, over the WHOLE registry rather than a list of names.

        A named-list assertion would pass the moment someone renames a tool; this
        fails for any new one, which is the point.
        """
        thin = _thin()

        assert not thin, (
            "these tools cannot be found by search — their descriptions carry fewer "
            f"than {_MIN_DESCRIPTION_WORDS} searchable words:\n"
            + "\n".join(f"  {n} ({w}w): {d!r}" for n, w, d in thin)
        )

    def test_the_threshold_is_the_registry_s_own(self) -> None:
        """Read from the registry, never copied. Two constants that must agree are
        two places to change, and they drift — the defect shape this repo names."""
        assert _MIN_DESCRIPTION_WORDS >= 1


class TestDescriptionsCarryTheWordsPeopleSearch:
    """Word COUNT alone is gameable — padding would satisfy the count and improve
    nothing. These pin that the rewritten descriptions actually contain the terms
    someone would type, which is what the scorer matches on.
    """

    def test_file_tools_are_found_by_their_common_verbs(self) -> None:
        from stackowl.tools.meta.tool_search import CatalogEntry, rank_tools

        logging.disable(logging.WARNING)
        try:
            registry = ToolRegistry.with_defaults()
            entries = [
                CatalogEntry(name=t.name, description=t.description or "", category="")
                for t in registry.all()
            ]
        finally:
            logging.disable(logging.NOTSET)

        for query, expected in (("save file", "write_file"), ("open file", "read_file")):
            names = [e.name for e in rank_tools(entries, query, limit=8)]
            assert expected in names, (
                f"searching {query!r} does not surface {expected} — got {names}"
            )

    def test_browser_cookie_tools_are_found_by_login_words(self) -> None:
        """Nobody searches "cookies_set". They search "login" or "session"."""
        from stackowl.tools.meta.tool_search import CatalogEntry, rank_tools

        logging.disable(logging.WARNING)
        try:
            registry = ToolRegistry.with_defaults()
            entries = [
                CatalogEntry(name=t.name, description=t.description or "", category="")
                for t in registry.all()
            ]
        finally:
            logging.disable(logging.NOTSET)

        names = [e.name for e in rank_tools(entries, "login session cookies", limit=10)]

        assert any(n.startswith("browser_cookies") for n in names), (
            f"no cookie tool surfaced for a login query — got {names}"
        )
