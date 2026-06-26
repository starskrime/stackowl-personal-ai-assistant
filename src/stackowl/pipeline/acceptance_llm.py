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
        if not self._tier or not draft.strip():
            return None
        messages = [Message(role="user", content=self._build_prompt(intent, draft))]
        try:
            provider = self._provider_registry.get_with_cascade(self._tier)
            result = await provider.complete(
                messages, model="",
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
