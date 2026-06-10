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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.gateway.scanner import _SLASH_CMD_RE
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.interaction.intent_classifier import ClarifyIntentClassifier

# The running turn's stage-2 coherence judge. Given the running ask and a proposed
# STEER message, it returns ``True`` to VETO (the steer is incoherent with the
# in-flight goal → fall back to NEW) or ``False`` to allow the steer. Injected so
# the running turn supplies its OWN judgment (the D3 two-stage pattern); fully
# mockable in tests and decoupled from the TurnRegistry internals.
TurnVeto = Callable[..., Awaitable[bool]]

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


class TurnRouter:
    """Routes a mid-turn message to STOP / STEER / NEW with a conservative bias.

    Two-layer decision (concurrent-msg §6.2/§6.3):

      1. :func:`parse_explicit_signal` — a ZERO-cost deterministic parse. Any
         explicit signal (``/stop``, ``/steer``, ``/new``, reply-to-inflight, a
         bare stop token) wins immediately and the classifier is never consulted.
      2. On ``NONE`` (UNSIGNALED), the conservative
         :meth:`ClarifyIntentClassifier.is_steer` proposes STEER vs NEW. STEER is
         proposed ONLY at HIGH confidence; every uncertainty defaults to NEW.
      3. **Turn-veto (stage 2, the D3 two-stage pattern).** A *proposed* STEER is
         offered to the running turn's own coherence judge (``turn_veto``). The
         turn may VETO a steer that is incoherent with its in-flight goal → NEW.

    The whole asymmetric-cost safety principle is: a wrong STEER poisons the
    running turn AND loses the new ask invisibly (expensive), while a wrong NEW is
    a recoverable, visible second answer (cheap). So the router fail-safes to NEW
    EVERYWHERE — an uncertain classifier, a classifier error, OR a crashing veto
    judge all collapse to NEW. ``route`` never raises.

    The ``REPLY`` (pending-clarify answer) outcome is NOT decided here — the
    orchestrator checks clarify-pending BEFORE consulting the router (see the
    module docstring), so the router only sees messages with no pending clarify.
    """

    def __init__(
        self,
        classifier: ClarifyIntentClassifier,
        *,
        turn_veto: TurnVeto | None = None,
    ) -> None:
        self._classifier = classifier
        self._turn_veto = turn_veto

    async def route(
        self,
        *,
        running_ask: str,
        message: str,
        is_reply_to_inflight: bool = False,
        stop_tokens: StopTokens = DEFAULT_STOP_TOKENS,
    ) -> ExplicitSignal:
        """Resolve a mid-turn ``message`` to a routing :class:`ExplicitSignal`.

        :param running_ask: the in-flight turn's original ask (context for the
            STEER-vs-NEW classifier and the turn-veto judge).
        :param message: the arriving mid-turn message text.
        :param is_reply_to_inflight: structural reply-to-the-running-turn signal.
        :param stop_tokens: configurable bare-stop token set (multilingual-safe).

        Fail-safe → :data:`ExplicitSignal.NEW` on ANY classifier or veto error.
        Never raises. Returns one of STOP / STEER / NEW (never NONE — an UNSIGNALED
        message is always resolved by the classifier to STEER or NEW).
        """
        # 1. ENTRY
        log.gateway.debug(
            "[turn] route: entry",
            extra={
                "_fields": {
                    "running_ask_len": len(running_ask),
                    "message_len": len(message),
                    "is_reply": is_reply_to_inflight,
                }
            },
        )
        try:
            # Stage 0 — deterministic explicit signal (zero LLM cost).
            signal = parse_explicit_signal(
                message,
                is_reply_to_inflight=is_reply_to_inflight,
                stop_tokens=stop_tokens,
            )
            if signal is not ExplicitSignal.NONE:
                log.gateway.info(
                    "[turn] route: explicit signal — short-circuit",
                    extra={"_fields": {"signal": signal.value}},
                )
                return signal

            # Stage 1 — conservative STEER-vs-NEW (high-confidence STEER only).
            proposed_steer = await self._classifier.is_steer(
                running_ask=running_ask, message=message,
            )
            if not proposed_steer:
                log.gateway.info(
                    "[turn] route: classifier → NEW (uncertain/unrelated)",
                    extra={"_fields": {"signal": ExplicitSignal.NEW.value}},
                )
                return ExplicitSignal.NEW

            # Stage 2 — turn-veto: the running turn judges the steer's coherence.
            if self._turn_veto is not None and await self._veto(running_ask, message):
                log.gateway.info(
                    "[turn] route: STEER vetoed by running turn — fall back to NEW",
                    extra={"_fields": {"signal": ExplicitSignal.NEW.value}},
                )
                return ExplicitSignal.NEW

            # 4. EXIT — a high-confidence, un-vetoed STEER.
            log.gateway.info(
                "[turn] route: STEER (high-confidence, not vetoed)",
                extra={"_fields": {"signal": ExplicitSignal.STEER.value}},
            )
            return ExplicitSignal.STEER
        except Exception as exc:  # self-healing — routing must NEVER crash intake.
            log.gateway.error(
                "[turn] route: failed — fail-safe to NEW",
                exc_info=exc,
                extra={"_fields": {"signal": ExplicitSignal.NEW.value}},
            )
            return ExplicitSignal.NEW

    async def _veto(self, running_ask: str, message: str) -> bool:
        """Invoke the turn-veto judge; a crashing judge fail-safes to VETO (→ NEW).

        A proposed STEER is only honoured when the turn EXPLICITLY does not veto
        it. If the veto judge itself errors we treat that as a veto (return
        ``True``) so a broken judge collapses to the cheap, safe NEW direction
        rather than letting an unvetted steer poison the running turn.
        """
        veto = self._turn_veto
        if veto is None:  # pragma: no cover — guarded by the caller
            return False
        try:
            return bool(await veto(running_ask=running_ask, message=message))
        except Exception as exc:  # self-healing — a broken judge → veto (NEW).
            log.gateway.error(
                "[turn] route: turn-veto judge failed — treating as veto (NEW)",
                exc_info=exc,
                extra={"_fields": {"message_len": len(message)}},
            )
            return True
