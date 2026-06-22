"""Runtime process-topology layer: gateway/core split, drain, code watcher.

This package hosts the machinery that turns the in-process monolith into a
durable-gateway + restartable-core pair (gated by ``runtime.split_process``) and
the dev-convenience code watcher that exec-replaces the core on source changes
(gated by ``runtime.auto_restart``). The IPC transport it rides on lives in
``stackowl.ipc``.
"""
