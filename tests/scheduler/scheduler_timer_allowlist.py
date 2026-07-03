"""Explicit allowlist of persistent background poll/ping loops that
legitimately live outside src/stackowl/scheduler/JobScheduler.

Each entry is a conscious exception, not a default — see
test_no_dummy_schedulers.py for the check this feeds. Audited 2026-07-03
(docs/superpowers/specs/2026-07-03-scheduler-single-authority-design.md).
"""

INFRA_TIMER_ALLOWLIST: dict[str, str] = {
    "src/stackowl/service/watchdog.py": (
        "systemd sd_notify WATCHDOG=1 liveness ping — must keep running even "
        "if JobScheduler itself hangs, since detecting that IS its job."
    ),
    "src/stackowl/channels/telegram/adapter.py": (
        "Telegram long-poll/heartbeat loop — a channel-protocol requirement "
        "for message delivery, not business-domain scheduling."
    ),
    "src/stackowl/channels/whatsapp/adapter.py": (
        "WhatsApp inbound message poll loop — protocol requirement (no push "
        "webhook wired), not business-domain scheduling."
    ),
    "src/stackowl/tools/browser/sessions.py": (
        "Browser session idle-timeout/TTL cleanup loop — resource lifecycle "
        "management, not user-facing scheduled work."
    ),
    "src/stackowl/ipc/client.py": (
        "IPC reconnect retry-backoff loop — connection resilience, not a "
        "competing scheduler."
    ),
    "src/stackowl/tools/process/wait_tool.py": (
        "Bounded synchronous wait-for-subprocess tool — exits on process "
        "completion or timeout, never runs indefinitely."
    ),
    "src/stackowl/startup/orchestrator.py": (
        "Startup-phase-only retry/backoff loops during the one-shot boot "
        "sequence — not a persistent runtime scheduler."
    ),
}
