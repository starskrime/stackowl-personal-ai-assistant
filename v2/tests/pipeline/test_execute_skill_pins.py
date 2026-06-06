import pytest

from stackowl.pipeline.steps.execute import _compute_presented_pins


class _Sk:
    def __init__(self, name, tool_names): self.name, self.tool_names = name, tuple(tool_names)


class _Store:
    def __init__(self, skills): self._s = skills
    async def get_many_by_name(self, names): return [s for s in self._s if s.name in names]


@pytest.mark.asyncio
async def test_pins_augmented_with_owned_skill_tools():
    store = _Store([_Sk("research_skill", ["deep_search"])])
    pins = await _compute_presented_pins(["base_tool"], ("research_skill",), store)
    assert set(pins) == {"base_tool", "deep_search"}


@pytest.mark.asyncio
async def test_no_owned_skills_returns_base_pins():
    pins = await _compute_presented_pins(["base_tool"], (), _Store([]))
    assert pins == ["base_tool"]


@pytest.mark.asyncio
async def test_none_store_returns_base_pins():
    pins = await _compute_presented_pins(["base_tool"], ("x",), None)
    assert pins == ["base_tool"]


@pytest.mark.asyncio
async def test_no_duplicate_pins():
    store = _Store([_Sk("s", ["base_tool", "extra"])])
    pins = await _compute_presented_pins(["base_tool"], ("s",), store)
    assert sorted(pins) == ["base_tool", "extra"]
