"""Principal — the owner identity that scopes all user data (Pass 1 tenancy).

A :class:`Principal` is the root of the ownership model: every user-data row
carries an ``owner_id`` that points at a principal. In single-user mode the
whole system runs under one stable principal, :data:`DEFAULT_PRINCIPAL_ID`,
which the migrations and :class:`~stackowl.tenancy.store.PrincipalStore` seed
idempotently. Teams are represented with ``principal_type='team'`` so the same
ownership column can later scope shared workspaces without a schema change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: Fixed, stable identifier for the single-user default owner.
#:
#: This id is referenced in two places that must agree forever: migration
#: ``0042`` seeds the principal row with this id, and migration ``0043``
#: uses the *same literal* as the ``owner_id`` column DEFAULT so existing rows
#: backfill to this owner. Never change it — doing so would orphan all
#: previously-written user data. New owners get freshly-minted ids instead.
DEFAULT_PRINCIPAL_ID = "principal-default"

PrincipalType = Literal["user", "team"]


class Principal(BaseModel):
    """An owner identity (a single user or a team) that scopes user data."""

    principal_id: str = Field(..., min_length=1)
    principal_type: PrincipalType
    display_name: str = Field(..., min_length=1)
    created_at: datetime
