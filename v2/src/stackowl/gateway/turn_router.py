"""TurnRouter — the deterministic EXPLICIT-SIGNAL parser (concurrent-msg §6.1).

When a message arrives while a turn is already in-flight for the session, the
orchestrator first asks: did the user send an EXPLICIT, unambiguous routing
signal? This module answers that with ZERO LLM cost — pure, deterministic,
fail-safe signal parsing. Only when there is NO explicit signal (``NONE`` /
UNSIGNALED) does the conservative STEER-vs-NEW classifier (Task 15) get consulted.

Explicit signals recognised here (all language-neutral / structural — see the
multilingual rule below):

  * a recognised SLASH-COMMAND — ``/stop`` → STOP, ``/steer`` → STEER, ``/new`` →
    NEW. Slash-command tokens are language-neutral command tokens (the platform's
    universal command surface), so matching them literally is correct. The command
    NAME is extracted via the same parser the gateway scanner uses
    (:data:`stackowl.gateway.scanner._SLASH_CMD_RE`) so there is ONE command-token
    extractor in the codebase (DRY) — we do not reinvent a slash parser here.
  * a Telegram REPLY-to-the-in-flight-message (``is_reply_to_inflight=True``) — a
    language-neutral STRUCTURAL signal (replying to the running turn's message is
    an unambiguous "this refines THAT turn"): → STEER.

Multilingual / no-hardcoded-English rule (a standing project rule): free-text
English words ("stop"/"cancel"/"wait"/"no") are NOT used as the matcher — that
would break the multilingual platform. The ONE concession is a BARE stop token,
and it is a small CONFIGURABLE, casefolded :class:`StopTokens` set (default
includes common forms so a user typing a plain stop word still halts), NOT a
hardcoded English literal baked into the control flow — callers override it for
other locales. Free-text intent ("no, I meant Y") is deliberately NOT parsed here:
it returns NONE → Task 15's classifier decides.

The pending-clarify ANSWER path is NOT re-implemented here. A typed answer to a
pending clarify folds into the EXISTING clarify ANSWER path
(:meth:`ClarifyGateway.peek_for_session` + :class:`ClarifyIntentClassifier`); the
orchestrator checks clarify-pending BEFORE consulting this parser, so a plain
answer reaches this function only when there is no pending clarify, where it is
correctly NONE (deferred to the classifier). The :data:`ExplicitSignal.REPLY`
member names that clarify-answer outcome for callers that fold the two together.
"""

from __future__ import annotations

import enum
import unicodedata
from dataclasses import dataclass

from stackowl.gateway.scanner import _SLASH_CMD_RE
from stackowl.infra.observability import log

# Default bare stop tokens — a small, casefolded, CONFIGURABLE set (NOT a
# hardcoded English literal in the control flow; per the no-hardcoded-English
# rule a caller supplies a locale-specific set). The defaults cover common
# plain-text halt forms so a user who types a bare stop word still halts even
# without the slash prefix; the canonical signal remains the language-neutral
# ``/stop`` command, which works regardless of this set.
_DEFAULT_STOP_TOKENS: frozenset[str] = frozenset({"stop", "halt", "abort"})

# Canonical slash-command tokens → signal. Language-neutral command tokens.
_SLASH_STOP = "stop"
_SLASH_STEER = "steer"
_SLASH_NEW = "new"


class ExplicitSignal(enum.Enum):
    """Deterministic routing outcome of an explicit user signal.

    * ``STOP``  — cooperative-stop the running turn (→ ``TurnRegistry.request_stop``).
    * ``STEER`` — fold into the running turn's mailbox (→ ``TurnRegistry.try_steer``).
    * ``NEW``   — start a fresh queued-new turn (never onto the running turn).
    * ``REPLY`` — a pending-clarify answer; fold into the clarify ANSWER path.
    * ``NONE``  — UNSIGNALED: no explicit signal → defer to Task 15's classifier.
    """

    STOP = "stop"
    STEER = "steer"
    NEW = "new"
    REPLY = "reply"
    NONE = "none"


@dataclass(frozen=True)
class StopTokens:
    """A configurable, casefolded set of bare stop tokens (multilingual-safe).

    Wraps a ``frozenset`` of tokens. Membership is tested casefolded so callers
    may pass tokens in any case. An empty set disables bare-token stop matching
    entirely (the language-neutral ``/stop`` command still works).
    """

    tokens: frozenset[str]

    def matches(self, word: str) -> bool:
        if not self.tokens:
            return False
        folded = word.casefold()
        return any(folded == t.casefold() for t in self.tokens)


DEFAULT_STOP_TOKENS: StopTokens = StopTokens(_DEFAULT_STOP_TOKENS)


def _normalize(text: str) -> str:
    """NFC-normalise, strip RTL marks, and trim — mirrors the scanner's intake.

    Keeps signal parsing consistent with :func:`GatewayScanner.scan` so a
    ``/steer`` recognised by the scanner is recognised here too.
    """
    # U+200F RTL mark, U+200E LTR mark — same strip the scanner applies.
    cleaned = unicodedata.normalize("NFC", text).replace("‏", "").replace("‎", "")
    return cleaned.strip()


def _slash_command(text: str) -> str | None:
    """Extract a leading slash-command NAME (lowercased) via the scanner's parser.

    Reuses :data:`scanner._SLASH_CMD_RE` (the ONE command-token extractor) so we
    do not reinvent a slash parser (DRY). Returns the casefolded command name or
    ``None`` when the text does not begin with a ``/word`` command token.
    """
    m = _SLASH_CMD_RE.match(text)
    if m is None:
        return None
    return m.group(1).casefold()


def parse_explicit_signal(
    text: str,
    *,
    is_reply_to_inflight: bool,
    stop_tokens: StopTokens = DEFAULT_STOP_TOKENS,
) -> ExplicitSignal:
    """Map an arriving message to a deterministic :class:`ExplicitSignal`.

    Pure and fail-safe: any unrecognised / odd input yields
    :data:`ExplicitSignal.NONE` (UNSIGNALED → Task 15's classifier) and NEVER
    raises. Precedence (most explicit first):

      1. Leading slash-command ``/stop`` / ``/steer`` / ``/new`` — language-neutral
         command tokens (extracted via the scanner's command parser). An
         UNRELATED slash-command (e.g. ``/help``) is NOT a steer/stop/new signal →
         it falls through to NONE (the normal command route handles it).
      2. Structural ``is_reply_to_inflight`` (a Telegram reply to the running
         turn's message) → STEER. Honoured even with an empty body. An explicit
         ``/new`` from rule 1 still wins (an unambiguous "fresh turn" intent).
      3. A BARE stop token (configurable, casefolded ``stop_tokens`` set) as the
         leading word → STOP.
      4. Otherwise → NONE.

    :param text: the raw arriving message text.
    :param is_reply_to_inflight: structural signal — the message is a channel
        reply to the in-flight turn's message (Telegram reply-to).
    :param stop_tokens: configurable bare-stop token set (multilingual-safe;
        defaults to :data:`DEFAULT_STOP_TOKENS`).
    """
    try:
        normalized = _normalize(text)

        # 1. Slash-command — the canonical, language-neutral explicit signal.
        cmd = _slash_command(normalized)
        if cmd is not None:
            if cmd == _SLASH_STOP:
                signal = ExplicitSignal.STOP
            elif cmd == _SLASH_STEER:
                signal = ExplicitSignal.STEER
            elif cmd == _SLASH_NEW:
                signal = ExplicitSignal.NEW
            else:
                # An unrelated slash-command is NOT a turn-routing signal here.
                signal = ExplicitSignal.NONE
            if signal is not ExplicitSignal.NONE:
                log.gateway.info(
                    "[turn] parse_explicit_signal: slash-command signal",
                    extra={"_fields": {"cmd": cmd, "signal": signal.value}},
                )
                return signal

        # 2. Structural reply-to-the-in-flight-message → STEER (language-neutral).
        if is_reply_to_inflight:
            log.gateway.info(
                "[turn] parse_explicit_signal: reply-to-inflight structural STEER",
                extra={"_fields": {"signal": ExplicitSignal.STEER.value}},
            )
            return ExplicitSignal.STEER

        # 3. Bare stop token (configurable, casefolded — NOT a hardcoded literal).
        if normalized:
            leading = normalized.split(maxsplit=1)[0]
            if stop_tokens.matches(leading):
                log.gateway.info(
                    "[turn] parse_explicit_signal: bare stop-token signal",
                    extra={"_fields": {"signal": ExplicitSignal.STOP.value}},
                )
                return ExplicitSignal.STOP

        # 4. No explicit signal — UNSIGNALED → defer to the Task 15 classifier.
        log.gateway.debug(
            "[turn] parse_explicit_signal: no explicit signal — UNSIGNALED",
            extra={"_fields": {"text_len": len(text), "is_reply": is_reply_to_inflight}},
        )
        return ExplicitSignal.NONE
    except Exception as exc:  # self-healing — parsing must NEVER crash intake.
        log.gateway.error(
            "[turn] parse_explicit_signal: failed — treating as UNSIGNALED",
            exc_info=exc,
            extra={"_fields": {"text_len": len(text), "is_reply": is_reply_to_inflight}},
        )
        return ExplicitSignal.NONE
