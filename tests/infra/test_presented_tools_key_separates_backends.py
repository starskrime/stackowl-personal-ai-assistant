"""Two backends of one protocol must not share a memoized tool array.

`presented_tools.make_key` keyed on `(session_key, owl, protocol, window, hydrated)`.
No provider. So two DIFFERENT backends speaking the same protocol, inside one session,
collided: the first to build an array handed it to the second.

WHY THAT IS NOT MERELY A CACHE INEFFICIENCY. The array is BUDGETED — `to_provider_schema`
is passed `window` and `max_tools` precisely so "a weak/small-window model is not drowned
in tool schemas" (`execute.py:1306-1307`). And the `window` component cannot separate them
either: `_resolve_execute_window` (`execute.py:730`) returns `state.model_window`
unconditionally when set, and that is stamped ONCE by assemble and never re-stamped per
tier — while `llm_gateway.complete_with_tools` DOES call `build_tool_schemas(provider)`
again for each tier it climbs. So the escalation ladder rebuilds schemas per tier and
then keys them all identically.

MEASURED 2026-08-21, and stated as measured rather than as feared. In `cost_records`:
1,530 real traces used more than one provider and 254 used more than one MODEL, spanning
`qwen3.5:2b` to `qwen3.5:122b` to `neraai-v1-raw`. All five backends are `protocol:
openai`. So the 2b tier was served an array fitted to the 122b tier's window.

BUT IT CANNOT FIRE TODAY: three of the four configured backends are `enabled: false`, and
the most recent multi-model trace is 2026-07-20. This is DORMANT, not live — it re-arms
the moment a second backend is enabled. Fixed now rather than later because that is
exactly what D04.1 is about, and because the symptom (a small model offered too many
tools) is indistinguishable from "the small model is small", with both diagnostic lines
at DEBUG.
"""

from __future__ import annotations

from stackowl.infra import presented_tools


def _key(**over: object):
    base: dict[str, object] = {
        "session_key": "s1", "owl": "secretary", "provider": "ollama",
        "protocol": "openai", "window": 262_144, "hydrated": None,
    }
    base.update(over)
    return presented_tools.make_key(**base)  # type: ignore[arg-type]


class TestTheKeySeparatesBackends:
    def test_two_backends_on_one_protocol_do_not_collide(self) -> None:
        """THE DEFECT. Same session, same owl, same protocol, same window — and
        genuinely different models behind them."""
        assert _key(provider="ollama") != _key(provider="NeraAiRaw")

    def test_the_same_backend_still_hits(self) -> None:
        """The memo's whole purpose. D05.2 memoizes because `_fixed_cost` grows every
        turn, so a per-turn rebuild would shrink the array as the session proceeds and
        defeat the position-0 prompt-cache marker. A key that never hits would ship
        that regression back."""
        assert _key() == _key()

    def test_an_array_stored_for_one_backend_is_not_served_to_another(self) -> None:
        """Drives the memo itself, not just the key — a key that differs but a store
        that ignores the difference would still collide."""
        presented_tools.clear()
        presented_tools.put(_key(provider="weak"), [{"name": "only_one"}])

        assert presented_tools.get(_key(provider="strong")) is None
        assert presented_tools.get(_key(provider="weak")) == [{"name": "only_one"}]

    def test_every_other_component_still_separates(self) -> None:
        """Adding a component must not accidentally collapse an existing one."""
        assert _key(session_key="other") != _key()
        assert _key(owl="other") != _key()
        assert _key(protocol="anthropic") != _key()
        assert _key(window=8192) != _key()
        assert _key(hydrated={"a"}) != _key()

    def test_clear_owl_still_finds_the_owl(self) -> None:
        """`clear_owl` scans `k[1]`, so it depends on the owl's POSITION in the key.
        `provider` was inserted at index 2 to keep that true; if a later change moves
        it, a self-extending owl silently keeps its pre-edit toolset for the rest of
        the session — the exact failure clear_owl exists to prevent."""
        presented_tools.clear()
        presented_tools.put(_key(owl="secretary"), [{"name": "a"}])
        presented_tools.put(_key(owl="other"), [{"name": "b"}])

        presented_tools.clear_owl("secretary")

        assert presented_tools.get(_key(owl="secretary")) is None
        assert presented_tools.get(_key(owl="other")) == [{"name": "b"}]

    def test_hydrated_order_still_does_not_matter(self) -> None:
        """The property the key already had. A set has no stable iteration order, so
        two identical hydrated sets must hash the same or the memo never hits."""
        assert _key(hydrated={"a", "b"}) == _key(hydrated={"b", "a"})
