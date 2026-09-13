"""Who owns this dashboard — proven by a one-time setup code, kept as a salted hash.

Spec: ``_bmad-output/implementation-artifacts/spec-q29-control-plane-default-credential.md``.

THE DEFECT THIS CLOSES. The dashboard binds every interface by design (ESC-172)
and shipped ``admin/admin``. A default-credential login received the real,
permanent bearer token and every data route served it, so anyone on the network
owned the dashboard of every fresh clone. A first fix that kept the published
password as proof of ownership still let the first network visitor claim the
install — so a password anybody can look up proves nothing here, ever (L1).

WHAT PROVES OWNERSHIP INSTEAD. A SETUP CODE: 60 bits, single use, 24 hours, sent
to the owner's Telegram when exactly one owner is configured, shown on the
platform's terminal when it has one, and always printed by `stackowl control-plane
reset-password` on the host. Only somebody with the host or the owner's chat
holds it. Until it is used the install is in
SETUP: no token is issued and no data route answers.

THREE STATES, RE-READ ON EVERY REQUEST — no cache, because the host CLI resets
the password in another process and a cached answer is a restart the operator
did not know they needed (BH2):

* ``setup``       — no password hash stored (or a damaged one, reported first).
* ``ready``       — a readable hash.
* ``unavailable`` — the store holds, or may hold, the hash and cannot be read.
  NEVER "no password" (L3): that was the reading that let a flaky keyring reopen
  the first-claim race.

ONE OWNER OF BOTH FACTS. The hash and the code, and the transactions that change
them together with the bearer token, live here; the server and the CLI ask.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import datetime as _dt
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Any, Final, Literal, NoReturn

from stackowl.config.secret_writer import (
    SecretStoreUnreadable,
    delete_secret,
    read_located_secret,
    store_located_secret,
)
from stackowl.control_plane.auth import (
    CredentialUnavailable,
    read_credential,
    rotate_credential,
)
from stackowl.health.status import HealthStatus, remedy_for
from stackowl.infra.observability import log

#: Where the password hash lives. Deliberately NOT `stackowl-control-plane-password`,
#: the reference name older example configs carried for the retired YAML field.
HASH_SERVICE: Final = "stackowl-control-plane-password-hash"

#: Where the one-time setup code lives.
CODE_SERVICE: Final = "stackowl-control-plane-setup-code"

#: The shortest password the platform accepts.
MIN_LENGTH: Final = 12

#: The longest. Bounds what one unauthenticated request can make scrypt chew on.
MAX_LENGTH: Final = 1024

#: How long a setup code stays valid after it is issued.
CODE_TTL_SECONDS: Final = 24 * 60 * 60

#: How far in the FUTURE an `issued_at` may sit before the code counts as expired:
#: a clock step or a tampered record, and either way nobody can say how long the
#: code has really been valid.
_CLOCK_SKEW_S: Final = 300

PasswordState = Literal["setup", "ready", "unavailable"]
CodeIssuer = Literal["platform", "host"]

#: What an operator does when the store cannot be read. Names no path beyond the
#: platform's own home, because login shows it to unauthenticated callers.
STORE_REMEDY: Final = (
    "the platform cannot read the dashboard password from its secret store (the OS "
    "keyring, or ~/.stackowl/.secrets) — unlock or fix access to it; nothing needs "
    "restarting, the next sign-in reads it again"
)

#: What an operator does about a damaged record or a lost code.
RESET_REMEDY: Final = (
    "run `stackowl control-plane reset-password` on the host for a fresh setup code, "
    "then set a new password on the dashboard"
)

_SCHEME: Final = "scrypt"
#: scrypt cost. 128 * r * n bytes = 16 MiB per derivation, inside hashlib's 32 MiB
#: default `maxmem`, so it runs on the smallest box this platform targets.
_N: Final = 2**14
_R: Final = 8
_P: Final = 1
_DKLEN: Final = 32
_SALT_BYTES: Final = 16
#: The most memory a STORED record may ask a derivation for. A record is parsed from
#: the store, and a tampered one naming n=2**30 would be a memory bomb on sign-in.
_MAX_MEM: Final = 64 * 1024 * 1024
#: And the CPU and the output. Memory bounds n*r but not p, and a derivation's
#: cost is linear in p and in the digest length a record asks for.
_MAX_P: Final = 4
_MAX_DIGEST: Final = 64
_MAX_SALT: Final = 64

_CODE_SCHEME: Final = "setup-code"
#: Crockford base32 — no I, L, O or U, so a code read aloud or retyped from a phone
#: survives. 12 symbols x 5 bits = 60 bits.
_CODE_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_SYMBOLS: Final = 12
_CODE_TYPO_MAP: Final = str.maketrans({"O": "0", "I": "1", "L": "1"})


class PasswordStoreUnavailable(RuntimeError):
    """The secret store could not be read or changed. Nothing was changed."""

    def __init__(self, message: str, *, remedy: str = STORE_REMEDY) -> None:
        super().__init__(message)
        self.remedy = remedy


class PasswordResetIncomplete(PasswordStoreUnavailable):
    """A reset failed AND putting things back did not finish. `changed` says what stayed."""

    def __init__(self, message: str, *, changed: list[str]) -> None:
        super().__init__(message, remedy=RESET_REMEDY)
        self.changed = changed


@dataclass(frozen=True)
class _StoredHash:
    """One scrypt record: `scrypt$n$r$p$<salt b64>$<digest b64>`."""

    n: int
    r: int
    p: int
    salt: bytes
    digest: bytes

    @classmethod
    def derive(cls, candidate: str, salt: bytes) -> _StoredHash:
        return cls(_N, _R, _P, salt, cls._scrypt(candidate, salt, _N, _R, _P, _DKLEN))

    @staticmethod
    def _scrypt(candidate: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
        # UTF-8 BYTES: a non-ASCII password is a password, not a TypeError.
        return hashlib.scrypt(
            candidate.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen,
            maxmem=_MAX_MEM + 1024 * 1024,
        )

    @classmethod
    def parse(cls, text: str) -> _StoredHash:
        parts = text.strip().split("$")
        if len(parts) != 6 or parts[0] != _SCHEME:
            raise ValueError("not a scrypt password record")
        try:
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            salt = base64.b64decode(parts[4], validate=True)
            digest = base64.b64decode(parts[5], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"malformed scrypt password record: {exc}") from exc
        if (n < 2 or n & (n - 1) or r < 1 or not 1 <= p <= _MAX_P
                or 128 * r * n > _MAX_MEM):
            raise ValueError("scrypt parameters out of range")
        if not 1 <= len(salt) <= _MAX_SALT or not 16 <= len(digest) <= _MAX_DIGEST:
            raise ValueError("scrypt record has a salt or digest of the wrong length")
        return cls(n, r, p, salt, digest)

    def serialise(self) -> str:
        return "$".join((
            _SCHEME, str(self.n), str(self.r), str(self.p),
            base64.b64encode(self.salt).decode("ascii"),
            base64.b64encode(self.digest).decode("ascii"),
        ))

    def matches(self, candidate: str) -> bool:
        derived = self._scrypt(
            candidate, self.salt, self.n, self.r, self.p, len(self.digest)
        )
        return hmac.compare_digest(derived, self.digest)


@dataclass(frozen=True)
class PasswordLookup:
    """One reading of the store. Never cached — the next request reads again."""

    state: PasswordState
    record: _StoredHash | None = None
    damaged: bool = False
    reason: str | None = None
    remedy: str | None = None

    def matches(self, candidate: str) -> bool:
        """Whether *candidate* is the stored password. Slow by design (scrypt)."""
        if self.record is None or len(candidate) > MAX_LENGTH:
            return False
        return self.record.matches(candidate)


@dataclass(frozen=True)
class SetupCode:
    """A one-time setup code as stored: `setup-code$1$issued$issuer$sent$code`."""

    code: str
    issued_at: int
    issuer: CodeIssuer
    sent: bool

    @property
    def expires_at(self) -> int:
        return self.issued_at + CODE_TTL_SECONDS

    def expired(self, now: float) -> bool:
        return now >= self.expires_at or self.issued_at > now + _CLOCK_SKEW_S

    def matches(self, presented: str) -> bool:
        """Whether *presented* is this code. Constant time; forgives case, spaces,
        hyphens and the look-alike letters."""
        return hmac.compare_digest(
            normalise_code(presented).encode("utf-8"), self.code.encode("utf-8")
        )

    @property
    def display(self) -> str:
        """`XXXX-XXXX-XXXX` — grouped so a person can read it back."""
        return "-".join(self.code[i:i + 4] for i in range(0, len(self.code), 4))

    @property
    def expires_utc(self) -> str:
        return _dt.datetime.fromtimestamp(self.expires_at, _dt.UTC).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    def serialise(self) -> str:
        return "$".join((
            _CODE_SCHEME, "1", str(self.issued_at), self.issuer,
            "1" if self.sent else "0", self.code,
        ))

    @classmethod
    def parse(cls, text: str) -> SetupCode:
        parts = text.strip().split("$")
        if (len(parts) != 6 or parts[0] != _CODE_SCHEME or parts[1] != "1"
                or parts[3] not in ("platform", "host") or parts[4] not in ("0", "1")):
            raise ValueError("not a setup-code record")
        code = parts[5]
        if len(code) != _CODE_SYMBOLS or any(c not in _CODE_ALPHABET for c in code):
            raise ValueError("setup-code record carries a malformed code")
        issuer: CodeIssuer = "platform" if parts[3] == "platform" else "host"
        return cls(code, int(parts[2]), issuer, parts[4] == "1")


def normalise_code(presented: str) -> str:
    """What a person typed, in the form the code is stored in.

    Case, spaces and hyphens are ignored, and the three look-alikes Crockford
    base32 leaves out are read as the digits they are mistaken for.
    """
    return "".join(presented.split()).replace("-", "").upper().translate(_CODE_TYPO_MAP)


def weakness(candidate: str, username: str) -> str | None:
    """Why *candidate* may not become the password, or ``None`` when it may."""
    if len(candidate) < MIN_LENGTH:
        return f"a password needs at least {MIN_LENGTH} characters"
    if len(candidate) > MAX_LENGTH:
        return f"a password may be at most {MAX_LENGTH} characters"
    if candidate.casefold() == username.casefold():
        return "the password may not be the username"
    return None


class ControlPlanePassword:
    """The dashboard password and its setup code. Stateless: every call reads the store.

    Every method does blocking I/O (and `set` a 16 MiB scrypt), so an async caller
    runs it through `asyncio.to_thread`. The only thing an instance remembers is
    which failure it last reported, so a store that stays unreadable is reported
    once rather than on every request.
    """

    def __init__(self) -> None:
        self._reported: str | None = None

    def _report(self, state: str, level: str, message: str, details: dict[str, Any]) -> None:
        """Loud the first time *state* is seen, quiet while it persists."""
        first = self._reported != state
        self._reported = state
        emit = getattr(log.control_plane, level if first else "debug")
        emit(message, extra={"_fields": {**details, "repeat": not first}})

    # ---------------------------------------------------------------- state

    def lookup(self) -> PasswordLookup:
        """Read the store once and say which of the three states the install is in."""
        # 1. ENTRY
        log.control_plane.debug("[control_plane] password.lookup: entry")

        # 2. DECISION — absent and unreadable are told apart HERE, by the store.
        try:
            raw = read_located_secret(HASH_SERVICE)
        except SecretStoreUnreadable as exc:
            self._report(
                "unavailable", "warning",
                "[control_plane] password.lookup: exit — the secret store cannot be "
                "read; sign-in is refused until it can",
                {"reason": str(exc)[:200], "remedy": STORE_REMEDY},
            )
            return PasswordLookup(
                "unavailable", reason="the password store cannot be read",
                remedy=STORE_REMEDY,
            )
        if raw is None:
            self._reported = None
            log.control_plane.debug("[control_plane] password.lookup: exit — setup")
            return PasswordLookup("setup")

        # 3. STEP — parse. A damaged record is REPORTED, then treated as setup (L3):
        # the setup code still proves ownership, so nobody is locked out and nobody
        # who lacks the code gets in.
        try:
            parsed = _StoredHash.parse(raw)
        except ValueError as exc:
            self._report(
                "damaged", "error",
                "[control_plane] password.lookup: exit — the stored dashboard password "
                "record is damaged; the dashboard is back in setup mode",
                {"reason": str(exc)[:200], "remedy": RESET_REMEDY},
            )
            return PasswordLookup(
                "setup", damaged=True,
                reason="the stored dashboard password record is damaged",
                remedy=RESET_REMEDY,
            )

        # 4. EXIT
        self._reported = None
        log.control_plane.debug("[control_plane] password.lookup: exit — ready")
        return PasswordLookup("ready", record=parsed)

    # --------------------------------------------------------------- hash

    def set(self, new_password: str) -> str | None:
        """Store *new_password* as a salted hash. Returns the record it replaced.

        Reads back through the lookup a restart performs; on a mismatch the record
        it replaced goes back, so a failure leaves the password as it was. Raises
        :class:`PasswordStoreUnavailable`.
        """
        # 1. ENTRY — the password and its hash are never logged.
        log.control_plane.info(
            "[control_plane] password.set: entry",
            extra={"_fields": {"service": HASH_SERVICE}},
        )

        # 2. DECISION — what is being replaced, and a fresh salt.
        try:
            previous = read_located_secret(HASH_SERVICE)
        except SecretStoreUnreadable as exc:
            raise PasswordStoreUnavailable(str(exc)) from exc
        encoded = _StoredHash.derive(new_password, secrets.token_bytes(_SALT_BYTES)).serialise()

        # 3. STEP — persist, read back, undo on disagreement.
        self._write(HASH_SERVICE, encoded, previous, what="password")

        # 4. EXIT
        had_one = previous is not None
        log.control_plane.info(
            "[control_plane] password.set: exit — stored as a salted hash",
            extra={"_fields": {"service": HASH_SERVICE, "replaced_earlier": had_one}},
        )
        return previous

    def clear(self) -> None:
        """Forget the password. The install is in setup mode on the next request."""
        log.control_plane.info(
            "[control_plane] password.clear: entry",
            extra={"_fields": {"service": HASH_SERVICE}},
        )
        try:
            delete_secret(HASH_SERVICE)
            still = read_located_secret(HASH_SERVICE)
        except SecretStoreUnreadable as exc:
            raise PasswordStoreUnavailable(str(exc)) from exc
        if still is not None:
            raise PasswordStoreUnavailable("the dashboard password is still stored after removal")
        log.control_plane.info("[control_plane] password.clear: exit — removed")

    def restore(self, previous: str | None) -> None:
        """Put back a hash record :meth:`set` returned — the rollback half of a transaction."""
        had_one = previous is not None
        log.control_plane.warning(
            "[control_plane] password.restore: entry — undoing a password change",
            extra={"_fields": {"had_previous": had_one}},
        )
        if previous is None:
            self.clear()
            return
        try:
            store_located_secret(HASH_SERVICE, previous)
        except Exception as exc:  # noqa: BLE001 — B5, never silent
            raise PasswordStoreUnavailable(f"could not restore the password: {exc}") from exc
        log.control_plane.warning("[control_plane] password.restore: exit — restored")

    # --------------------------------------------------------------- code

    def current_code(self) -> SetupCode | None:
        """The stored, UNEXPIRED setup code, or ``None``. Raises on an unreadable store."""
        log.control_plane.debug("[control_plane] password.current_code: entry")
        try:
            raw = read_located_secret(CODE_SERVICE)
        except SecretStoreUnreadable as exc:
            raise PasswordStoreUnavailable(str(exc)) from exc
        if not raw:
            log.control_plane.debug("[control_plane] password.current_code: exit — none")
            return None
        try:
            stored = SetupCode.parse(raw)
        except ValueError as exc:
            log.control_plane.error(
                "[control_plane] password.current_code: exit — the stored setup code is "
                "damaged; it is ignored and a fresh one will be issued",
                extra={"_fields": {"reason": str(exc)[:200], "remedy": RESET_REMEDY}},
            )
            return None
        if stored.expired(time.time()):
            log.control_plane.info(
                "[control_plane] password.current_code: exit — the stored setup code "
                "has expired",
                extra={"_fields": {"expired_at": stored.expires_utc}},
            )
            return None
        log.control_plane.debug("[control_plane] password.current_code: exit — valid")
        return stored

    def ensure_code(self) -> SetupCode:
        """An unexpired code is REUSED (restarts do not re-issue); otherwise one is issued."""
        reusable = self.current_code()
        if reusable is not None:
            log.control_plane.info(
                "[control_plane] password.ensure_code: exit — reusing the unexpired code",
                extra={"_fields": {"issuer": reusable.issuer, "sent": reusable.sent,
                                   "expires": reusable.expires_utc}},
            )
            return reusable
        return self.issue_code("platform")

    def issue_code(self, issuer: CodeIssuer) -> SetupCode:
        """Issue a FRESH code, replacing any earlier one. Raises PasswordStoreUnavailable."""
        # 1. ENTRY
        log.control_plane.info(
            "[control_plane] password.issue_code: entry",
            extra={"_fields": {"issuer": issuer}},
        )
        # 2. DECISION — 60 bits from the OS CSPRNG.
        try:
            previous = read_located_secret(CODE_SERVICE)
        except SecretStoreUnreadable as exc:
            raise PasswordStoreUnavailable(str(exc)) from exc
        issued = SetupCode(
            "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_SYMBOLS)),
            int(time.time()), issuer, sent=False,
        )
        # 3. STEP
        self._write(CODE_SERVICE, issued.serialise(), previous, what="setup code")
        # 4. EXIT — never the code, only when it stops working.
        log.control_plane.info(
            "[control_plane] password.issue_code: exit — issued",
            extra={"_fields": {"issuer": issuer, "expires": issued.expires_utc}},
        )
        return issued

    def mark_code_sent(self, issued: SetupCode) -> None:
        """Record that *issued* has been delivered, so a restart does not send it again."""
        stored = self.current_code()
        if stored is None or not hmac.compare_digest(stored.code, issued.code):
            log.control_plane.info(
                "[control_plane] password.mark_code_sent: exit — the code was replaced "
                "or used meanwhile; nothing to mark",
            )
            return
        try:
            store_located_secret(
                CODE_SERVICE,
                SetupCode(stored.code, stored.issued_at, stored.issuer, True).serialise(),
            )
        except Exception as exc:  # noqa: BLE001 — B5, never silent
            raise PasswordStoreUnavailable(f"could not mark the setup code sent: {exc}") from exc
        log.control_plane.info("[control_plane] password.mark_code_sent: exit — marked")

    def code_matches(self, presented: str) -> bool:
        """Whether *presented* is the current, unexpired setup code. Constant time."""
        stored = self.current_code()
        return stored is not None and stored.matches(presented)

    def consume_code(self) -> str | None:
        """Use the code up. Returns the record removed, for a rollback."""
        try:
            previous = read_located_secret(CODE_SERVICE)
        except SecretStoreUnreadable as exc:
            raise PasswordStoreUnavailable(str(exc)) from exc
        try:
            delete_secret(CODE_SERVICE)
        except SecretStoreUnreadable as exc:
            # A delete can fail HALF-WAY — the keyring entry gone, the file still
            # there — so the code goes back before the failure is reported as
            # "nothing changed".
            self._put_code_back(previous)
            raise PasswordStoreUnavailable(str(exc)) from exc
        log.control_plane.info("[control_plane] password.consume_code: exit — used up")
        return previous

    # ------------------------------------------------------- transactions

    def replace_password(self, new_password: str, *, consume_code: bool) -> str:
        """Rotate the token, store the hash and (at setup) use the code up — all or nothing.

        Returns the new bearer token. A failure part-way puts back whatever was
        already changed, so password, code and token are as they were (Q29), and
        raises :class:`PasswordStoreUnavailable`. The caller serialises.
        """
        # 1. ENTRY
        log.control_plane.info(
            "[control_plane] password.replace_password: entry",
            extra={"_fields": {"consume_code": consume_code}},
        )
        # 2. DECISION — what a rollback would put back.
        previous_token = read_credential()

        # 3. STEP — token first: a token can always be put back.
        try:
            minted = rotate_credential()
        except CredentialUnavailable as exc:
            raise PasswordStoreUnavailable(f"could not rotate the access token: {exc}") from exc

        replaced: str | None = None
        hash_written = False
        try:
            replaced = self.set(new_password)
            hash_written = True
            if consume_code:
                self.consume_code()
        except Exception as exc:
            self._undo(
                "replace_password", previous_token=previous_token,
                hash_written=hash_written, replaced=replaced,
            )
            if isinstance(exc, PasswordStoreUnavailable):
                raise
            raise PasswordStoreUnavailable(f"the password change failed: {exc}") from exc

        # 4. EXIT
        log.control_plane.info(
            "[control_plane] password.replace_password: exit — password stored and "
            "token rotated; tokens issued before this open nothing",
            extra={"_fields": {"consume_code": consume_code}},
        )
        return minted

    def reset(self) -> SetupCode:
        """The host CLI's reset: fresh code, rotated token, no password — all or nothing."""
        # 1. ENTRY
        log.control_plane.warning("[control_plane] password.reset: entry")
        # 2. DECISION — what a rollback would put back.
        try:
            previous_code = read_located_secret(CODE_SERVICE)
        except SecretStoreUnreadable as exc:
            raise PasswordStoreUnavailable(str(exc)) from exc
        previous_token = read_credential()

        # 3. STEP — code, then token, then the hash; each failure undoes the rest.
        issued = self.issue_code("host")
        try:
            rotate_credential()
        except CredentialUnavailable as exc:
            changed = [] if self._put_code_back(previous_code) else ["a new setup code was stored"]
            self._raise_reset_failure(f"could not rotate the access token: {exc}", changed, exc)
        try:
            self.clear()
        except PasswordStoreUnavailable as exc:
            changed = []
            if not self._restore_token(previous_token):
                changed.append("the access token was rotated")
            if not self._put_code_back(previous_code):
                changed.append("a new setup code was stored")
            self._raise_reset_failure(str(exc), changed, exc)

        # 4. EXIT — never the code.
        log.control_plane.warning(
            "[control_plane] password.reset: exit — password cleared, token rotated, "
            "fresh setup code issued",
            extra={"_fields": {"expires": issued.expires_utc}},
        )
        return issued

    # ---------------------------------------------------------- internals

    def _write(self, service: str, encoded: str, previous: str | None, *, what: str) -> None:
        """Write, read back, and put *previous* back if the store disagrees."""
        try:
            description = store_located_secret(service, encoded)
        except Exception as exc:  # noqa: BLE001 — B5, never silent
            log.control_plane.error(
                f"[control_plane] password.write: exit — the secret store refused the "
                f"{what}; nothing changed",
                exc_info=exc,
                extra={"_fields": {"service": service, "remedy": STORE_REMEDY}},
            )
            raise PasswordStoreUnavailable(f"could not store the {what}: {exc}") from exc
        try:
            read_back = read_located_secret(service)
        except SecretStoreUnreadable as exc:
            read_back = None
            log.control_plane.error(
                f"[control_plane] password.write: the {what} could not be read back",
                exc_info=exc,
            )
        if read_back is None or not hmac.compare_digest(
            read_back.encode("utf-8"), encoded.encode("utf-8")
        ):
            try:
                if previous is None:
                    delete_secret(service)
                else:
                    store_located_secret(service, previous)
            except Exception as undo_exc:  # noqa: BLE001 — B5, never silent
                log.control_plane.error(
                    f"[control_plane] password.write: could not put the previous {what} back",
                    exc_info=undo_exc,
                    extra={"_fields": {"service": service, "remedy": RESET_REMEDY}},
                )
            log.control_plane.error(
                f"[control_plane] password.write: exit — the {what} did not read back; "
                "the previous one is back in place",
                extra={"_fields": {"service": service, "stored_in": description}},
            )
            raise PasswordStoreUnavailable(f"the {what} did not read back")

    def _undo(self, step: str, *, previous_token: str | None, hash_written: bool,
              replaced: str | None) -> None:
        log.control_plane.error(
            f"[control_plane] password.{step}: failed part-way — putting back what "
            "was already changed",
            extra={"_fields": {"hash_written": hash_written}},
        )
        if hash_written:
            try:
                self.restore(replaced)
            except PasswordStoreUnavailable as exc:
                log.control_plane.error(
                    f"[control_plane] password.{step}: could NOT put the previous "
                    "password back",
                    exc_info=exc,
                    extra={"_fields": {"remedy": RESET_REMEDY}},
                )
        self._restore_token(previous_token)

    @staticmethod
    def _raise_reset_failure(message: str, changed: list[str], cause: BaseException) -> NoReturn:
        """"Nothing was changed" only when that is true."""
        if changed:
            raise PasswordResetIncomplete(message, changed=changed) from cause
        raise PasswordStoreUnavailable(message) from cause

    def _restore_token(self, previous_token: str | None) -> bool:
        """Put the previous access token back. False when that failed."""
        if previous_token is None:
            return True
        try:
            rotate_credential(restore=previous_token)
        except CredentialUnavailable as exc:
            log.control_plane.error(
                "[control_plane] password: could NOT put the previous access token back "
                "— after a restart everyone signs in again",
                exc_info=exc,
            )
            return False
        return True

    def _put_code_back(self, previous_code: str | None) -> bool:
        """Put the previous setup-code record back. False when that failed."""
        try:
            if previous_code is None:
                delete_secret(CODE_SERVICE)
            else:
                store_located_secret(CODE_SERVICE, previous_code)
        except Exception as exc:  # noqa: BLE001 — B5, never silent
            log.control_plane.error(
                "[control_plane] password: could not put the previous setup code back",
                exc_info=exc,
            )
            return False
        return True


class PasswordStoreHealth:
    """Reports the password store to the health sweep, so an unreadable one pages (L3).

    READ-ONLY. It never issues a code or repairs a record: a probe that changes the
    thing it measures is worse than no probe. The sweep re-collects every tick, which
    is the retry; the remedy is what the page carries.
    """

    def __init__(self, password: ControlPlanePassword) -> None:
        self._password = password

    @property
    def contributor_name(self) -> str:
        return "control_plane_password"

    async def health_check(self) -> HealthStatus:
        t0 = time.monotonic()
        state = await asyncio.to_thread(self._password.lookup)
        latency_ms = (time.monotonic() - t0) * 1000
        if state.state == "unavailable" or state.damaged:
            return HealthStatus(
                name=self.contributor_name, status="down", message=state.reason,
                latency_ms=latency_ms, remedy=state.remedy,
            )
        if state.state == "setup":
            # A CODE NOBODY HAS RECEIVED is an install the owner cannot claim —
            # say so, rather than reporting setup mode as fine.
            try:
                current = await asyncio.to_thread(self._password.current_code)
            except PasswordStoreUnavailable as exc:
                return HealthStatus(
                    name=self.contributor_name, status="down", message=str(exc),
                    latency_ms=latency_ms, remedy=remedy_for(exc),
                )
            if current is not None and not current.sent:
                return HealthStatus(
                    name=self.contributor_name, status="degraded",
                    message="setup mode — the current setup code has not reached anyone",
                    latency_ms=latency_ms, remedy=RESET_REMEDY,
                )
        return HealthStatus(
            name=self.contributor_name, status="ok",
            message=(
                "setup mode — no dashboard password yet; the owner sets one with the "
                "setup code" if state.state == "setup" else None
            ),
            latency_ms=latency_ms,
        )
