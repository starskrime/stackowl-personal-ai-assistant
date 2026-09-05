"""The plugin surface — and the one rule that keeps it a surface.

**PLUGINS MUST NOT TOUCH CORE** (D16.2, adopted 2026-09-04 from the reference
platform, whose wording this follows). A plugin may not require an edit to a core
entrypoint — ``cli/app.py``, ``startup/orchestrator.py``, ``runtime/``,
``pipeline/``. If a plugin needs something the framework does not expose, you
**widen the generic surface**; you never special-case the plugin in core. There,
adopting it meant deleting 95 lines of hardcoded plugin argparse from ``main.py``.

WHY IT IS WORTH A RULE. The decay is one-way and quiet: the first special case is
always cheaper than widening the surface, and each one makes the next easier to
justify, until the plugin system is a list of names core knows about.

THE SURFACE is ``_ABC_NAMES`` in ``local_loader.py`` — eight extension points,
each handed a real registry at boot: Tool, JobHandler, SlashCommand,
ChannelAdapter, OwlSource, MemoryProvider, LifecycleHook, PromptContributor.
Adding a ninth means an entry in BOTH tables, in the same edit.

WHAT ENFORCES IT, because a rule with no enforcement is a rule nobody follows:

* ``tests/plugins/test_every_declared_extension_point_can_register.py`` —
  ``set(_ABC_NAMES) == set(loader._registries)``, both directions.
* ``test_a_contributor_reaches_a_real_prompt.py::TestEveryDeclaredSlotIsActuallyWired``
  — reads the REAL construction site, because the check above passes even when a
  slot's VALUE is None. That is the D08.2 defect wearing a different hat: an
  extension point declared and not wired, failing SILENTLY.
* ``test_core_reaches_the_plugin_surface_and_nothing_else.py`` — a tripwire over
  all 841 modules in ``src/stackowl``: exactly seven outside this package may
  import it, and they import the hook seams or plugin management, never a
  plugin. Set equality in both directions, so the list cannot rot either way.

WHAT IS NOT ENFORCED, stated rather than implied. "Core must not special-case a
plugin" cannot be screened directly while **zero plugins are installed** — the
decay looks like ``if plugin_name == "foo"`` and there is no name to look for, so
any screen would be a zero over a zero denominator. The import-direction tripwire
above is the closest proxy with a real denominator. When the first plugin is
installed, the direct screen becomes possible and should be written then.
"""
