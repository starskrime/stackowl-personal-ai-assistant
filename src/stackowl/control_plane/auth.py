"""The control plane's authentication decision, separated from its transport.

WHY THIS IS A MODULE AND NOT A MIDDLEWARE. Every handler calls
:func:`authenticate` explicitly. That is invariant I2 of
``docs/reference-mapping/designs/A05.1.md``, and the reason is specific: aiohttp
middleware does NOT run for a WebSocket upgrade, so a middleware-based design
serves the live-activity channel A05.6 will want completely unauthenticated, and
fixing it later means re-plumbing every handler written between now and then.
A per-handler call costs one line each and cannot have that bug.

WHY IT FAILS CLOSED IN THREE SEPARATE PLACES. This tree already contains the
counter-example: ``mcp/server.py::_sse_auth_ok`` reads ``if not expected_token:
return True`` — an HTTP transport that serves unauthenticated when nobody
configured a token, with a loud warning at startup. A warning is not a gate.
Here, an absent credential is a startup failure (I1), an invalid token is a
refusal (I3), and neither ever falls back to ``DEFAULT_PRINCIPAL_ID``.
"""

from __future__ import annotations

import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from typing import Final

from stackowl.config.secret_resolver import SecretResolver
from stackowl.config.secret_writer import store_secret
from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

#: The service name the credential is stored under. Shared with the CLI command
#: that prints it, so there is ONE source for where the token lives.
SECRET_SERVICE: Final = "stackowl-control-plane"

#: The severities an endpoint can declare, in the platform's own vocabulary —
#: `ToolManifest.action_severity` is `Literal["read","write","consequential"]`.
#: Reusing it means "this credential may read but not write" is expressible on
#: day one with no new concepts, rather than needing a rewrite when it is asked
#: for. A boolean `authenticated` is the recorded core defect of this programme —
#: "consent gated ACTIONS, nothing gated AUTHORITY" — with a new front door.
READ: Final = "read"
WRITE: Final = "write"
CONSEQUENTIAL: Final = "consequential"
ALL_SEVERITIES: Final = frozenset({READ, WRITE, CONSEQUENTIAL})

_BEARER: Final = "Bearer "

#: Uniform refusal body. Invariant I5: "no such route", "bad token" and "unknown
#: principal" are byte-identical to an unauthenticated caller, so none of them
#: can be used to enumerate the others. The distinguishing detail goes to the
#: WARNING log, which is the shape `webhooks/receiver.py` already uses (F139).
UNAUTHORIZED_BODY: Final = "unauthorized"
UNAUTHORIZED_STATUS: Final = 401


class CredentialUnavailable(RuntimeError):
    """Raised when no credential can be resolved or minted.

    The server treats this as fatal. It is NOT a signal to serve openly — that
    is the one behaviour this whole module exists to make impossible.
    """


@dataclass(frozen=True)
class ControlPrincipal:
    """Who is calling, and what they are allowed to do.

    `granted` is a SET even though it currently always holds all three
    severities. That is deliberate: a boolean would have to become a set later,
    and every handler written against the boolean would have to change. The set
    costs nothing now and is the whole difference between authenticating a
    CONNECTION and authorising a PERSON.
    """

    principal_id: str
    credential_id: str
    granted: frozenset[str]

    def may(self, severity: str) -> bool:
        """Whether this principal may invoke an endpoint of *severity*."""
        return severity in self.granted


def ensure_credential() -> str:
    """Return the control-plane token, minting and persisting one if absent.

    Fails closed: any failure to resolve AND mint raises
    :class:`CredentialUnavailable`, which the server turns into a refusal to
    bind. There is no branch here that returns a sentinel meaning "no auth".
    """
    # 1. ENTRY — the token's value is never logged, only its destination.
    log.control_plane.info(
        "[control_plane] auth.ensure_credential: entry",
        extra={"_fields": {"service": SECRET_SERVICE}},
    )

    # 2. DECISION — an existing credential wins; minting is the fallback.
    #
    # BOTH BACKENDS ARE TRIED, and that is not belt-and-braces. `store_secret`
    # writes to the OS keyring when it can and to a 0600 file when it cannot,
    # and it does NOT tell its caller which. A resolver that only asked the
    # keyring would therefore mint a FRESH TOKEN ON EVERY BOOT of a headless
    # box — where no session keyring exists — silently invalidating whatever
    # the operator had saved. That is the exact failure this item's own design
    # document names as "the silent one", and the first draft of this function
    # had it.
    for kind, ref in (
        ("keychain", f"keychain:{SECRET_SERVICE}"),
        ("file", f"file:{StackowlHome.secrets_dir() / f'{SECRET_SERVICE}.key'}"),
    ):
        try:
            existing = SecretResolver.resolve(ref)
        except Exception as exc:  # noqa: BLE001 — B5, never silent
            log.control_plane.info(
                "[control_plane] auth.ensure_credential: no stored credential "
                "under this backend — trying the next",
                extra={"_fields": {"ref_kind": kind, "reason": str(exc)[:200]}},
            )
            continue
        if existing:
            log.control_plane.info(
                "[control_plane] auth.ensure_credential: exit — existing "
                "credential resolved",
                extra={"_fields": {"ref_kind": kind}},
            )
            return existing.strip()

    # 3. STEP — mint. `store_secret` logs WHERE it landed, never the value.
    minted = secrets.token_urlsafe(32)
    try:
        description, _yaml_ref = store_secret(SECRET_SERVICE, minted)
    except Exception as exc:  # noqa: BLE001 — B5, never silent
        log.control_plane.error(
            "[control_plane] auth.ensure_credential: exit — could NOT persist a "
            "credential, so the control plane will not serve",
            exc_info=exc,
            extra={"_fields": {"service": SECRET_SERVICE}},
        )
        raise CredentialUnavailable(
            f"could not persist a control-plane credential: {exc}"
        ) from exc

    # 4. EXIT — loud, because a newly minted token is a thing the operator must
    # go and fetch. `store_secret` logs every step of its own at DEBUG, a level
    # this deployment has never written (DEBT-303), so without this line the
    # provisioning would leave no production record at all.
    log.control_plane.info(
        "[control_plane] auth.ensure_credential: exit — minted a new credential",
        extra={"_fields": {"service": SECRET_SERVICE, "stored_in": description}},
    )
    return minted


def is_loopback(address: str) -> bool:
    """Whether *address* can only be reached from this machine.

    Shared with the reachability warning rather than re-derived, and it uses
    `ipaddress` rather than a string compare because `127.0.1.1` and the
    IPv4-mapped `::ffff:127.0.0.1` are both real bind addresses a compare misses.
    """
    text = (address or "").strip()
    if text.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        # FAIL TOWARDS SILENCE. An address this cannot parse must not produce a
        # warning: a wrong warning sends an operator chasing a configuration
        # that is fine, and cry-wolf is the failure this repo pays for most.
        return False


def check_origin(origin: str | None, host: str | None) -> bool:
    """Whether a browser-originated request may be served.

    Checked BEFORE the token, and the order is behaviour: a browser-driven
    request from another origin may carry a perfectly valid credential, so
    checking the token first would authenticate an attack before rejecting it.

    A request with NO `Origin` is allowed — that is a curl or a native client,
    not a browser, and the header is only meaningful when a browser sets it.

    **THE COMPARISON IS AGAINST THE REQUEST'S OWN `Host`, NOT THE CONFIGURED
    BIND, AND THAT IS WHAT MAKES A WILDCARD BIND POSSIBLE.** This used to take an
    `expected_host` built as `f"{bind_address}:{port}"`. On loopback that is the
    same string a browser sends, so it worked and looked right. The moment the
    bind became `0.0.0.0` — one setting, and now the DEFAULT so the dashboard is
    reachable out of the box — every real request would have been refused:
    a browser at `http://192.168.1.50:8787` sends `Host: 192.168.1.50:8787`,
    which is not `0.0.0.0:8787`. The dashboard would have 401'd everyone while
    every test on loopback stayed green.
    The rule was always "the Origin the browser reports must match the host the
    browser actually connected to", and the request carries both. Nothing is lost
    by dropping the config: a browser cannot be made to send a forged `Host` to a
    different server, which is the only thing this check defends against.

    **AND THE COMPARISON IS EXACT RATHER THAN A SUFFIX.** The old form ended
    `origin.endswith(expected_host)`, so `http://evil-127.0.0.1:8787` satisfied
    it. That was hard to exploit (it needs a registrable name ending in the
    literal authority) and it was still a suffix test standing in for an equality
    test, which on a LAN address is a weaker accident than it was on loopback.
    An Origin is `scheme "://" host [":" port]` and nothing else, so the
    authority can simply be compared.
    """
    if origin is None:
        return True
    if not host:
        # A browser sent an Origin and no Host. Not a shape any real client
        # produces, and there is nothing to compare against — refuse.
        return False
    _, sep, authority = origin.partition("://")
    if not sep:
        return False
    return authority == host


def authenticate(
    authorization: str | None,
    expected_token: str,
    *,
    principal_id: str = DEFAULT_PRINCIPAL_ID,
) -> ControlPrincipal | None:
    """Resolve a bearer credential to a principal, or ``None``.

    ``None`` means REFUSE. It never means "fall back to the default owner" —
    invariant I3. The `principal_id` keyword exists so a later credential store
    can supply the real owner; it is a keyword with a default on the RESOLVER,
    never on the request path, which is the distinction that keeps an unscoped
    call visible.
    """
    if not expected_token:
        # The one branch that must never return a principal. `_sse_auth_ok`
        # returns True here, and closing that is ESC-171.
        log.control_plane.error(
            "[control_plane] auth.authenticate: exit — asked to authenticate "
            "with no expected credential; refusing",
        )
        return None

    if not authorization or not authorization.startswith(_BEARER):
        log.control_plane.warning(
            "[control_plane] auth.authenticate: exit — refused, no bearer "
            "credential presented",
            extra={"_fields": {
                "remedy": "run `stackowl control-plane` on the host to print the "
                          "access token, and send it as `Authorization: Bearer <token>`",
            }},
        )
        return None

    presented = authorization[len(_BEARER):]
    if not hmac.compare_digest(presented, expected_token):
        log.control_plane.warning(
            "[control_plane] auth.authenticate: exit — refused, credential did "
            "not match",
            extra={"_fields": {
                "remedy": "the token has changed or was truncated; run "
                          "`stackowl control-plane` to print the current one",
            }},
        )
        return None

    principal = ControlPrincipal(
        principal_id=principal_id,
        credential_id=SECRET_SERVICE,
        granted=ALL_SEVERITIES,
    )
    log.control_plane.info(
        "[control_plane] auth.authenticate: exit — authenticated",
        extra={"_fields": {
            "principal_id": principal.principal_id,
            "granted": sorted(principal.granted),
        }},
    )
    return principal
