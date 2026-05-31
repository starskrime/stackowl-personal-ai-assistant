"""Plan B Task 0 — extracted facts must have a distinct source_type.

RC-A safety: raw conversation turns use source_type='conversation'; extracted
facts must use a different value so they are excluded from short-term history
(recent_conversation_turns filters on 'conversation') and cannot be re-mined
by a future conversation miner.
"""

from stackowl.memory.models import StagedFact
from stackowl.memory.fact_extractor import EXTRACTED_FACT_SOURCE_TYPE


def test_extracted_source_type_constant():
    assert EXTRACTED_FACT_SOURCE_TYPE == "conversation_fact"


def test_stagedfact_accepts_extracted_source_type():
    # Literal must allow the new value (else pydantic raises)
    f = StagedFact(content="x", source_type="conversation_fact", source_ref="s", confidence=0.9)
    assert f.source_type == "conversation_fact"


def test_extracted_type_distinct_from_raw_conversation():
    # The two must differ so recent_conversation_turns (filters 'conversation') excludes extracted facts
    assert EXTRACTED_FACT_SOURCE_TYPE != "conversation"
