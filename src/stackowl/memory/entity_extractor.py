"""EntityExtractor — extracts named entities from text using an LLM.

Output is a list of :class:`ExtractedEntity` instances; failure paths
return ``[]`` and log a warning so the calling KuzuSyncJobHandler can
continue processing other facts without raising.

Sensitive-content filtering mirrors :class:`FactExtractor`: when any
configured regex matches the input ``text``, extraction is skipped
entirely (the entity may itself be sensitive — name of a person, account
identifier, etc.).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.providers.base import ModelProvider
    from stackowl.providers.registry import ProviderRegistry


_FENCE_OPEN_RE = re.compile(r"^\s*```(?:[A-Za-z0-9_-]+)?\s*\n?", re.UNICODE)
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$", re.UNICODE)

_PROMPT_TEMPLATE = (
    "Extract named entities from the text below.\n"
    "\n"
    'For every distinct entity, emit one JSON object with keys "name", '
    '"entity_type", and "mentions" (a list of short text snippets where the '
    "entity appeared).\n"
    "\n"
    "Valid entity_type values: PERSON, ORG, TOPIC, CONCEPT, LOCATION, OTHER.\n"
    "\n"
    "Return ONLY a JSON array. No commentary. No markdown code fences.\n"
    "\n"
    "Text:\n"
    "{text}\n"
)


class ExtractedEntity(BaseModel):
    """One entity returned by :meth:`EntityExtractor.extract`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    entity_type: str
    mentions: list[str]


_ENTITY_LIST_ADAPTER: TypeAdapter[list[ExtractedEntity]] = TypeAdapter(
    list[ExtractedEntity]
)


class EntityExtractor:
    """LLM-driven entity extractor used by :class:`KuzuSyncJobHandler`."""

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        sensitive_categories: list[str] | None = None,
        preferred_tier: str = "powerful",
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] entity_extractor.init: entry",
            extra={
                "_fields": {
                    "sensitive_count": len(sensitive_categories or []),
                    "preferred_tier": preferred_tier,
                }
            },
        )
        self._registry = provider_registry
        self._sensitive_categories = list(sensitive_categories or [])
        self._sensitive_patterns = [
            re.compile(pattern, re.UNICODE | re.IGNORECASE)
            for pattern in self._sensitive_categories
        ]
        self._preferred_tier = preferred_tier
        # 4. EXIT
        log.memory.debug("[memory] entity_extractor.init: exit")

    async def extract(self, text: str, fact_id: str) -> list[ExtractedEntity]:
        """Extract entities from ``text``. Returns ``[]`` on any failure."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] entity_extractor.extract: entry",
            extra={"_fields": {"fact_id": fact_id, "text_len": len(text)}},
        )
        TestModeGuard.assert_not_test_mode("entity_extractor.extract")

        # 2. DECISION — sensitive content short-circuits the LLM call
        is_sensitive, pattern = self._is_sensitive(text)
        if is_sensitive:
            log.memory.info(
                "[memory] entity_extractor.extract: sensitive — skipping",
                extra={"_fields": {"fact_id": fact_id, "category_match": pattern}},
            )
            return []

        resolved = self._resolve_provider()
        if resolved is None:
            log.memory.warning(
                "[memory] entity_extractor.extract: no provider available",
                extra={"_fields": {"fact_id": fact_id}},
            )
            return []
        provider, model = resolved

        prompt = _PROMPT_TEMPLATE.format(text=text)
        messages = [Message(role="user", content=prompt)]

        # 3. STEP — provider call
        try:
            result = await provider.complete(messages, model=model)
        except Exception as exc:
            # B5
            log.memory.warning(
                "[memory] entity_extractor.extract: provider call failed",
                exc_info=exc,
                extra={"_fields": {"fact_id": fact_id}},
            )
            return []
        log.memory.debug(
            "[memory] entity_extractor.extract: provider response received",
            extra={
                "_fields": {
                    "fact_id": fact_id,
                    "response_len": len(result.content),
                    "provider": result.provider_name,
                }
            },
        )

        # 3. STEP — parse JSON response
        entities = self._parse_response(result.content, fact_id)

        # 4. EXIT
        log.memory.info(
            "[memory] entity_extractor.extract: exit",
            extra={"_fields": {"fact_id": fact_id, "entity_count": len(entities)}},
        )
        return entities

    # ------------------------------------------------------------------ helpers

    def _resolve_provider(self) -> tuple[ModelProvider, str] | None:
        try:
            return self._registry.get_with_cascade(self._preferred_tier)
        except Exception as exc:
            # B5 — any provider lookup failure degrades to []
            log.memory.warning(
                "[memory] entity_extractor: provider lookup failed",
                exc_info=exc,
                extra={"_fields": {"preferred_tier": self._preferred_tier}},
            )
            return None

    def _is_sensitive(self, text: str) -> tuple[bool, str]:
        for pattern, compiled in zip(
            self._sensitive_categories, self._sensitive_patterns, strict=True
        ):
            if compiled.search(text):
                return True, pattern
        return False, ""

    def _parse_response(self, raw: str, fact_id: str) -> list[ExtractedEntity]:
        stripped = _FENCE_OPEN_RE.sub("", raw.strip())
        stripped = _FENCE_CLOSE_RE.sub("", stripped).strip()
        if not stripped:
            log.memory.warning(
                "[memory] entity_extractor.parse: empty response",
                extra={"_fields": {"fact_id": fact_id}},
            )
            return []
        try:
            return list(_ENTITY_LIST_ADAPTER.validate_json(stripped))
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            # B5 — every except logs at warning+
            log.memory.warning(
                "[memory] entity_extractor.parse: invalid JSON response",
                exc_info=exc,
                extra={
                    "_fields": {
                        "fact_id": fact_id,
                        "raw_len": len(raw),
                        "stripped_len": len(stripped),
                    }
                },
            )
            return []
