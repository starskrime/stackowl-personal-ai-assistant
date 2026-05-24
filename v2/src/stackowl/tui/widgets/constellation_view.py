"""ConstellationView — left-panel owl roster with layout-tier adaptation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.reactive import reactive
from textual.widget import Widget

from stackowl.infra.observability import log
from stackowl.tui.layout import LayoutTier, compute_tier
from stackowl.tui.messages import LayoutTierChangedMessage
from stackowl.tui.widgets.constellation_helpers import (
    OwlCardModel,
    pick_dominant_trait,
    render_collapsed,
    render_full,
)

if TYPE_CHECKING:
    from stackowl.owls.manifest import OwlAgentManifest
    from stackowl.owls.registry import OwlRegistry

_SECRETARY = "secretary"
_UNKNOWN_LAST_ACTIVE = "-"


class OwlCard(Widget):
    """Card showing one owl in the :class:`ConstellationView`.

    Two render modes: ``render_collapsed`` for compact layouts, ``render_full``
    for standard / expanded layouts.  The widget itself stays passive — the
    parent view drives mode selection by querying which method to call.
    """

    DEFAULT_CSS = """
    OwlCard {
        height: auto;
        padding: 0 1;
        color: $color-text-primary;
        background: $color-bg-elevated;
    }
    """

    def __init__(self, owl_name: str, is_secretary: bool = False) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] owl_card.__init__: entry",
            extra={"_fields": {"owl_name": owl_name, "is_secretary": is_secretary}},
        )
        self._owl_name: str = owl_name
        self._is_secretary: bool = is_secretary
        self._model: OwlCardModel = OwlCardModel(
            owl_name=owl_name,
            is_secretary=is_secretary,
            tier="powerful",
            dominant_trait_name="neutral",
            dominant_trait_value=0.5,
            last_active=_UNKNOWN_LAST_ACTIVE,
        )

    @property
    def owl_name(self) -> str:
        return self._owl_name

    @property
    def is_secretary(self) -> bool:
        return self._is_secretary

    def update_from_manifest(self, manifest: OwlAgentManifest) -> None:
        """Refresh the cached card model from the supplied manifest."""
        log.tui.debug(
            "[tui] owl_card.update_from_manifest: entry",
            extra={"_fields": {"owl_name": manifest.name}},
        )
        dna_traits = {
            "challenge": manifest.dna.challenge_level,
            "verbosity": manifest.dna.verbosity,
            "curiosity": manifest.dna.curiosity,
            "formality": manifest.dna.formality,
            "creativity": manifest.dna.creativity,
            "precision": manifest.dna.precision,
        }
        name, value = pick_dominant_trait(dna_traits)
        self._model = OwlCardModel(
            owl_name=manifest.name,
            is_secretary=manifest.name == _SECRETARY,
            tier=manifest.model_tier,
            dominant_trait_name=name,
            dominant_trait_value=value,
            last_active=_UNKNOWN_LAST_ACTIVE,
        )

    def render_collapsed(self) -> str:
        """Icon strip — 2-char avatar + health dot."""
        return render_collapsed(self._owl_name)

    def render_full(self) -> str:
        """Full card with name, tier, dominant DNA trait, last_active."""
        return render_full(self._model)


class ConstellationView(Widget):
    """Left-panel owl roster widget.

    Subscribes to its own :class:`Resize` events and recomputes the active
    :class:`LayoutTier`.  The reactive ``current_tier`` attribute drives the
    visibility / render-mode of every contained :class:`OwlCard`.
    """

    DEFAULT_CSS = """
    ConstellationView {
        layer: base;
        width: 24;
        background: $color-bg-elevated;
        border-right: solid $color-border;
    }
    """

    current_tier: reactive[LayoutTier] = reactive(LayoutTier.STANDARD)

    def __init__(self) -> None:
        super().__init__()
        log.tui.debug(
            "[tui] constellation_view.__init__: entry",
            extra={"_fields": {}},
        )
        self._cards: dict[str, OwlCard] = {}
        self._owl_registry: OwlRegistry | None = None

    def set_registry(self, registry: OwlRegistry) -> None:
        """Bind to an :class:`OwlRegistry` and rebuild the card set."""
        log.tui.debug(
            "[tui] constellation_view.set_registry: entry",
            extra={"_fields": {"owls": len(registry.list())}},
        )
        self._owl_registry = registry
        self._refresh_cards()
        log.tui.debug(
            "[tui] constellation_view.set_registry: exit",
            extra={"_fields": {"cards": len(self._cards)}},
        )

    def _refresh_cards(self) -> None:
        """Rebuild the card dict from the bound registry."""
        if self._owl_registry is None:
            log.tui.warning(
                "[tui] constellation_view._refresh_cards: no registry bound",
                extra={"_fields": {}},
            )
            return
        manifests = self._owl_registry.list()
        new_cards: dict[str, OwlCard] = {}
        for manifest in manifests:
            existing = self._cards.get(manifest.name)
            card = existing or OwlCard(
                owl_name=manifest.name,
                is_secretary=manifest.name == _SECRETARY,
            )
            card.update_from_manifest(manifest)
            new_cards[manifest.name] = card
        self._cards = new_cards
        log.tui.debug(
            "[tui] constellation_view._refresh_cards: exit",
            extra={"_fields": {"count": len(self._cards)}},
        )

    def card_for(self, owl_name: str) -> OwlCard | None:
        """Return the :class:`OwlCard` for ``owl_name`` if present."""
        return self._cards.get(owl_name)

    def cards(self) -> list[OwlCard]:
        """Return cards in stable (registry) order."""
        return list(self._cards.values())

    def on_resize(self, event: events.Resize) -> None:
        log.tui.debug(
            "[tui] constellation_view.on_resize: entry",
            extra={"_fields": {"cols": event.size.width}},
        )
        tier = compute_tier(event.size.width)
        self.current_tier = tier

    def watch_current_tier(self, tier: LayoutTier) -> None:
        """Apply layout changes when the tier reactive updates."""
        log.tui.debug(
            "[tui] constellation_view.watch_current_tier: decision",
            extra={"_fields": {"tier": tier.value}},
        )
        if tier == LayoutTier.MINIMAL:
            self.display = False
        else:
            self.display = True
        self.post_message(LayoutTierChangedMessage(tier=tier.value))

    def render_for_tier(self, owl_name: str) -> str:
        """Render an owl card according to the active tier (test surface)."""
        card = self._cards.get(owl_name)
        if card is None:
            log.tui.warning(
                "[tui] constellation_view.render_for_tier: unknown card",
                extra={"_fields": {"owl_name": owl_name}},
            )
            return ""
        if self.current_tier == LayoutTier.COMPACT:
            return card.render_collapsed()
        return card.render_full()
