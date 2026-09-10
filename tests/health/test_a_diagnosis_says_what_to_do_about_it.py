"""Health tells the operator what is broken. It must also say what to do.

WHY THIS EXISTS — D14.4's Ask, verbatim: *"Ours is arguably better. Add the 'explain how
to fix it' half?"* The first half is true and measured: `stackowl health` runs a real
aggregator plus a reachability census that asks whether a capability can be REACHED rather
than merely registered, which the reference platform's `doctor` does not do.

THE SECOND HALF WAS ENTIRELY ABSENT. Measured 2026-09-06: `HealthStatus` carries
`name`, `status`, `message`, `latency_ms` — and nothing else. Across 72 construction sites
there was no field for a remedy, so a contributor that KNEW the fix had nowhere to put it.
Every message is a diagnosis: "database not found: {path}", "runtime not constructed",
"knowledge graph unavailable", "{label} missing: {path}".

Both surfaces that an operator actually reads render exactly that and stop:

    stackowl health   ->  ✗  db   down   12ms  database not found: /home/.../stackowl.db
    the 2am alert     ->  ✗ db: down — database not found: /home/.../stackowl.db

and the CLI then exits 1. The operator is told, correctly and uselessly, that something is
broken.

A REMEDY IS WRITTEN ONLY WHERE THE CONTRIBUTOR GENUINELY KNOWS THE FIX, and the tests below
pin BOTH halves of that. `prefix_growth`, `unattributed_spend` and `store_cadence` are
measurements about the platform's own behaviour — "the prompt prefix is growing" has no
command that fixes it — so their remedy stays None. That is deliberate scoping, not an
oversight, and asserting it means a later "fill them all in" has to be a decision rather
than a reflex. Inventing a remedy for a condition with no action is worse than none: it
teaches the operator to skim the field that matters.
"""

from __future__ import annotations

import pytest

from stackowl.health.contributors import (
    BrowserContributor,
    DbContributor,
    FilesystemContributor,
    GraphContributor,
)
from stackowl.health.status import HealthStatus

pytestmark = pytest.mark.asyncio


class TestTheFieldExistsAndIsOptional:
    async def test_remedy_defaults_to_none(self) -> None:
        """Defaulted so all 72 existing construction sites are byte-for-byte
        unaffected — the same discipline every added field on `IngressMessage` used."""
        s = HealthStatus(name="x", status="ok", message=None, latency_ms=1.0)

        assert s.remedy is None

    async def test_remedy_can_be_set(self) -> None:
        s = HealthStatus(
            name="x", status="down", message="broken", latency_ms=1.0,
            remedy="run the thing",
        )

        assert s.remedy == "run the thing"


class TestAKnownFailureCarriesItsFix:
    async def test_a_missing_database_says_how_to_create_it(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        status = await DbContributor(tmp_path / "nope.db").health_check()

        assert status.status == "down"
        assert status.remedy, "the db is missing and nothing says how to make one"
        assert "migrate" in status.remedy

    async def test_a_missing_directory_says_what_to_check(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        status = await FilesystemContributor(
            tmp_path / "absent-data", tmp_path / "absent-logs"
        ).health_check()

        assert status.status == "down"
        assert status.remedy
        assert "STACKOWL_HOME" in status.remedy

    async def test_an_unavailable_graph_says_what_is_wrong_with_the_install(self) -> None:
        status = await GraphContributor(available=False, reason="no kuzu wheel").health_check()

        assert status.status == "down"
        assert status.remedy

    async def test_a_browser_with_no_runtime_explains_it_is_a_CLI_artefact(self) -> None:
        """This one fires on EVERY `stackowl health` run from a terminal, because the
        runtime lives in the serve process. Without a remedy it reads as a fault the
        operator should chase; it is not."""
        status = await BrowserContributor(runtime=None, sessions=None).health_check()

        assert status.status == "degraded"
        assert status.remedy
        assert "/browser" in status.remedy


class TestNoRemedyIsInventedWhereThereIsNoAction:
    """THE OTHER HALF OF THE RULE, and the reason this class exists at all.

    A field that must always be filled gets filled with noise. These contributors
    report measurements about the platform's own behaviour — a growing prompt prefix,
    unattributed spend, a store past its cadence — and there is no command an operator
    can run to fix any of them. Their remedy stays None on purpose.

    CORRECTED 2026-09-10, because this docstring described a branch the test below
    does not take. It drives the contributor with a `_Db` that RAISES, so what it
    exercises is the instrument FAILING, not the measurement degrading — two states
    the contributor's own comment already separates in the message ("I could not
    measure it is not it has regressed") and which received one remedy policy between
    them. Since DEBT-288 that branch asks `remedy_for(exc)`; it still answers None
    here, and for the reason this class is about — a bare `RuntimeError` carries no
    evidence, so there is nothing to say. See
    `test_a_failure_that_arrived_can_say_what_to_do.py` for the case where the same
    branch has something to say and now says it.
    """

    async def test_an_instrument_failure_with_no_evidence_invents_nothing(self) -> None:
        from stackowl.health.contributors import PrefixGrowthContributor

        class _Db:
            async def fetch_all(self, *_a: object, **_k: object) -> list[dict[str, object]]:
                raise RuntimeError("no db here")

        status = await PrefixGrowthContributor(_Db()).health_check()  # type: ignore[arg-type]

        assert status.status == "degraded"
        assert status.remedy is None, (
            "a remedy was invented for a condition no operator command can fix; that "
            "teaches the reader to skim the field that matters"
        )


class TestBothSurfacesRenderIt:
    async def test_the_operator_alert_includes_the_remedy(self) -> None:
        """The 2am surface. A remedy the alert drops is a remedy that does not exist
        when it is needed most."""
        from stackowl.scheduler.handlers.health_sweep import _compose_alert

        down = [HealthStatus(
            name="db", status="down", message="database not found",
            latency_ms=1.0, remedy="run `stackowl db migrate`",
        )]

        text = _compose_alert(down, [])

        assert "database not found" in text
        assert "stackowl db migrate" in text, "the alert dropped the fix"

    async def test_the_alert_still_reads_cleanly_without_one(self) -> None:
        """The measurement contributors have no remedy, so the common line must not
        grow a dangling separator or an empty clause."""
        from stackowl.scheduler.handlers.health_sweep import _compose_alert

        text = _compose_alert(
            [], [HealthStatus(name="prefix_growth", status="degraded",
                              message="prefix is growing", latency_ms=1.0)]
        )

        assert "prefix is growing" in text
        assert "→" not in text, f"an empty remedy left a dangling marker: {text!r}"
