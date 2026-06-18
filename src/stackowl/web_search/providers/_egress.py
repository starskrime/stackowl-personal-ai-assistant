"""Egress-logging helper — render a request URL as origin+path only.

Search request URLs carry the query text (and, for Brave, an API key in the params on
some call shapes). We must never log full query strings. This collapses any URL to
``scheme://netloc/path`` so logs record *where* a request went, never *what* was asked.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def egress_target(url: str) -> str:
    """Return ``scheme://netloc/path`` for ``url`` — no query string, no fragment.

    Safe to log: strips everything after the path. Falls back to the raw string only if
    parsing yields nothing useful (defensive; never raises).
    """
    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}{parts.path}"
    return parts.path or url
