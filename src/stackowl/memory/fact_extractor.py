"""FactExtractor — extracts StagedFacts from conversation messages via LLM."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import FactExtractionParseError
from stackowl.infra.observability import log
from stackowl.memory.models import StagedFact
from stackowl.memory.trust import Trust, trust_for_source
from stackowl.providers.base import Message
from stackowl.tenancy.identity import IdentityResolver

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.embeddings.registry import EmbeddingRegistry
    from stackowl.events.bus import EventBus
    from stackowl.providers.base import ModelProvider


EXTRACTED_FACT_SOURCE_TYPE: Literal["conversation_fact"] = "conversation_fact"

_PROMPT_DIR = Path(__file__).parent / "prompts"
_TEMPLATE_NAME = "fact_extraction.j2"
_FENCE_OPEN_RE = re.compile(r"^\s*```(?:[A-Za-z0-9_-]+)?\s*\n?", re.UNICODE)
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$", re.UNICODE)


class ExtractedFactDraft(BaseModel):
    """LLM extraction output schema — validated via TypeAdapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    confidence: float = Field(ge=0.0, le=1.0)


_DRAFT_LIST_ADAPTER: TypeAdapter[list[ExtractedFactDraft]] = TypeAdapter(list[ExtractedFactDraft])


class FactExtractor:
    """Extracts :class:`StagedFact` instances from conversation messages.

    The pipeline:

    1. Render the language-neutral Jinja2 prompt against the conversation.
    2. Call the configured ``ModelProvider``.
    3. Parse the JSON response into :class:`ExtractedFactDraft` instances.
    4. Drop any fact whose content matches a configured sensitive category.
    5. Embed the remaining facts (best-effort — degrades to ``embedding=None``).
    """

    def __init__(
        self,
        provider: ModelProvider,
        embedding_registry: EmbeddingRegistry | None = None,
        event_bus: EventBus | None = None,
        sensitive_categories: list[str] | None = None,
        identity_resolver: IdentityResolver | None = None,
    ) -> None:
        # 1. ENTRY
        log.memory.debug(
            "[memory] fact_extractor.init: entry",
            extra={
                "_fields": {
                    "has_embeddings": embedding_registry is not None,
                    "has_event_bus": event_bus is not None,
                    "sensitive_count": len(sensitive_categories or []),
                    "has_identity_resolver": identity_resolver is not None,
                }
            },
        )
        self._provider = provider
        self._embeddings = embedding_registry
        self._event_bus = event_bus
        self._sensitive_categories = list(sensitive_categories or [])
        # When no resolver is supplied, default to an empty one so resolve(x)==x
        # — preserves byte-identical behaviour for unconfigured deployments.
        self._identity_resolver: IdentityResolver = identity_resolver or IdentityResolver({})
        self._sensitive_patterns = [
            re.compile(pattern, re.UNICODE | re.IGNORECASE)
            for pattern in self._sensitive_categories
        ]
        env = Environment(
            loader=FileSystemLoader(str(_PROMPT_DIR)),
            autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        self._template = env.get_template(_TEMPLATE_NAME)
        # 4. EXIT
        log.memory.debug("[memory] fact_extractor.init: exit")

    async def extract(
        self, conversation: list[Message], session_id: str
    ) -> list[StagedFact]:
        """Extract :class:`StagedFact` items from a conversation."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] fact_extractor.extract: entry",
            extra={"_fields": {"session_id": session_id, "n_messages": len(conversation)}},
        )
        TestModeGuard.assert_not_test_mode("fact_extractor.extract")

        # 2. DECISION — render prompt
        messages = self._build_prompt(conversation)
        log.memory.debug(
            "[memory] fact_extractor.extract: prompt built",
            extra={"_fields": {"session_id": session_id, "prompt_messages": len(messages)}},
        )

        # 3. STEP — provider call
        result = await self._provider.complete(messages, model="")
        log.memory.debug(
            "[memory] fact_extractor.extract: provider response received",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "response_len": len(result.content),
                    "provider": result.provider_name,
                }
            },
        )

        # 3. STEP — parse JSON response
        drafts = self._parse_response(result.content)
        log.memory.debug(
            "[memory] fact_extractor.extract: parsed drafts",
            extra={"_fields": {"session_id": session_id, "draft_count": len(drafts)}},
        )

        # 3. STEP — filter sensitive
        kept = self._filter_sensitive(drafts)
        log.memory.debug(
            "[memory] fact_extractor.extract: sensitive filter applied",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "kept": len(kept),
                    "dropped": len(drafts) - len(kept),
                }
            },
        )

        # 3. STEP — embed remaining facts
        embeddings = await self._embed_drafts(kept)

        # 2. DECISION — coarse batch-level trust: any tool-role message means external
        # data touched the conversation, so we taint the entire batch as untrusted.
        has_tool_role = any(getattr(m, "role", "") == "tool" for m in conversation)
        batch_trust: Trust = "untrusted" if has_tool_role else trust_for_source(EXTRACTED_FACT_SOURCE_TYPE)
        log.memory.debug(
            "[memory] fact_extractor.extract: batch trust determined",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "has_tool_role": has_tool_role,
                    "batch_trust": batch_trust,
                }
            },
        )

        # Re-key the source_ref through the identity resolver so that facts from
        # different channels belonging to the same user reinforce each other.
        # When no resolver is configured (or handles are unmapped) resolve() returns
        # the session_id unchanged — byte-identical to today's behaviour.
        identity_ref = self._identity_resolver.resolve(session_id)
        log.memory.debug(
            "[memory] fact_extractor.extract: identity ref resolved",
            extra={
                "_fields": {
                    "session_id": session_id,
                    "identity_ref": identity_ref,
                    "cross_channel": identity_ref != session_id,
                }
            },
        )

        facts: list[StagedFact] = []
        for draft, embedding in zip(kept, embeddings, strict=True):
            facts.append(
                StagedFact(
                    content=draft.content,
                    source_type=EXTRACTED_FACT_SOURCE_TYPE,
                    source_ref=identity_ref,
                    confidence=draft.confidence,
                    trust=batch_trust,
                    embedding=embedding,
                    embedding_model=self._embeddings.get().model_name
                    if self._embeddings is not None and embedding is not None
                    else None,
                )
            )

        # 4. EXIT
        log.memory.info(
            "[memory] fact_extractor.extract: exit",
            extra={"_fields": {"session_id": session_id, "fact_count": len(facts)}},
        )
        if self._event_bus is not None:
            self._event_bus.emit(
                "memory.facts_extracted",
                {"session_id": session_id, "count": len(facts)},
            )
        return facts

    # ------------------------------------------------------------------ helpers

    def _build_prompt(self, conversation: list[Message]) -> list[Message]:
        conversation_text = "\n".join(
            f"{msg.role}: {msg.content}" for msg in conversation
        )
        rendered = self._template.render(conversation_text=conversation_text)
        return [Message(role="user", content=rendered)]

    def _parse_response(self, raw: str) -> list[ExtractedFactDraft]:
        stripped = _FENCE_OPEN_RE.sub("", raw.strip())
        stripped = _FENCE_CLOSE_RE.sub("", stripped).strip()
        try:
            return list(_DRAFT_LIST_ADAPTER.validate_json(stripped))
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            # B5 — every except block logs at warning+
            log.memory.warning(
                "[memory] fact_extractor.parse: invalid JSON response",
                exc_info=exc,
                extra={"_fields": {"raw_len": len(raw), "stripped_len": len(stripped)}},
            )
            raise FactExtractionParseError(
                reason=str(exc), raw_response_excerpt=raw
            ) from exc

    def _is_sensitive(self, content: str) -> tuple[bool, str]:
        """Return ``(is_sensitive, matching_pattern)`` — never logs raw content."""
        for pattern, compiled in zip(
            self._sensitive_categories, self._sensitive_patterns, strict=True
        ):
            if compiled.search(content):
                return True, pattern
        return False, ""

    def _filter_sensitive(
        self, drafts: list[ExtractedFactDraft]
    ) -> list[ExtractedFactDraft]:
        kept: list[ExtractedFactDraft] = []
        for draft in drafts:
            is_sens, pattern = self._is_sensitive(draft.content)
            if is_sens:
                fact_hash = hashlib.sha256(draft.content.encode("utf-8")).hexdigest()[:8]
                log.memory.info(
                    "[memory] fact_extractor: sensitive fact dropped",
                    extra={
                        "_fields": {
                            "category_match": pattern,
                            "fact_hash": fact_hash,
                        }
                    },
                )
                continue
            kept.append(draft)
        return kept

    async def _embed_drafts(
        self, drafts: list[ExtractedFactDraft]
    ) -> list[list[float] | None]:
        if not drafts:
            return []
        if self._embeddings is None:
            log.memory.warning(
                "[memory] fact_extractor.embed: no embedding registry — facts will have embedding=None",
                extra={"_fields": {"draft_count": len(drafts)}},
            )
            return [None] * len(drafts)
        try:
            provider = self._embeddings.get()
            vectors = await provider.embed([d.content for d in drafts])
        except Exception as exc:
            # B5 — log and degrade gracefully
            log.memory.warning(
                "[memory] fact_extractor.embed: embedding failed — proceeding without",
                exc_info=exc,
                extra={"_fields": {"draft_count": len(drafts)}},
            )
            return [None] * len(drafts)
        return list(vectors)
