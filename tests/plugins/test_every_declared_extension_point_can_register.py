"""D16.1 implement — a declared extension point must actually have somewhere to go.

FOUND 2026-08-17 while starting the implement stage. ``_ABC_NAMES`` declares SIX
extension points; ``LocalPluginLoader.__init__`` builds ``self._registries`` with
FIVE. ``MemoryProvider`` — the whole point of D08.2 slice C — has no slot, so
``_register_classes`` does::

    registry = self._registries.get("MemoryProvider")   # -> None
    if registry is None:
        continue                                        # SILENTLY

No error. No warning. A memory-provider plugin would load, its class would be
discovered by ``issubclass``, and it would register nowhere — the platform
reporting a successful install of a plugin that does nothing.

THE GENERAL TEST IS THE POINT. Asserting only "MemoryProvider now has a slot"
fixes today's instance and lets the SEVENTH extension point repeat it — and a
seventh is already designed (``LifecycleHook``, in designs/D16.1.md). So the test
below is written against ``_ABC_NAMES`` itself: every declared point must have a
slot, whatever the table grows to. It fails the moment someone adds a row to one
table and forgets the other.

WHY A SILENT SKIP IS THE WRONG DEGRADE. A missing registry is a WIRING error, not
a runtime condition — it cannot be fixed by the user and will never resolve on its
own. ``continue`` hides it forever; the loader already knows how to fail loudly
(``PluginValidationError`` names the plugin and the class), and this is exactly a
case for it.
"""

from __future__ import annotations

from stackowl.plugins.local_loader import _ABC_NAMES, LocalPluginLoader


class TestEveryDeclaredPointHasSomewhereToGo:
    def test_no_extension_point_is_declared_without_a_registry_slot(self) -> None:
        """The general invariant, not the single instance."""
        loader = LocalPluginLoader()

        declared = set(_ABC_NAMES)
        slots = set(loader._registries)  # noqa: SLF001

        missing = declared - slots
        assert not missing, (
            f"declared in _ABC_NAMES but unregistrable: {sorted(missing)} — a plugin "
            f"defining one of these would be discovered and then silently dropped"
        )

    def test_no_registry_slot_exists_without_a_declared_point(self) -> None:
        """The other direction. A slot with no ABC is dead weight that reads as
        support for something the loader cannot actually discover."""
        loader = LocalPluginLoader()

        orphan = set(loader._registries) - set(_ABC_NAMES)  # noqa: SLF001

        assert not orphan, f"registry slots with no matching extension point: {sorted(orphan)}"

    def test_memory_provider_specifically(self) -> None:
        """The instance that prompted this — D08.2 slice C's extension point."""
        loader = LocalPluginLoader()

        assert "MemoryProvider" in loader._registries  # noqa: SLF001


class TestAnUnwiredSlotIsNotSilent:
    def test_a_declared_point_with_a_None_registry_is_reported(self) -> None:
        """Constructing the loader without a given registry is legitimate — most
        call sites pass only what they need. What must NOT happen is a plugin
        silently registering nowhere because of it.

        A None slot means "this deployment did not wire that extension point",
        which is a fact worth surfacing, not swallowing.
        """
        loader = LocalPluginLoader()  # nothing wired at all

        unwired = [k for k, v in loader._registries.items() if v is None]  # noqa: SLF001

        # Every point is unwired here — the assertion is that we can SEE that,
        # rather than the loader pretending the surface is complete.
        assert set(unwired) == set(_ABC_NAMES), (
            "the loader should be able to report which extension points are unwired"
        )
