"""Tenancy — owner-scoped persistence foundation (Pass 1).

Exposes the :class:`Principal` owner identity, the stable
:data:`DEFAULT_PRINCIPAL_ID`, the :class:`OwnedRepository` base that domain
Stores subclass to auto-scope by owner, and :class:`PrincipalStore` for the
root ``principals`` table.
"""

from __future__ import annotations

from stackowl.exceptions import PrincipalNotFoundError
from stackowl.tenancy.owned_repository import OwnedRepository
from stackowl.tenancy.principal import (
    DEFAULT_PRINCIPAL_ID,
    Principal,
    PrincipalType,
)
from stackowl.tenancy.store import PrincipalStore

__all__ = [
    "DEFAULT_PRINCIPAL_ID",
    "OwnedRepository",
    "Principal",
    "PrincipalNotFoundError",
    "PrincipalStore",
    "PrincipalType",
]
