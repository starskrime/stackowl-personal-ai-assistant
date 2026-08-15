"""A logging filter must never raise into the code it observes.

WHAT HAPPENED, 2026-08-14. `SqliteLessonsStore._corpus()` logged its corpus shape
as ``{"dims": {384: 3682}}`` — a dict keyed by embedding DIMENSION, an int. The
filter recurses into nested dicts and calls ``_is_sensitive(key)``, which does
``key.lower()``. On an int that is ``AttributeError: 'int' object has no attribute
'lower'``, raised from inside ``logging``, which propagated straight out of the
log call and up through ``_corpus()`` → ``search()`` → ``LessonsIndex.search()``.

classify caught it, retried once, failed again and annotated the turn
"recall DEGRADED". Measured on the live log: 18 warnings and 9 errors, meaning
EVERY turn after the store went live ran with no learned lessons at all.

The irony is the point: the INFO line was added specifically to make the read
path visible, and it is what broke the read path. So there are two fixes here and
both are needed. The call sites stringify their keys — but that alone would just
wait for the next caller to make the same mistake. An observability layer that
can take down the thing it observes is the more serious defect, so the filter is
made total: it coerces any key it cannot lower, and it must not raise whatever it
is handed.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.infra.observability import SensitiveFieldFilter


def _record(fields: object) -> logging.LogRecord:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    rec._fields = fields  # type: ignore[attr-defined]
    return rec


class TestTheFilterIsTotal:
    def test_an_int_keyed_nested_dict_does_not_raise(self) -> None:
        """THE regression. {"dims": {384: 3682}} killed lessons recall."""
        rec = _record({"dims": {384: 3682}})

        assert SensitiveFieldFilter().filter(rec) is True
        assert rec._fields["dims"] in ({384: 3682}, {"384": 3682})

    def test_an_int_keyed_TOP_LEVEL_dict_does_not_raise(self) -> None:
        """The same bug one level up — the warning branch passed dims as the
        whole _fields dict, not nested under a name."""
        rec = _record({384: 3682, 768: 12})

        assert SensitiveFieldFilter().filter(rec) is True

    @pytest.mark.parametrize(
        "key", [None, 3.5, (1, 2), True], ids=["none", "float", "tuple", "bool"]
    )
    def test_other_unhashable_shaped_keys_do_not_raise(self, key: object) -> None:
        """Not just ints. Anything that reaches a log call can be a key, and a
        filter that handles only the case that already bit us will be bitten by
        the next one."""
        rec = _record({"outer": {key: "value"}})

        assert SensitiveFieldFilter().filter(rec) is True

    def test_redaction_still_works_on_string_keys(self) -> None:
        """The hardening must not cost the filter its actual job."""
        rec = _record({"api_key": "sk-secret-value", "safe": "keep me"})

        SensitiveFieldFilter().filter(rec)

        assert rec._fields["api_key"] == "***"
        assert rec._fields["safe"] == "keep me"

    def test_redaction_still_works_INSIDE_a_mixed_key_dict(self) -> None:
        """A non-string key must not become a hole that smuggles a secret past
        the redactor — the sensitive sibling in the same dict is still caught."""
        rec = _record({"outer": {7: "harmless", "password": "hunter2"}})

        SensitiveFieldFilter().filter(rec)

        assert rec._fields["outer"]["password"] == "***"


class TestTheCallSiteThatCausedIt:
    async def test_corpus_load_logging_survives_a_real_search(self, tmp_db) -> None:  # type: ignore[no-untyped-def]
        """End-to-end: the store's own INFO line must not blow up the search it
        is describing. This is the shape the live failure took."""
        from stackowl.learning.lesson import Lesson
        from stackowl.learning.lessons_store import SqliteLessonsStore

        store = SqliteLessonsStore(tmp_db)
        await store.publish(
            Lesson(
                lesson_id="a",
                source_type="reflection",
                source_ref="r",
                content="c",
                embedding=[1.0, 0.0, 0.0],
            )
        )

        hits = await store.search([1.0, 0.0, 0.0], limit=1)

        assert [h.lesson_id for h in hits] == ["a"]

    async def test_a_mixed_dimension_corpus_still_searches(self, tmp_db) -> None:  # type: ignore[no-untyped-def]
        """The warning branch — the one that passed int keys as the whole fields
        dict — only fires when the corpus holds more than one dimension, so it
        needs its own case or it stays untested until it breaks production."""
        from stackowl.learning.lesson import Lesson
        from stackowl.learning.lessons_store import SqliteLessonsStore

        store = SqliteLessonsStore(tmp_db)
        for lid, vec in (("two", [1.0, 0.0]), ("three", [1.0, 0.0, 0.0])):
            await store.publish(
                Lesson(
                    lesson_id=lid,
                    source_type="reflection",
                    source_ref=lid,
                    content=lid,
                    embedding=vec,
                )
            )

        assert [h.lesson_id for h in await store.search([1.0, 0.0], limit=5)] == ["two"]


pytestmark = pytest.mark.asyncio
