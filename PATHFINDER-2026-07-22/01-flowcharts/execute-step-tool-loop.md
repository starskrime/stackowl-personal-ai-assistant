# Execute Step — Tool-Use ReAct Loop + Plain-Stream Path

execute.py is 2600+ lines, the largest file in the codebase — this is the core "message goes to the provider and comes back" logic, with two branches from one top-level decision.

## Mermaid

```mermaid
flowchart TD
    A["run() entry — execute.py:2589"] --> B["resolve provider choice +\njoin feedback task (concurrent)\nexecute.py:2615-2618"]
    B --> C{"state.feedback_handled?\nexecute.py:2628"}
    C -->|yes| Z1["return state — reaction WAS the reply"]
    C -->|no| D["_maybe_clarify()\nexecute.py:2642"]
    D --> E{"clarify triggered?"}
    E -->|yes| Z2["return clarify question"]
    E -->|no| F{"_use_tools =\nintent_class != 'conversational'\nAND tool_registry present AND non-empty\nexecute.py:2649-2653"}

    F -->|True| G["_run_with_tools()\nexecute.py:1002"]
    F -->|False| H["build messages + resolve manifest\nexecute.py:2682-2688"]

    subgraph TOOLLOOP["TOOL-LOOP BRANCH"]
      direction TB
      G --> G1["build_tool_schemas(): DNA/profile/pins/\nwindow-budgeted presentation\nexecute.py:1077"]
      G1 --> G2{"choice.pinned?\nexecute.py:1917"}
      G2 -->|pinned| G3["provider.complete_with_tools()\nexecute.py:1918"]
      G2 -->|non-pinned| G4["LLMGateway.complete_with_tools()\nexecute.py:1937"]
      G4 --> G5["tier loop: for tier in tier_span(floor,ceiling)\nllm_gateway.py:272"]
      G5 --> G3b["provider.complete_with_tools(can_escalate=idx<last)\nllm_gateway.py:295"]

      G3 --> I["ReAct iteration:\nfor _iter_idx in range(max_iterations)\nopenai_provider.py:575"]
      G3b --> I
      I --> I1["model round: chat.completions.create()\nopenai_provider.py:591-601"]
      I1 --> I2{"native tool_calls\nreturned?"}
      I2 -->|no| I3["parse_react_action(content)\n_react.py:184, openai_provider.py:659"]
      I3 --> I4{"action parsed?"}
      I4 -->|yes| I5["dispatch via _dispatch()\nexecute.py:1210 → OBSERVATION appended\nopenai_provider.py:664-703"]
      I2 -->|yes| I8["dispatch each tool_call via _dispatch()\nopenai_provider.py:819-844"]
      I5 --> L["LoopGuard.observe(name,args)\n_react.py:302, openai_provider.py:682"]
      I8 --> L2["LoopGuard.observe per call\nopenai_provider.py:845"]
      L --> L1{"guard.tripped()?\n_react.py:316 break_at=4"}
      L2 --> L1
      L1 -->|yes| M["break loop → wrap-up\nopenai_provider.py:683-702 / 863-869"]
      L1 -->|no| I

      I4 -->|no: draft final answer| I6{"looks_like_tool_call(content)?\n_react.py:222, openai_provider.py:754"}
      I6 -->|yes: leaked call, retries left| I6a["re-prompt FORMAT_FIX_DIRECTIVE\n(max 2) — openai_provider.py:755-763"]
      I6a --> I
      I6 -->|yes: exhausted, can_escalate| J1["return ESCALATE_SENTINEL\nopenai_provider.py:770"]
      I6 -->|yes: exhausted, at ceiling| J2["honest floor: synthesize_from_calls()\nopenai_provider.py:776"]
      I6 -->|no: real answer| I7["persistence judge _enforce()\nopenai_provider.py:779"]
      I7 -->|give-up + can_escalate| J1b["return ESCALATE_SENTINEL\nopenai_provider.py:792"]
      I7 -->|give-up, at ceiling| I7a["nudge directive, continue"]
      I7a --> I
      I7 -->|delivered| K["NATIVE FINISH\nreturn content, all_calls\nopenai_provider.py:799"]

      M --> N{"can_escalate?\nopenai_provider.py:879"}
      N -->|yes| J1c["return ESCALATE_SENTINEL\nopenai_provider.py:885"]
      N -->|no| O["MAX-ITER WRAP-UP:\n1 final no-tools call +\nconsequential-giveup veto\nopenai_provider.py:891-950"]

      J1 --> G5
      J1b --> G5
      J1c --> G5
      G5 -->|ESCALATE / cascadable fault\nstep up a tier| G5
      G5 -->|ceiling reached or delivered| P["result → _run_with_tools returns\nPipelineState"]
      K --> P
      O --> P
      J2 --> P
    end

    subgraph PLAINSTREAM["PLAIN-STREAM BRANCH"]
      direction TB
      H --> H1["_is_tool_free_turn = intent_class in TOOL_FREE_CLASSES\nexecute.py:2687"]
      H1 --> H2["_open_stream(provider, manifest, messages,\nmax_tokens=4096 if tool-free, disable_thinking)\nexecute.py:2689-2692"]
      H2 --> H3["OwlResourceGuard.stream()\nowls/guards.py:130"]
      H3 --> H4["1. concurrency slot try_acquire()\nguards.py:173 → OwlConcurrencyError"]
      H4 --> H5["2. per-chunk asyncio.wait_for(__anext__(), remaining)\nguards.py:196-214 → OwlTimeoutError"]
      H5 --> H6["3. whitespace word-count vs manifest.max_tokens\nguards.py:219-234 → soft cutoff, NO exception"]
      H6 --> H7["execute.py consumes chunks:\ndegenerate-repeat counter\nexecute.py:2701-2716"]
      H7 --> H8{"repeat_counts[stripped]\n>= 20 (min len 3)?"}
      H8 -->|yes| H9["clear chunks, raise OwlTimeoutError(0.0)\nexecute.py:2716 — FLOORED"]
      H8 -->|no| H10{"chunk_index==0 or\nall-empty content?\nexecute.py:2726"}
      H10 -->|yes| H9b["raise OwlTimeoutError\nexecute.py:2743"]
      H10 -->|no| H11{"looks_like_tool_call(full_text)?\nexecute.py:2753"}
      H11 -->|yes: leak| H9
      H11 -->|no| H12["NORMAL EXIT:\nreturn state.responses += chunks\nexecute.py:2821"]
    end

    P --> Q["_snapshot_consequential(out)\nexecute.py:2680"]
    Q --> R["return PipelineState"]
    H9 --> R2["except OwlTimeoutError →\nattach floored error to state\nexecute.py:2766-2777"]
    H9b --> R2
    H12 --> R
```

## Loop-detection/guard overlap analysis

Three distinct mechanisms in this feature, each covering a genuinely different failure mode (not redundant):
1. **`LoopGuard`** (`providers/_react.py:275`, `break_at=4`) — trips on the tool-loop branch when the SAME `(name, args)` tuple repeats — catches a model stuck re-calling the same tool.
2. **Degenerate-repetition counter** (`execute.py:2701-2716`, threshold=20, min-len=3) — trips on the plain-stream branch when the SAME short text unit repeats in the raw token stream — catches a model stuck emitting the same token/phrase (e.g. empty `<tool_code></tool_code>` pairs), a stream-level failure mode the tool-loop's `LoopGuard` cannot see (no tool calls involved at all).
3. **`OwlResourceGuard`'s word-count cutoff** (`guards.py:219-234`) — a soft client-side ceiling on total output length, unrelated to repetition — stops consuming once `manifest.max_tokens` (whitespace words) is crossed, no exception raised.

These three guards protect against three different pathologies (tool-call thrashing, stream-token thrashing, raw length) at two different layers (tool-loop vs plain-stream) — legitimate specialization, not duplication.

## Confidence note + known gaps

High confidence on: the top-level `_use_tools` condition, the full plain-stream guard stack, the openai_provider ReAct iteration mechanics and all four ESCALATE trigger sites, `LoopGuard`'s semantics, and the guard-overlap analysis — all backed by full reads.

Gaps: `anthropic_provider.py:183`'s `complete_with_tools` was located but not read in full — assumed to mirror the openai loop structure via the shared `ModelProvider` interface, not verified byte-for-byte (may implement leak-detection differently for Anthropic's native tool-call format). `execute.py:1290-1780` and `2100-2286` (~700 lines, the recovery-ladder tool-substitution logic, deeper `BudgetGovernor`/`ConsequentialActionGate` wiring) were only grepped, not read — there may be additional resource-budget guards (e.g. cost ceilings) not captured here. `llm_gateway.py:357+` and `1-227` (non-tools `.complete()` path) were grepped only. `_dispatch`'s full body past line 1290 (actual tool invocation, consent-prompt flow) not traced.
