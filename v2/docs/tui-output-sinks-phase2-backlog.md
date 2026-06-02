# TUI Output Sinks — Phase 2 Backlog

`UIStateCoordinator` builds a Textual message for every EventBus output event and
hands it to `StackOwlApp.deliver()`, which routes each message to its target
widget by a direct handler call (see `_DELIVERY_ROUTES` in
`src/stackowl/tui/app.py`).

The message types below are *built by the coordinator* but currently have **no
UI rendering surface**, so `deliver()` logs a loud warning (`no UI sink wired
for message type`) and drops them. They are deferred — not lost — pending the
rendering surfaces noted below. The warning makes the gap honest: nothing is
silently swallowed.

| Message type | Source event | Why deferred (no rendering surface built) | When to revisit |
|---|---|---|---|
| `BudgetAlertMessage` | `budget_80pct_alert` | No budget/cost banner surface exists in the 5-zone layout yet | When a budget alert banner/strip is designed |
| `JobPausedMessage` | `job_paused` | No scheduler/job status surface exists in the TUI yet | When a job-status surface (e.g. footer badge) is built |
| `MemoryFactMessage` | `memory_fact_updated` | No memory-activity surface exists in the TUI yet | When a memory ticker/inspector surface is built |
| `EvolutionBadgeMessage` | `evolution_batch_complete` | No DNA/evolution badge surface exists in the TUI yet | When an evolution badge overlay is built |
| `ToastRequestMessage` | `toast_request` | No toast overlay surface exists in the TUI yet | When a toast/notification overlay is built |

## Reverse orphans (handler with no producer)

`ProviderChangedMessage` and `CostUpdatedMessage` already have `PipelineStrip`
handlers wired in `_DELIVERY_ROUTES`, but the coordinator has **no producer
event** that builds them yet — the reverse of the orphans above. They will route
and render correctly as soon as a coordinator event begins emitting them.
