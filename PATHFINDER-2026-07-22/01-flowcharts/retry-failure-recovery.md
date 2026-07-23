# Retry / Failure-Recovery — Application + Durable Layers

This is the most directly relevant feature to "no clear retries." Three genuinely separate mechanisms exist, converging only at one shared finalize call.

## Mermaid

```mermaid
flowchart TD
    subgraph Intake["Gateway intake — orchestrator.py:2442"]
        MSG[Inbound message] --> ML_INS["message_ledger.insert_pending<br/>orchestrator.py:2461<br/>status=pending"]
        ML_INS --> ROUTE{routed}
        ROUTE -->|overflow/STOP/STEER<br/>never reaches persist_turn| ML_TERM["mark_failed/completed/absorbed<br/>orchestrator.py:2406-2421"]
        ROUTE -->|dispatched| PIPE[Pipeline run<br/>backends/shared.py]
    end

    PIPE --> GATES["surface_*_floor/gate steps<br/>shared.py:104-133"]
    GATES --> PT["persist_turn(state)<br/>turn_persist.py:74<br/>shared.py:140 — SHARED finalize, every run"]

    PT --> FLOORED{"_turn_floored?<br/>turn_persist.py:36-56"}

    FLOORED -->|No, clean turn| ML_OK["message_ledger.mark_completed<br/>turn_persist.py:165"]
    FLOORED -->|"Yes (giveup/critical/overclaim/grounding)"| ML_FAIL["message_ledger.mark_failed(reason)<br/>turn_persist.py:163"]

    FLOORED -->|Yes AND NOT retry_replay| RQ_DEDUP{"existing pending row<br/>for this session?<br/>get_latest_pending_for_session<br/>turn_persist.py:126"}
    RQ_DEDUP -->|no| RQ_INS["retry_queue.insert_pending<br/>attempt_count=0, due now<br/>turn_persist.py:142"]
    RQ_DEDUP -->|"yes (2nd floor while pending)"| RQ_SUP["retry_queue.supersede(existing)<br/>repoint trace_id/goal, reset attempts<br/>b65058d1 — turn_persist.py:135"]
    FLOORED -->|"Yes AND retry_replay=True<br/>(this run IS a retry's own replay)"| SKIP_RQ["skip retry_queue insert —<br/>row's own attempt_count/_MAX_ATTEMPTS<br/>already tracks this floor<br/>state.py:113-121"]

    RQ_INS --> DUE[(retry_queue row: pending)]
    RQ_SUP --> DUE

    subgraph Sweep["RetrySweepHandler — 1min cron, retry_sweep.py:20"]
        DUE --> GET_DUE["retry_store.get_due()<br/>next_retry_at <= now"]
        GET_DUE --> ACT["RetryActuator.attempt_retry(row)<br/>retry_actuator.py:152"]
        ACT --> AUG["_augment_goal — steer away from<br/>banned_capabilities<br/>retry_actuator.py:269"]
        AUG --> REPLAY["backend.run(synthetic PipelineState<br/>trace_id=retry-uuid, retry_replay=True,<br/>defer_delivery=True)<br/>retry_actuator.py:163-181"]
        REPLAY -.->|"loops back — this run\nALSO calls persist_turn"| PT
    end

    REPLAY --> R_FLOORED{"still floored?<br/>is_floor / budget_capped /<br/>overclaim_blocked<br/>retry_actuator.py:205-209"}
    R_FLOORED -->|No — success| DELIVER["_deliver_success:<br/>edit_message → send_text fallback<br/>retry_actuator.py:296-319"]
    DELIVER --> MARK_C["retry_queue.mark_completed<br/>retry_actuator.py:231"]
    DELIVER -->|delivery itself raises<br/>e.g. Telegram RetryAfter| RESCHED["retry_queue.reschedule<br/>honor flood-control cooldown<br/>retry_actuator.py:244-253"]
    RESCHED --> DUE

    R_FLOORED -->|Yes| HANDLE_FAIL["_handle_failure →<br/>retry_queue.mark_attempt_failed<br/>ban newly_failed_capability,<br/>exponential backoff (1,2,4..cap 10m)<br/>retry_queue_store.py:486"]
    HANDLE_FAIL --> CAPPED{attempt_count >= 3?}
    CAPPED -->|No| DUE
    CAPPED -->|Yes| GIVEUP["status=failed<br/>_notify_gave_up: 'Still couldn't...'<br/>retry_actuator.py:345-362"]

    subgraph Durable["Durable-task crash recovery — pipeline/durable/recovery.py"]
        BOOT["Process boot<br/>orchestrator.py:3543-3558<br/>role != gateway"] --> DT_SCAN["list status IN (running, recovering)<br/>BOTH are orphans — prior process is dead<br/>recovery.py:166-179"]
        LIVE["TaskLivenessSweepHandler<br/>recurring job, task_liveness_sweep.py:77<br/>stale running root >600s"] --> DT_ONE
        DT_SCAN --> DT_ONE["reclaim_one: CAS claim<br/>running/recovering -> recovering<br/>store.claim_for_recovery"]
        DT_ONE --> DT_RECON["reconstruct PipelineState from<br/>ReAct checkpoint (or fresh if none)<br/>recovery.py:397-471"]
        DT_RECON --> DT_DRIVE["background task:<br/>DurableTaskRunner.resume()<br/>drives full pipeline again"]
        DT_DRIVE -.->|"resume also flows through\nshared.py finalize"| PT
    end

    subgraph MsgRecover["Message-ledger crash recovery — recovery.py:506"]
        BOOT2["Process boot<br/>orchestrator.py:3565-3579<br/>role != gateway, AFTER durable recovery"] --> ML_PEND["message_ledger.get_pending()<br/>every pending row is an orphan<br/>(no separate claim state needed)"]
        ML_PEND --> ML_REDRIVE["background task:<br/>backend.run(same trace_id)<br/>reply_target = stored chat_id"]
        ML_REDRIVE -.->|"redrive flows through\nshared.py finalize, SAME trace_id"| PT
    end

    classDef store fill:#2d3748,stroke:#718096,color:#fff
    class DUE,ML_INS,ML_OK,ML_FAIL,MARK_C,HANDLE_FAIL store
```

## Verdict: three genuinely separate mechanisms, not accidental duplication — but converging on one shared finalize

1. **App-level delivery retry** (`retry_queue_store.py` + `RetryActuator` + `RetrySweepHandler`): a floored turn (past the honesty gates) gets ONE row per session, retried on a 1-minute sweep, exponential backoff, capped at 3 attempts, delivers via Telegram edit/send.
2. **Durable-task crash recovery** (`durable/recovery.py:94`): fires ONLY at process boot (plus a liveness sweep for stale `running` roots >600s) — recovers mid-flight ReAct checkpoints that were orphaned by a process crash, not by an honesty-gate floor.
3. **Message-ledger crash recovery** (`durable/recovery.py:506`): fires only at boot, redrives any `pending` message_ledger row under the SAME trace_id — a different orphan concept again (no durable-task identity).

**Why not merged:** `RetryActuator`'s replay uses a synthetic `trace_id` that never touches `message_ledger`. The two crash-recovery paths key off entirely different tables (`durable_tasks` has no `trace_id`; `message_ledger` has no durable-task identity) and only fire at boot. All three converge only at the single shared `persist_turn` finalize call (`backends/shared.py:140`) — that convergence is intentional (one place owns floor-detection and terminal bookkeeping), not the mechanisms being the same thing wearing different names.

## Confidence note + known gaps

High confidence — control flow and schema backed by direct reads, including the negative claim ("retry replay never touches message_ledger") verified via call-site comparison.

Flagged gaps:
- `retry_queue_store.py`'s own docstring documents KNOWN, UNFIXED cross-instance races (two sweep workers polling `get_due` concurrently; no `trace_id` uniqueness) tracked in `deferred-work.md` against migration 0082. Single-row races ARE closed via `DbPool.transaction`; the two-worker race is not.
- `retry_queue` rows are Telegram-only by convention (`channel="telegram"` default) — whether Slack/Discord/WhatsApp floors get a retry row at all, or silently mis-tag as telegram, was not verified.
- `delivery_gate.py`'s `_attempts_for_state`/`is_consequential_giveup_now`/`_critical_failure_classes` (the actual source of "what counts as floored") were not read in full — treat that file as load-bearing for this whole map.
- Test coverage for the `b65058d1` supersede-dedup path and sticky-cache eviction was not verified (implementation only).
