"""Integrations — the ADAPTER SURFACE, and four vendor modules that predate the rule.

**THIRD-PARTY PRODUCTS SHIP AS PLUGINS, NOT IN THIS TREE** (D16.4, adopted
2026-09-04 from the reference platform, whose stated reason is maintenance load
rather than quality). Observability backends, vendor SaaS connectors and analytics
belong in standalone plugin repos. The in-tree set is CLOSED.

THE DESIGN ALREADY SAID SO, in June. ``IntegrationRegistry.register()`` is
documented "open for extension: plugins can call register() at import time", and
``/connect`` tells the operator "No integrations registered. Install an
integration plugin first."

WHAT IS GENERIC AND STAYS: ``base.py`` (the ``IntegrationAdapter`` ABC),
``registry.py``, ``oauth_manager.py`` (vendor-neutral encrypted token storage),
``integration_assembler.py``, ``settings.py``.

WHAT PREDATES THE RULE, measured 2026-09-04: ``gmail.py``, ``gmail_settings.py``,
``google_calendar.py`` and ``google_oauth.py`` — four vendor-specific modules,
registered from three CORE sites (``cli/app.py``, ``commands/assembly.py``,
``startup/orchestrator.py``). This is live, not dormant: the Gmail OAuth token on
disk was refreshed 2026-09-02. Whether it moves out is ESC-133 — user-facing
capability with real credentials is not a decision this package makes.

ENFORCED by ``tests/plugins/test_the_core_tree_does_not_grow_a_vendor_connector.py``
(tripwire): the module set and the registrar set are both pinned with set
equality, so a fifth vendor module or a fourth core registrar fails the gate, and
so does a list that rots after the pair is finally moved out.
"""
