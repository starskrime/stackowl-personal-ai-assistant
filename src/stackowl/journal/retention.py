"""Journal retention tripwire constant (Story 2.11).

AD-6's real journal retention is now the ``journal`` settings section
(``config/journal_settings.py``): a 30-day default, changeable via a
setting, read live by the seeded ``journal_prune`` job
(``scheduler/handlers/journal_prune.py``). That job reads
``Settings().journal.retention_days`` directly -- it NEVER reads this
module's constant.

WHAT THIS MODULE STILL IS: the one value
``tests/journal/test_retention_tripwire.py`` checks every subsystem prune
window against (AD-4/NFR45: "a tripwire fails any referenced record kind
whose prune window is shorter [than journal retention]"). It stays
TRIPWIRE-ONLY, deliberately -- not because the real setting is still
provisional (it is not; Story 2.11 raised it to the architecture's real
30-day default), but because the tripwire must compare against the DEFAULT
retention rather than whatever an operator's ``stackowl.yaml`` currently
overrides it to. A tripwire that read live ``Settings()`` would pass or fail
depending on a deployment's own config file, which is not what "does this
subsystem's window structurally violate AD-4" is asking. Deriving from
``JournalSettings()``'s default instance (rather than hand-typing a second
literal ``30``) keeps this constant a SINGLE SOURCE OF TRUTH with the
settings model -- the exact drift class ``test_retention_tripwire.py``'s own
docstring warns against ("not restated as a copy").

Story 2.11 also raised BOTH real subsystem prune windows this tripwire
checks to 30 days in the same change
(``config/task_loop_settings.py::prune_completed_after_days``,
``scheduler/handlers/db_reclaim.py::_RUN_HISTORY_RETENTION_DAYS``) --
logged to ``_bmad-output/implementation-artifacts/deferred-work.md`` (DW-17).
"""

from __future__ import annotations

from stackowl.config.journal_settings import JournalSettings

#: Derived from ``JournalSettings()``'s default -- see module docstring for
#: why this stays a tripwire-only constant rather than reading live
#: ``Settings()``. Used ONLY by ``tests/journal/test_retention_tripwire.py``;
#: no production prune job reads this constant.
JOURNAL_RETENTION_DAYS = JournalSettings().retention_days
