"""`pinned_skills` is retired — and the eleven stored manifests still carry it.

REPLACES `tests/owls/test_manifest_pinned_skills.py`, whose two tests asserted
only that the field existed and round-tripped. The field is gone (A05.3): zero
readers and zero writers anywhere in `src/`, never set by any of the eleven live
owls, and the skill-relevance tiering it belonged to was never finished — its
scoring was later REMOVED, which `pipeline/steps/assemble.py` records in its own
comment. A field that cannot do anything advertises a capability that does not
exist.

WHAT THIS FILE GUARDS IS THE DELETION'S ONE REAL HAZARD. `OwlAgentManifest` is
`extra="forbid"`, and ALL ELEVEN stored manifests carry `pinned_skills` as `[]`
(pydantic's `model_dump` writes every field). Removing the field without
forgetting the key would make every owl fail validation on the next boot, and
`OwlStore.list_all` SKIPS an unreadable row — so the platform would come up with
no owls at all, logging eleven errors nobody reads until somebody asks why the
assistant has no assistants.
"""

from __future__ import annotations

import pytest

from stackowl.owls.manifest import OwlAgentManifest


def _fields() -> dict[str, object]:
    return {
        "name": "secretary", "role": "r", "system_prompt": "p", "model_tier": "fast",
    }


@pytest.mark.tripwire
def test_the_retired_field_is_gone_from_the_model() -> None:
    assert "pinned_skills" not in OwlAgentManifest.model_fields


@pytest.mark.tripwire
def test_a_STORED_manifest_carrying_it_still_loads() -> None:
    """The shape on disk for all eleven owls today. If this fails, a boot brings
    the roster up empty."""
    m = OwlAgentManifest.model_validate({**_fields(), "pinned_skills": []})

    assert m.name == "secretary"
    assert not hasattr(m, "pinned_skills")


@pytest.mark.tripwire
def test_a_NON_retired_unknown_field_is_still_REFUSED() -> None:
    """The drop is scoped to the retired names on purpose. A blanket
    `extra="ignore"` would turn every typo in a manifest into a silent no-op,
    and `forbid` is what catches those today."""
    with pytest.raises(Exception, match="pinned_skils|extra_forbidden|Extra inputs"):
        OwlAgentManifest.model_validate({**_fields(), "pinned_skils": []})


@pytest.mark.tripwire
def test_the_key_is_shed_on_the_next_write_so_no_migration_is_needed() -> None:
    """Why no data was deleted: `upsert` rewrites `manifest_json` from the model,
    so a row loaded once and written back no longer carries the key."""
    m = OwlAgentManifest.model_validate({**_fields(), "pinned_skills": ["a"]})

    assert "pinned_skills" not in m.model_dump(mode="json")
