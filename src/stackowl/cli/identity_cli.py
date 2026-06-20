"""stackowl identity — cross-channel identity management commands."""
from __future__ import annotations

import contextlib
import logging
import sqlite3
import sys

import typer

log = logging.getLogger("stackowl.cli")

identity_app = typer.Typer(help="Cross-channel identity management.")


@identity_app.callback()
def identity() -> None:
    """Cross-channel identity management."""


def relink(
    db_path: str,
    aliases: dict[str, list[str]],
    owner_id: str,
    *,
    dry_run: bool,
) -> dict[str, int]:
    """Re-key user_preferences.owner_key + staged_facts.source_ref (source_type!='conversation')
    from each per-channel handle to its configured identity. Owner-scoped. Idempotent.

    Returns counts {'preferences': N, 'facts': M}.
    dry_run rolls back (reports would-change counts without modifying the DB).

    Only re-keys rows whose source_type is NOT 'conversation' in staged_facts —
    conversation turns remain keyed on their per-session session_id so chat history
    stays channel-isolated.
    """
    counts: dict[str, int] = {"preferences": 0, "facts": 0}
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        for identity_key, handles in aliases.items():
            for handle in handles:
                cur = conn.execute(
                    "UPDATE user_preferences SET owner_key=? WHERE owner_id=? AND owner_key=?",
                    (identity_key, owner_id, handle),
                )
                counts["preferences"] += cur.rowcount
                cur2 = conn.execute(
                    "UPDATE staged_facts SET source_ref=?"
                    " WHERE owner_id=? AND source_ref=? AND source_type != 'conversation'",
                    (identity_key, owner_id, handle),
                )
                counts["facts"] += cur2.rowcount
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception as exc:
        with contextlib.suppress(Exception):
            conn.rollback()
        log.error("[identity] relink: failed", exc_info=exc)
        typer.echo(f"✗ relink failed: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()
    return counts


@identity_app.command("link")
def identity_link(
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        help="Report would-change counts without modifying the database.",
    ),
) -> None:
    """Re-key existing rows from per-channel handles to the configured identity."""
    from stackowl.config.settings import Settings
    from stackowl.db.pool import default_db_path
    from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

    aliases = Settings().identity.aliases
    if not aliases:
        typer.echo("no identity.aliases configured — nothing to relink")
        return

    db_path = str(default_db_path())
    counts = relink(db_path, aliases, DEFAULT_PRINCIPAL_ID, dry_run=dry_run)
    mode = "would change" if dry_run else "updated"
    typer.echo(
        f"✓ identity link: {mode} {counts['preferences']} preference row(s),"
        f" {counts['facts']} fact row(s)"
    )
