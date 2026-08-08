#!/usr/bin/env python3
"""Run the skill authoring-standard migration in bounded batches.

Lives in the repo rather than a scratch directory because it is how the D10.2
backlog actually gets worked through, and because the first throwaway version of
it had a defect worth not repeating: it looped 39 batches into an OPEN CIRCUIT
BREAKER and logged 371 "failures" that were the breaker doing its job. That is
the same retry-storm shape already noted in the scheduler, and a batch runner
has no business re-deriving it.

Progress is recorded per skill (``skills.standard_version``), so stopping and
re-running is free and never repeats work.

    uv run python scripts/migrate_skills_to_standard.py [--limit N] [--batches N]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from stackowl.config.settings import Settings
from stackowl.db.pool import DbPool
from stackowl.paths import StackowlHome
from stackowl.providers.registry import ProviderRegistry
from stackowl.skills.standard_migration import SkillStandardMigrator
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.registry import ConsequentialActionGate


async def run(batch_size: int, max_batches: int) -> int:
    db = DbPool()
    await db.open()
    try:
        store = SkillIndexStore(db)
        provider, model = ProviderRegistry.from_settings(Settings()).get_with_cascade("fast")
        gate = ConsequentialActionGate(
            ConsentPolicy(tiers={"skill_synthesizer": TrustTier.AUTO}),
        )
        total_ok = total_fail = 0

        for batch in range(1, max_batches + 1):
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            report = await SkillStandardMigrator(
                store, provider,
                archive_root=StackowlHome.skills_dir().parent / "pre-migration",
                model=model, consent_gate=gate,
            ).run(apply=True, limit=batch_size, stamp=f"batch-{batch:02d}-{stamp}")

            if not report.outcomes:
                print("DONE — every skill meets the current standard.", flush=True)
                break

            total_ok += report.migrated
            total_fail += report.failed
            print(
                f"batch {batch}: {report.summary()} "
                f"| cumulative ok={total_ok} fail={total_fail}",
                flush=True,
            )
            for outcome in report.outcomes:
                if not outcome.ok:
                    print(outcome.describe(), flush=True)

            if report.remaining <= 0:
                break

            # STOP ON A WHOLLY-FAILED BATCH. Ten failures and zero successes
            # means the provider is down, not that ten skills are unmigratable.
            # Retrying into an open breaker burns the run and buries the real
            # per-skill failures in noise.
            if report.migrated == 0 and report.failed:
                print(
                    f"STOPPING — batch {batch} failed entirely, so the provider "
                    f"is unavailable rather than the skills being unmigratable. "
                    f"{report.remaining} remain; re-run when it is back "
                    f"(progress is recorded per skill).",
                    flush=True,
                )
                return 1

        print(f"FINAL ok={total_ok} fail={total_fail}", flush=True)
        return 0
    finally:
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="skills per batch")
    parser.add_argument("--batches", type=int, default=40, help="max batches")
    args = parser.parse_args()
    return asyncio.run(run(args.limit, args.batches))


if __name__ == "__main__":
    sys.exit(main())
