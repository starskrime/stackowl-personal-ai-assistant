"""LLM-derived acceptance — the POST-HOC, FAIL-CLOSED second layer (verification B3).

The deterministic :class:`~stackowl.pipeline.acceptance.AcceptanceChecker` catches
the case where a turn DECLARED an expected artifact up front. This layer covers the
complementary case: a turn that did NOT declare an outcome but whose draft CLAIMS
one ("All done, saved it to disk"). It asks a model — post-hoc, off the delivery
critical path — to extract a deterministically-observable expectation from the
draft, then hands that expectation back to the deterministic checker to OBSERVE
against reality.

Two hard guarantees (owner-settled, spec §5 Branch 3):

* **Flag-gated, default OFF** — engaged only when ``settings.acceptance_tier`` is a
  non-empty tier name. Off ⇒ this module is never reached (byte-identical).
* **FAIL-CLOSED** — if the model is unavailable, times out, or returns anything
  unparseable, the deriver asserts NO expectation (returns ``None``). The checker
  then renders no verdict, so the turn falls back to its prior signal. This layer
  can never MANUFACTURE a positive acceptance it could not measure, and it never
  fabricates a failure from a model it could not reach.

General and vendor-neutral: the tier is config-driven and resolved through the
provider cascade; the prompt is English glue with the intent/draft inlined; the
reply is parsed by a fixed structured grammar, never a keyword list.
"""

from __future__ import annotations

import re

from stackowl.infra.observability import log
from stackowl.objectives.model import ExpectedOutcome
from stackowl.providers.base import Message
from stackowl.providers.registry import ProviderRegistry

_DERIVE_MAX_TOKENS = 64
_DERIVE_TEMPERATURE = 0.0

# The reply grammar: ``ARTIFACT: <dir>`` (a saved-file claim, optional directory) or
# ``NONE`` (no observable file outcome claimed). Parsed deterministically; anything
# else ⇒ no expectation (fail-closed).
_ARTIFACT_RE = re.compile(r"^\s*ARTIFACT\s*:?\s*(?P<dir>.*?)\s*$", re.IGNORECASE)


def acceptance_skip_reason(tier: str) -> str | None:
    """Why the LLM-derived acceptance layer is SKIPPED for this turn, or None when
    it is engaged (F-14).

    The layer is intentionally OFF by default (an empty ``acceptance_tier``) — it
    adds a model round-trip per turn, so flipping it on is a deliberate cost/latency
    choice, NOT a silent default. But "OFF" must be OBSERVABLE: a turn that CLAIMS a
    file but declared no outcome simply goes unverified, and that fact should be
    visible in the trace rather than a silent early-return. This returns a short,
    config-driven explanation a caller (or the deriver itself) can log so
    "we did not verify this claim" is never invisible.
    """
    if not tier:
        return (
            "acceptance_tier unset — LLM-derived acceptance OFF; an undeclared "
            "outcome claim was not verified"
        )
    return None


class LlmAcceptanceDeriver:
    """Derive an :class:`ExpectedOutcome` from a turn's draft, fail-closed."""

    def __init__(self, provider_registry: ProviderRegistry, tier: str) -> None:
        self._provider_registry = provider_registry
        self._tier = tier

    def _build_prompt(self, intent: str, draft: str) -> str:
        return (
            "You verify whether an assistant's reply CLAIMS it produced a saved "
            "file or download on disk. Read the task and the reply. If the reply "
            "claims a file was saved or downloaded, respond with exactly "
            "`ARTIFACT: <directory>` (give the directory if the reply names one, "
            "otherwise just `ARTIFACT:`). If the reply does NOT claim a saved file "
            "(it only fetched, summarized, answered, or notified), respond with "
            "exactly `NONE`. Respond with one line and nothing else.\n\n"
            f"Task: {intent}\n"
            f"Reply: {draft}"
        )

    async def derive(self, *, intent: str, draft: str) -> ExpectedOutcome | None:
        """Return a declared-artifact expectation extracted from ``draft``, or None.

        FAIL-CLOSED: any provider error / empty / unparseable reply ⇒ None (no
        expectation asserted). ``NONE`` from the model ⇒ None. ``ARTIFACT: dir`` ⇒
        an artifact ExpectedOutcome (empty dir ⇒ None dir ⇒ the workspace root)."""
        # F-14 — make the OFF-by-default skip OBSERVABLE instead of a silent return.
        skip_reason = acceptance_skip_reason(self._tier)
        if skip_reason is not None:
            log.engine.debug(
                "[acceptance-llm] derive: skipped — layer off",
                extra={"_fields": {"reason": skip_reason}},
            )
            return None
        if not draft.strip():
            return None
        messages = [Message(role="user", content=self._build_prompt(intent, draft))]
        try:
            provider, model = self._provider_registry.get_with_cascade_and_model(self._tier)
            result = await provider.complete(
                messages, model=model,
                max_tokens=_DERIVE_MAX_TOKENS, temperature=_DERIVE_TEMPERATURE,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed: no expectation, never raise
            log.engine.debug(
                "[acceptance-llm] derive: provider unavailable — no expectation",
                extra={"_fields": {"err": type(exc).__name__}},
            )
            return None

        line = (result.content or "").strip().splitlines()[0] if result.content else ""
        match = _ARTIFACT_RE.match(line)
        if match is None:
            return None  # "NONE" or anything unparseable ⇒ no expectation
        raw_dir = match.group("dir").strip()
        return ExpectedOutcome(kind="artifact", artifact_dir=raw_dir or None)
