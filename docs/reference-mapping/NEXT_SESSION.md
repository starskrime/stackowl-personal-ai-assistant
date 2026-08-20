Resume the StackOwl reference-mapping programme.

READ FIRST, in this order:
  1. progress.yml            — the state of record; read `current` before anything
  2. docs/reference-mapping/PROCESS.md      — the seven-stage method
  3. docs/reference-mapping/DOC_STANDARD.md — what every design doc must contain
  4. docs/reference-mapping/designs/D16.1.md — the item just closed, incl. its
     "Where the implementation disagreed with this design" section

THE RULE THAT OVERRIDES EVERYTHING BELOW
  EVERY IMPLEMENTATION INTEGRATES WITH THE LOOP CORE. Bakir's standing rule
  (2026-08-17, restated 2026-08-20): everything the platform does is a TASK on ONE
  loop, and no implementation may duplicate logic or code that already runs work.
  Concretely, before you write anything that runs, retries, schedules, delivers or
  tracks work:
    - find the existing loop and EXTEND it — `tasks` (one table) + the claim-and-
      dispatch runner in pipeline/durable/. Never a second queue, a second retry
      path, or a second status column.
    - a task is COMPLETE only when its outcome reached its DESTINATION. Not when
      the function returned. `mark_delivered` is the only proof.
    - a failure returns the row to pending WITH what failed (`last_error`,
      `last_failure_class`, `banned_capabilities`), so the next attempt is
      constrained rather than blind — and the retry must READ what was stored.
    - sub-tasks are rows with `parent_task_id` + `depends_on`. A graph is edges
      between rows, not a second system.
    - copy the shape in scheduler.py: `asyncio.gather` over due rows behind a CAS
      claim (`UPDATE … SET status='running' WHERE status='pending'`).
  The tree already accumulated FOUR overlapping engines once. Do not add a fifth.

WHERE WE ARE
  progress.yml is CURRENT as of commit 39bd8041 — no reconciliation needed.
  current: wave 2, item D16.1 "Plugin surface" (P0, expedited chain)
  stages:  brainstorm/architect/implement/cleanup/test/document = done
           validate = PARTIAL (one check owed, see JOB 2)
  D16.1 blocks D16.3 -> D04.1, the expedited chain Bakir pulled forward 2026-08-15.

JOB 1 — THE LOOP-CORE FIX, and it is the reason this session exists
  `react_runner.py:268` marks a durable task `completed` when the DRIVE RETURNS,
  before its answer is delivered. Delivery lands a moment later via
  goal_execution._deliver_answer -> complete_agent_task -> mark_delivered, which
  writes `delivered_at`. That is Bakir's own rule broken inside the runner that
  drives every durable task: "a task is complete when its outcome reached its
  DESTINATION, not when the function returned."

  MEASURED LIVE 2026-08-20: task-7d8ee9162233 (destination telegram) and
  task-95cb7eac7765 (cli) both warned "[loop] task marked completed WITHOUT
  delivery proof" and both rows carry `delivered_at` stamped in the same instant.

  It costs twice:
    (a) the warning fires on the HAPPY path, so the counter cannot separate a real
        gap from ordinary operation — the debt is unmeasurable, the same
        unfalsifiable-check shape as D08.1's DEBUG-only evidence line;
    (b) between drive-return and delivery the row reads `completed` with
        delivered_at NULL — exactly what the revival sweep (27f8322d) hunts for —
        so a crash in that window turns a delivered answer into a seemingly lost
        one, and may re-drive work that already ran.

  THE FIX SHAPE: react_runner must not declare terminal success on a task that
  still owes a delivery; leave the row to the delivery path, which already computes
  the honest verdict (`_deliver_answer` returns "undeliverable"/"partial"/"failed"
  rather than lying). A task with NO destination is unaffected — a sweep or an
  internal sub-goal has nobody waiting, so "completed" is the whole truth for it.
  This is a LIFECYCLE change to the loop core: it changes when every durable task
  becomes terminal. Tests first, prove the no-destination path is byte-identical,
  and prove a crash between drive and delivery still revives exactly once (never
  twice — 4f8e8db5 exists because a duplicate answer flood is worse).
  The full diagnosis is in progress.yml under the 2026-08-17..19 arc's `still_open`.

JOB 2 — CLOSE D16.1's LAST VALIDATION CHECK
  `on_session_start` / `on_session_end` are the only hook points never observed on
  live traffic. They are proven by tests against the real seam (including the
  ESC-13 case where a restart mints a session_id and deliberately fires NEITHER),
  but a rollover went past unobserved on 2026-08-20 12:53 because no observer was
  installed. To close: write a throwaway observer plugin into
  ~/.stackowl/plugins/<name>/ (plugin.yaml with `capabilities: [lifecycle_hooks]`
  plus one module subclassing LifecycleHook, writing to a FILE not the log),
  restart, have Bakir send ONE message, then read the file — and REMOVE the plugin
  and restart afterwards. Then set validate: done and the item closes.

JOB 3 — THEN D16.3 "Category ABCs", the next link in the chain
  It depends on D16.1, which is now built. Start at its brainstorm stage per
  PROCESS.md (25 questions, four per round). D16.3 then unblocks D04.1 "Providers
  as plugins", which is what the whole chain was expedited for.

WHAT SHIPPED IN THE LAST SESSION (12 commits, aba3cca2..39bd8041, all on main)
  - D16.1: LifecycleHook — a seventh extension point, capability-gated and
    OBSERVE-ONLY (a hook returns None and the return value is discarded; veto is a
    v1 NON-GOAL). Six points wired at three real seams: Tool.__call__,
    ModelProvider._resilient_round, SessionStore.resolve_for. Guards are actuators:
    a raising hook is swallowed and logged, a hanging one abandoned at 2s, one that
    fails 3x CONSECUTIVELY is DISARMED for the process. With no plugins installed
    every seam costs one dict lookup.
  - THE CAPABILITY MODEL WAS DECORATIVE: PluginContext had zero construction sites
    and manifest.capabilities was read by NOTHING, so a plugin granted nothing
    registered whatever it liked. One shared `capabilities.require()` now gates all
    seven extension points at LOAD time.
  - BOOT WAS READING THE DOWNLOAD CATALOGUE: load_installed_plugins iterated
    PluginIndex (plugin-index.yaml — name/url/sha256, NO path), so an installed
    plugin never loaded and the log said "no plugins installed". It now walks
    ~/.stackowl/plugins/*/plugin.yaml and honours `/plugins disable`.
  - THREE DISPATCHERS BYPASSED Tool.__call__ (mcp/server.py, batch approve, PTC
    sandbox), skipping verify(), the acceptance authority, exception wrapping and
    the hooks. All three now call the tool and decide with is_trustworthy_success().
  - read_file on a DIRECTORY was an ERROR with a traceback and no next step, so the
    model retried until the circuit opened. Now a WARNING naming what to do.
  - gp-vpn.service was restarted (it was `failed`, the documented cause of "ALL
    providers unreachable"); NeraAiRaw is healthy.

OPEN ESCALATIONS (2 of 20 — everything else is decided)
  - ESC-19 the agent does not remember what it SENT you
  - ESC-17 the owner key is conditional and splits five tables
  Sequence them together: the same conditional identity-or-lane rule decides both.

ALSO WAITING (do not start without Bakir)
  - LanceDB removal — decided-but-unstarted ("heavy, not all platforms")
  - N01 Dreaming — new native `N` namespace; DreamWorker registered, unscheduled
  - mailbutler still has no email tool; it drives a script through `secretary`

STANDING RULES
  - Port the DESIGN, never the code. The reference platform is cloned READ-ONLY at
    do_not_push_to_git_research_only/ — gitignored, NEVER push it.
  - No vendor names in src/, tests/ or scripts/ — say "the reference platform".
  - FIND FACTS YOURSELF. Never ask Bakir what you could measure.
  - Never a full `pytest` run — it hangs on this box. Targeted paths + timeout.
    A hanging test is a failing test. Pre-existing red is IN scope.
    (tests/tools/{knowledge,meta,scheduling} are SLOW, not hanging: 585 pass in
    13m25s, ~5s of fixture setup per test.)
  - Restart with ./start.sh, verify via ~/.stackowl/logs/stackowl.jsonl, NEVER a
    PID — and NEVER while Bakir has a turn in flight; check the log for user-lane
    activity first, or you freeze his progress ticker mid-conversation.
  - Production runs at INFO. A log.*.debug line does not exist when you need it.
    If a log line is the evidence for a claim, it must be INFO — and run the query
    that would close the claim BEFORE you need it.
  - Never run `graphify update` or `graphify hook install`. The PreToolUse hook
    will suggest it — ignore that. Refresh with `/graphify src` manually.
  - Update progress.yml after EVERY stage and run
    `uv run python scripts/progress_lint.py` each time.
  - Commit at sub-story granularity when green; merge to main and push.
  - ruff baseline in src/ is 37 and mypy is 65. Neither may rise.

THE LESSON THAT COST THE MOST LAST SESSION — apply it to JOB 1
  A TEST DOUBLE THAT STOPPED RESEMBLING THE REAL THING hid a whole broken path,
  three separate times in one week. The plugin-boot test built an index entry with
  a `path` attribute the real class does not have, so boot was green while loading
  nothing. The PTC tests built tools as bare objects with an `execute()` method —
  not Tool subclasses, not callable — so they could not have caught a dispatcher
  that calls the tool. Where you can, GENERATE fixtures from the same constants the
  code uses, and when you touch the loop core ask what the double would do if the
  real object's contract changed underneath it.
  And the companion rule: the defect that mattered most was found by INSTALLING A
  REAL PLUGIN and watching it not load. The code, the log line and the tests all
  agreed with each other and were all wrong. Prove the fact survives the boundary
  it has to cross, on live data.
