"""Channel self-heal: the actuator has to be findable when the sweep looks.

THE BUG, found 2026-08-15 by chasing a warning that fired on every single boot:
"[scheduler] assembly: telegram healer setup failed — health detection via
ChannelLivenessContributor only". Telegram had DETECTION and no ACTUATOR. The
ADR-6 loop was open for the primary channel, and had been since it was written.

TWO INDEPENDENT DEFECTS, either of which alone was fatal:

1. ORDERING. The wiring called ``ChannelRegistry.get("telegram")`` and tested the
   result for ``None`` — but ``get()`` never returns ``None``, it RAISES
   ChannelNotFoundError. So the success branch and its ``else`` were both dead
   code and every boot landed in the ``except``. It missed every time because
   scheduler assembly runs BEFORE the adapter starts: measured in the live log,
   assembly exits at 03:31:19 and Telegram starts at 03:31:45.

2. KEY MISMATCH. Even with the ordering fixed the heal could not fire. The sweep
   does ``self._healers.get(status.name)`` — a plain exact-string match. The
   healer would have been keyed ``"telegram"`` (the adapter's contributor_name),
   while the statuses that actually report the channel unhealthy are
   ``"telegram_receive"`` and ``"telegram_canary_send"``, because
   ChannelLivenessContributor names itself ``f"{channel}_{kind}"``.

THE FIX RESOLVES AT LOOKUP TIME, which removes the ordering dependency rather
than moving it. An eager snapshot is correct only if it is taken after every
adapter has started, and "call these in the right order" is exactly the kind of
constraint that regressed here silently.
"""

from __future__ import annotations

import pytest

from stackowl.health.channel_healers import ChannelHealers

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    """Shaped like a channel adapter that is HealableResource."""

    def __init__(self, name: str = "telegram") -> None:
        self._name = name
        self.healed = 0

    @property
    def channel_name(self) -> str:
        return self._name

    @property
    def contributor_name(self) -> str:
        return self._name

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str | None:
        return None

    async def ensure_available(self) -> None:
        self.healed += 1


class _NotHealable:
    """A registered adapter with no ensure_available — must never be returned."""

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def contributor_name(self) -> str:
        return "telegram"


@pytest.fixture(autouse=True)
def _clean_registry():  # type: ignore[no-untyped-def]
    from stackowl.channels.registry import ChannelRegistry

    ChannelRegistry.instance().reset()
    yield
    ChannelRegistry.instance().reset()


def _register(adapter: object, name: str = "telegram") -> None:
    """The registry keys on the adapter's own ``channel_name``."""
    from stackowl.channels.registry import ChannelRegistry

    ChannelRegistry.instance().register(adapter)  # type: ignore[arg-type]


class TestItResolvesAfterTheAdapterStarts:
    async def test_an_adapter_registered_LATER_is_still_found(self) -> None:
        """Defect 1. The healer map is built during scheduler assembly, before the
        channel adapters start. An eager snapshot is empty at that moment."""
        healers = ChannelHealers({})
        assert healers.get("telegram_receive") is None  # nothing started yet

        adapter = _FakeAdapter()
        _register(adapter)

        assert healers.get("telegram_receive") is adapter

    async def test_a_missing_channel_returns_the_default_rather_than_raising(
        self,
    ) -> None:
        """ChannelRegistry.get RAISES for an unknown name; the sweep calls .get()
        on this map inside its heal loop and must never see an exception."""
        assert ChannelHealers({}).get("nosuchchannel_receive") is None

    async def test_a_registered_but_UNHEALABLE_adapter_is_not_offered(self) -> None:
        """Returning it would make the sweep call ensure_available on something
        that has none — an AttributeError inside the heal loop."""
        _register(_NotHealable())

        assert ChannelHealers({}).get("telegram_receive") is None


class TestTheStatusNamesTheSweepActuallySees:
    """Defect 2. These are the exact strings the aggregator produces."""

    @pytest.mark.parametrize(
        "status_name",
        ["telegram_receive", "telegram_canary_send", "telegram"],
    )
    async def test_each_maps_to_the_telegram_adapter(self, status_name: str) -> None:
        adapter = _FakeAdapter()
        _register(adapter)

        assert ChannelHealers({}).get(status_name) is adapter, status_name

    async def test_the_canary_maps_to_the_REAL_channel(self) -> None:
        """"telegram_canary" is not a registered channel — it is the send-path
        signal FOR telegram. A canary failure means the send path is dead, which
        is precisely when recycling the adapter is the right response."""
        adapter = _FakeAdapter()
        _register(adapter)

        assert ChannelHealers({}).get("telegram_canary_send") is adapter

    async def test_another_channel_is_not_confused_for_telegram(self) -> None:
        tg, discord = _FakeAdapter("telegram"), _FakeAdapter("discord")
        _register(tg)
        _register(discord, "discord")

        healers = ChannelHealers({})
        assert healers.get("discord_receive") is discord
        assert healers.get("telegram_receive") is tg


class TestStaticEntriesStillWork:
    async def test_a_static_healer_wins_and_is_returned(self) -> None:
        """The db and provider healers are passed in directly and must be
        unaffected — this map wraps them, it does not replace them."""
        db = _FakeAdapter("db")

        assert ChannelHealers({"db": db}).get("db") is db

    async def test_a_static_entry_is_preferred_over_a_registry_lookup(self) -> None:
        explicit, registered = _FakeAdapter("telegram"), _FakeAdapter("telegram")
        _register(registered)

        assert ChannelHealers({"telegram_receive": explicit}).get("telegram_receive") is explicit

    async def test_it_is_falsy_when_nothing_could_be_healed(self) -> None:
        """health_sweep guards with `if not self._healers`. Always-truthy would
        make it enter the heal loop with nothing wired — a behaviour change
        disguised as a bug fix."""
        assert not ChannelHealers({})

    async def test_it_is_truthy_when_only_a_CHANNEL_could_be_healed(self) -> None:
        """The subtle half. Truthiness must mean "something could be healed", not
        "the static dict is non-empty" — otherwise a deployment whose only
        healable subsystem is a channel never enters the loop, which is the same
        class of bug this class exists to fix."""
        _register(_FakeAdapter())

        assert ChannelHealers({})

    async def test_a_static_entry_alone_is_truthy(self) -> None:
        assert ChannelHealers({"db": _FakeAdapter("db")})
