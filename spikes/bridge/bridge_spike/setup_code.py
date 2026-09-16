"""Setup-code minting for the Bridge kit's first-start passkey ceremony
(AD-16, FR66): a single code, minted only when the kit process starts, that
authorizes exactly one passkey registration and then can never be used
again.

No route anywhere in this kit calls `SetupCodeStore()` -- the only
constructor call lives in `kit.py`'s `_serve()`, at process start, before
the HTTPS listener even opens. There is deliberately no "mint a new code"
route: a network request can only *consume* the one code minted at start,
never create another.
"""

from __future__ import annotations

import hmac
import secrets

# Excludes 0/O and 1/I: this code is read off a terminal and typed (or
# read aloud) into a second device, so visually ambiguous characters would
# turn "works" into "half the owner's attempts fail for no visible reason".
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def generate_setup_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))


class SetupCodeStore:
    """Holds exactly one setup code for the lifetime of the kit process.

    Consume-once semantics: the first successful `consume()` call with the
    correct code invalidates it permanently, including for a second call
    with that same correct code -- there is no "un-consume" or refresh.
    """

    def __init__(self, code: str | None = None) -> None:
        self._code = code or generate_setup_code()
        self._consumed = False

    @property
    def code(self) -> str:
        return self._code

    @property
    def consumed(self) -> bool:
        return self._consumed

    def consume(self, candidate: str) -> bool:
        """True exactly once, for the first correct `candidate`. Every other
        call -- wrong code, or any call after the first success -- is False.

        `hmac.compare_digest` avoids leaking the code one byte at a time via
        a timing side channel, cheap insurance for something this kit prints
        to a terminal but still exposes over the network to consume.
        """
        if self._consumed:
            return False
        if not isinstance(candidate, str) or not hmac.compare_digest(candidate, self._code):
            return False
        self._consumed = True
        return True
