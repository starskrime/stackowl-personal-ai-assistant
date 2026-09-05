"""ConfigPath — a filesystem path as a HUMAN writes it in a config file.

A config file is written by a person, and a person writes ``~/photos``. Python
does not expand that: ``Path("~/photos")`` is a RELATIVE directory literally named
``~``, created beside the working directory, and nothing complains.

MEASURED 2026-09-05, and the way it was found is the point. ``docs/stackowl.yaml.example``
emits ``screenshots_dir: ~/.stackowl/screenshots`` — deliberately, so the published
artifact carries no machine-specific absolute path and no operator's home directory
name. Loading it back appeared to work, returning an absolute path, so the tilde
looked harmless.

It was not harmless. It looked fine because a SECOND defect was hiding it:
``settings_customise_sources`` never returned ``init_settings``, so the YAML value
was being discarded entirely and the absolute DEFAULT came back instead. Fixing
that unmasked this — ``PosixPath('~/.stackowl/screenshots')``, relative, exactly
as predicted. One defect was the other's alibi.

So ``~`` is now a SUPPORTED form rather than a trap, for every source — config
file, environment variable and constructor argument alike — because expansion
happens at validation, which all three pass through.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from pydantic import BeforeValidator


def _expand(value: Any) -> Any:
    """Expand ``~`` and ``$VARS`` before pydantic coerces to ``Path``.

    Non-string values (an already-built ``Path``, ``None``) pass through
    untouched — this must not second-guess a caller who handed over a real path.
    """
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    if isinstance(value, Path):
        return Path(os.path.expandvars(os.path.expanduser(str(value))))
    return value


#: Use this for EVERY ``Path``-typed settings field. A bare ``Path`` silently
#: accepts ``~`` as a directory named ``~``, which is never what anyone meant.
ConfigPath = Annotated[Path, BeforeValidator(_expand)]
