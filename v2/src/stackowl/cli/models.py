"""`stackowl models` CLI — manage local AI models (sentence-transformers, etc.).

Currently exposes ``stackowl models pull <model_name>`` which downloads a
sentence-transformers model into the local cache directory honored by the
``STACKOWL_MODEL_CACHE_DIR`` environment variable.

The command is idempotent: calling it on an already-cached model simply
re-verifies the load path.
"""

from __future__ import annotations

import os

import typer

from stackowl.infra.observability import log

models_app = typer.Typer(help="Manage local AI models.")


@models_app.callback()
def _models_callback() -> None:
    """Manage local AI models."""


@models_app.command("pull")
def models_pull(
    model_name: str = typer.Argument(
        default="all-MiniLM-L6-v2",
        help="sentence-transformers model name (e.g. 'all-MiniLM-L6-v2').",
    ),
) -> None:
    """Download a sentence-transformers model into the local model cache."""
    cache_dir = os.environ.get("STACKOWL_MODEL_CACHE_DIR")

    # 1. ENTRY
    log.cli.debug(
        "[cli] models.pull: entry",
        extra={"_fields": {"model": model_name, "cache_dir": cache_dir}},
    )
    typer.echo(f"Pulling model: {model_name}")
    if cache_dir:
        typer.echo(f"  cache: {cache_dir}")

    try:
        # 3. STEP — load (downloads on cache miss, no-op on cache hit)
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_name, cache_folder=cache_dir)
        dim = int(model.get_sentence_embedding_dimension())
    except Exception as exc:
        # 4. EXIT (error)
        log.cli.error(
            "[cli] models.pull: failed",
            exc_info=exc,
            extra={"_fields": {"model": model_name, "cache_dir": cache_dir}},
        )
        typer.echo(f"✗ Failed to pull {model_name}: {exc}", err=True)
        raise typer.Exit(1) from exc

    # 4. EXIT (success)
    log.cli.info(
        "[cli] models.pull: ready",
        extra={"_fields": {"model": model_name, "dim": dim, "cache_dir": cache_dir}},
    )
    typer.echo(f"✓ Model {model_name} ready (dimension={dim})")
