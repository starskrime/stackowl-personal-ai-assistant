# 01: Agentic OS and the JARVIS Interface

**Research date:** 2026-09-12
**Scope:** research only. No code was changed. This report feeds the design of the web app that replaces the mock dashboard.
**Method:** web search and source reading (industry, academic, open source, film and game design press). Every claim links to its source.
**Confidence legend:** **[High]** means a primary source (paper, official docs, vendor announcement, standard). **[Medium]** means reputable secondary press or several agreeing secondary sources. **[Low]** means one blog, vendor marketing, a benchmark site or an anecdote. Paragraphs marked *Synthesis* are the analyst's own reasoning from the cited sources, not a claim any source makes.

---

## Executive summary

1. **"Agentic OS" means at least four different things in 2026.** The research definition is a kernel that schedules LLM calls, manages context and memory, mediates tool access and enforces access control (AIOS, MemGPT, the 2026 "AOS" paper). OS vendors mean an agent layer bolted onto a consumer OS (Windows Agent Workspace plus MCP, Android 17 AppFunctions, iOS 27 App Intents). Workflow vendors mean an orchestration product with guardrails and human-in-the-loop controls. Systems researchers mean agents that tune the OS itself (SchedCP). The layers they share are **scheduler, context/memory hierarchy, tool/capability registry, policy/permissions, observability/audit**. The newest agreed layer, added by Microsoft, Google and the AOS paper, is **human visibility and takeover**. Beyond that the term is heavily marketed: Microsoft's "agentic OS" post drew a public backlash, and "agent-washing" is now a named practice. StackOwl already has most kernel-like layers. What it lacks, and what earns the "OS" label, is a **shell that shows and governs those layers**. That shell is the dashboard.

2. **Agent control surfaces have converged on a few patterns that work:**
   - "needs you" as the top-level state (Claude Code Agent View, Codex app)
   - an inbox for interrupts, sorted by priority (LangChain Agent Inbox: notify, question, review)
   - live narration with **pause, stop and take over** (ChatGPT agent, Manus, Android 17 live view)
   - approvals that can be conditional (AI SDK `needsApproval`)
   - per-model cost panels
   - timelines that "zoom like a map" and replayable sessions (Langfuse, AgentOps, Temporal)

   What fails:
   - review and supervision burden grows with parallelism (the Cursor 3 critique)
   - admin panels made of forms and 5-second polling (Hermes dashboard)
   - approvals that live in a different surface from the monitoring
   - node-and-edge graphs, which are exactly the "diagram" the owner rejected

3. **Movie UIs feel alive because they are made for a two-second read, not for use.**
   - **Who made them:** Perception (Iron Man 2 onward), Cantina Creative (Iron Man and Avengers HUDs), Jayse Hansen (Avengers HUD), Territory Studio (Guardians of the Galaxy, Blade Runner 2049, The Martian), GMUNK (Oblivion), the motion team on The Expanse.
   - **Why they feel alive:** every element has a job, and designers ground the work in real references (military, NASA, flight). Information sits in the periphery and escalates to the centre when needed. One graphic system (grid, palette, type) runs throughout. There is depth and diegesis, and sound that carries information.
   - **Why they fail as software:** they are post-produced to match an actor, and heroes need no learning. Mid-air gestures cause "gorilla arm". Christopher Noessel's analysis argues the JARVIS HUD itself is a **placebo interface**: 87% of its elements move without being asked, while the AI does the real work.
   - **How real products borrowed the feel:** they put the spectacle into short *moments* (Territory's 3–4 second car-entry sequences), minimal watch faces, the Siri edge glow and Gemini's motion that tracks cognitive load. They kept the working surface quiet (the Airbus "dark cockpit") and hard-backed critical controls (SpaceX Dragon's Chromium touch UI still has physical emergency buttons).

4. **"Not a diagram" has good precedents.**
   - **The precedents:** Gource, code_swarm ("organic information visualization"), Logstalgia, Netflix Vizceral ("intuition engineering" with particle flows), earth.nullschool (particles advected by real forecast data), the GitHub globe (real pull requests) and Peep (network state as a sound ecology).
   - **When a living visualisation stays truthful:** every moving thing has a real referent, the encoding matches the concept (Tversky's congruence principle), and sampling is declared. Motion is reserved for *change*, because motion is the strongest peripheral attention cue (Bartram et al.). Staleness is visible, and every glyph drills down to the underlying record.
   - **The single biggest honesty risk:** the client-side animation loop keeps "breathing" after the data stream has silently died, which is common on iOS.

5. **Voice presence is now a component category.** LiveKit Agents UI, ElevenLabs UI and the Pipecat Voice UI Kit are all open source. They drive a visual from two inputs, the agent state (connecting, listening, thinking, speaking, failed) and live audio amplitude.
   - **Numbers:** 2026 practice targets 200–400 ms turn gaps and under 150 ms barge-in [Medium/Low]. Full-duplex open models exist (Moshi, about 200 ms, CC-BY).
   - **Self-hosted stacks on a Jetson Orin run today:** Whisper, Piper or Kokoro, and LiveKit or Pipecat [Medium].
   - **Keeping voice and screen in sync:** one server-side state machine plus word-level TTS timestamps.

6. **The web platform can carry this in 2026, with caveats.**
   - **Where WebGPU ships by default:** Chrome/Edge desktop, Chrome Android 12+ (Qualcomm/ARM GPUs), Safari 26 on iOS/macOS, and Firefox on Windows and macOS.
   - **Where it does not:** Firefox Android and Linux (Nightly only), and Chrome Linux outside specific GPUs. WebGL 2 covers the rest, and Three.js `WebGPURenderer` falls back to it automatically.
   - **PWA:** iOS 26 opens every home-screen site as a web app. Web push works for home-screen apps.
   - **Battery:** browsers pause `requestAnimationFrame` in hidden tabs, and iOS Low Power Mode caps it at 30 fps. Phones thermally throttle sustained GPU load within minutes. An "always animated" UI therefore needs a frame budget, render-on-demand and adaptive quality (GitHub's globe drops quality below 55.5 fps).
   - **Accessibility:** `prefers-reduced-motion` and WCAG 2.2.2 (pause auto-playing motion longer than 5 s) are mandatory. About 35% of US adults over 40 show some vestibular dysfunction.
   - **Transport:** SSE is fine over HTTP/2; use WebSocket or WebTransport when you need bidirectional traffic; voice goes over WebRTC. On mobile, heartbeat-based reconnect is non-negotiable.

---

## 1. What "agentic OS" means in 2026

### 1.1 The four families of definition

**A. Research: the LLM as the kernel.**
- **"LLM as OS, Agents as Apps" (Rutgers, Dec 2023)** maps the LLM to the kernel, the context window to memory, external storage to files, tools to devices and libraries, prompts to the UI, and agents to applications. [High] ([arXiv 2312.03815](https://arxiv.org/html/2312.03815v2))
- **AIOS (Mei et al., COLM 2025)** implements this as a kernel with six modules: **scheduler, context manager, memory manager, storage manager, tool manager, access manager**. It sits between agent applications and model/tool providers, schedules with FIFO and round-robin, uses a K-LRU memory policy, and reports "up to 2.1x faster execution". [High] ([arXiv 2403.16971](https://arxiv.org/abs/2403.16971), [PDF](https://arxiv.org/pdf/2403.16971))
- **The Cerebrum SDK** adds a four-layer agent SDK (LLM, memory, storage, tools), an Agent Hub, and web and terminal UIs. [High] ([GitHub agiresearch/Cerebrum](https://github.com/agiresearch/Cerebrum))
- **MemGPT / Letta, "Towards LLMs as Operating Systems" (2023)** contributes *virtual context management*: main context as RAM, recall and archival storage as disk, memory functions as page-in/page-out, and interrupts for control flow. [High] ([arXiv 2310.08560](https://arxiv.org/abs/2310.08560))
- **Karpathy's "LLM OS" framing** (LLM as CPU, context window as RAM, retrieval as paging, tools as peripherals) became the shared vocabulary and gave rise to "context engineering". [Medium] ([MindStudio explainer](https://www.mindstudio.ai/blog/software-3-0-explained-karpathy-context-window-ram-model-weights-cpu), [frenxt summary](https://www.frenxt.com/cables/claude-code/karpathy-02-llm-os))
- **"Agent Operating Systems (AOS)" (Sharma and Shah, June 2026)** proposes an *agentic control plane* integrated into, and eventually beyond, traditional OSes. Its five responsibilities are **schedulers; context and memory management; tool and capability registries; policy and trust enforcement; observability and audit**. It explicitly does "not replace operating systems wholesale". [High for content; the paper is new and uncited] ([arXiv 2606.01508](https://arxiv.org/abs/2606.01508))

**B. OS vendors: an agent layer inside a consumer OS.**
- **Microsoft Windows** has several pieces:
  - An **Agent Workspace**, "a separate, contained space in Windows where you can grant agents access to your apps and files".
  - A separate **agent account** per agent.
  - **Agent connectors**, which are MCP servers registered in an On-Device Registry.
  - Three stated security principles. **Non-repudiation**: "All actions of an agent are observable and distinguishable from those taken by a user". **Least privilege**. **Visible activity**, with tamper-evident audit logs.
  - Per-agent file-access settings: "Allow Always / Ask every time / Never allow".

  [High] ([Microsoft Support](https://support.microsoft.com/en-us/windows/ai/ai-features/experimental-agentic-features), [InfoWorld](https://www.infoworld.com/article/4100474/the-first-building-blocks-of-an-agentic-windows-os.html)). At Build 2026 Microsoft positioned Windows as a platform for building and running agents. [Medium] ([Visual Studio Magazine](https://visualstudiomagazine.com/articles/2026/06/02/at-build-2026-microsoft-sets-up-windows-as-an-os-for-ai-agents.aspx))
- **Google Android ("The Intelligent OS", Feb 2026)** offers **AppFunctions**, self-describing functions apps expose to agents, and **UI automation**, which drives existing apps with no developer work. User safeguards are **live view / notifications**, **takeover at any point**, and **alerts before sensitive tasks** such as purchases. [High] ([Android Developers Blog](https://android-developers.googleblog.com/2026/02/the-intelligent-os-making-ai-agents.html)) Android 17's Gemini Intelligence ships first on Pixel 10 and Galaxy S26. [Medium] ([Lycamobile summary](https://www.lycamobile.it/blog/en/android-show-2026/))
- **Apple iOS 27 (WWDC 2026)** makes App Intents the *only* way Siri reaches into apps. It adds streaming progress for long-running actions, multi-turn clarifying follow-ups, and on-screen awareness, so that "the third one" resolves to what is visible. [Medium] ([Dracode](https://dracode.dev/blog/2026-06-08-09-wwdc-2026-siri-2-app-intents/), [NowSecure](https://www.nowsecure.com/blog/2026/08/05/what-appsec-teams-need-to-know-about-app-intents-siri-ai-and-the-new-ios-27-attack-surface/))

**C. Agent platforms calling themselves an "OS": an orchestration and control layer.**
- Make: an agentic OS "coordinates AI agents, tools, and data". Slack: "an operating layer for AI that coordinates autonomous agents, connects them to your data and apps, and keeps humans in control of outcomes". The capabilities they cite are orchestration, memory, guardrails, human-in-the-loop controls and observability. [Low: vendor blogs] ([Make](https://www.make.com/en/blog/agentic-operating-system), [Slack](https://slack.com/blog/productivity/what-is-an-agentic-os))
- The open-source personal-agent wave (OpenClaw: first released as Clawdbot in Nov 2025, renamed Jan 2026, reportedly more than 250k GitHub stars; Nous Research's Hermes Agent) ships a runtime plus scheduler, skills, memory and channels. A cottage industry of "Mission Control" dashboards has grown around them. [Medium] ([oneclaw](https://www.oneclaw.net/blog/openclaw-ai-agent-self-hosted-github), [Hermes docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard))
- One landscape blog claims "Agent Operating System" went "from research paper to product category between January and June 2026". [Low] ([CortexPrism](https://cortexprism.io/blog/open-source-ai-agent-os-2026-landscape))

**D. Agents that operate the OS itself.** "Towards Agentic OS" (SchedCP, 2025) uses LLM agents to synthesise eBPF Linux scheduler policies. It reports up to 1.79x performance and 13x lower cost than naive approaches. Here "agentic OS" means a *self-optimising* OS, not an agent host. [High] ([arXiv 2509.01245](https://arxiv.org/abs/2509.01245))

### 1.2 Layer comparison

| Layer | AIOS (research) | AOS paper 2026 | Windows | Android 17 | iOS 27 | Orchestration vendors / OpenClaw-style runtimes |
|---|---|---|---|---|---|---|
| Kernel / scheduler | Yes (FIFO, RR over LLM calls) | Yes | Background agent sessions | Task execution | Streaming long actions | Task queues, cron |
| Context / memory | Context manager, K-LRU memory | Yes | No (app-level) | No (app-level) | On-screen awareness | Vector memory, session stores |
| Storage | Storage manager (plus semantic FS, LSFS) | Part of memory | Files via connectors | App data via AppFunctions | App entities | Varies |
| Tool access | Tool manager | Capability registry | MCP connectors plus On-Device Registry | AppFunctions, UI automation | App Intents | Skills, MCP, integrations |
| Agent lifecycle | SDK plus Agent Hub | Implied | Agent accounts | Not explicit | Not explicit | Create, pause, delete agents |
| Permissions / policy | Access manager | Policy and trust enforcement | Least privilege, per-agent Allow/Ask/Never | Sensitive-task alerts | OS permission model | Approval queues (varies) |
| Observability / audit | Not central | **First-class** | Tamper-evident logs, non-repudiation | Live view | Progress streaming | Logs, cost dashboards |
| UI shell / human control | Web and terminal UI | Operator comprehension | Workspace you can watch and pause | **Takeover** | Conversational Siri | Dashboards, inboxes |

### 1.3 Where definitions agree, and where it is marketing

- **Agreement [High]:** (1) a mediating layer between agents and resources, (2) a memory hierarchy with paging-like movement, (3) a registry of tools and capabilities, (4) policy and permission enforcement, and (5), newest, observability, audit and **human takeover**. Microsoft, Google and the AOS paper all treat "the human can see it and stop it" as a structural requirement, not a UI nicety.
- **Disagreement:** whether the LLM *is* the kernel (Rutgers/AIOS) or a *tenant* governed by a control plane (AOS, Windows); and whether "OS" means hosting agents (A to C) or agents running the machine (D).
- **Marketing [Medium]:** Windows president Pavan Davuluri's Nov 2025 post that "Windows is evolving into an agentic OS" drew more than 700k views and a backlash ("nobody wants this"). Replies were locked, and he later acknowledged the focus on reliability and performance ([Windows Central](https://www.windowscentral.com/microsoft/windows-11/windows-president-confirms-os-will-become-ai-agentic-generates-push-back-online), [The Register](https://www.theregister.com/2025/11/17/windows_agentic_os_feedback/), [Tom's Hardware](https://www.tomshardware.com/software/windows/top-microsoft-execs-boast-about-windows-evolving-into-an-agentic-os-provokes-furious-backlash)). Commentators say the term "means everything and nothing" ([Data Science Dojo](https://datasciencedojo.com/blog/agentic-os-architecture/)). "Agent-washing" is the practice of rebranding scripted automations as agents ([PROS](https://pros.com/learn/blog/agent-washing-spot-hype-separate-buzzwords-from-real-agentic-ai/), [US News/AP](https://www.usnews.com/news/us/articles/2025-11-18/what-does-agentic-ai-mean-techs-newest-buzzword-is-a-mix-of-marketing-fluff-and-real-promise)).

*Synthesis for StackOwl.* By the research and AOS definitions, StackOwl already has the kernel-like layers:
- **Scheduler:** the scheduler and task loop.
- **Context / memory hierarchy:** memory.
- **Tool / capability registry:** tools and skills.
- **Agent lifecycle:** owls.
- **Policy / permissions:** consent and authorisation.
- **Self-healing:** goes beyond most definitions.

The layer it does not have as a first-class artefact is the **operator shell**: a surface where every layer is *visible, attributable and interruptible*. The public backlash suggests the "OS" claim is earned by control and transparency, not by the label. The dashboard is therefore not decoration on top of the OS. It is the missing OS layer.

---

## 2. How agent platforms present control to humans

### 2.1 Survey

| Product / project | What the user sees | What the user can control | Live activity / approvals / cost / failure | Source |
|---|---|---|---|---|
| **Claude Code Agent View** (research preview, May 2026) | A terminal list of all local sessions: last response, timestamp, **whether the agent needs you** | Reply inline without attaching; Enter to jump in; Space opens a **peek panel** with recent output and any blocking question | The "needs you" flag is the primary signal | [claudefa.st](https://claudefa.st/blog/guide/agents/agent-view), [pasqualepillitteri](https://pasqualepillitteri.it/en/news/2384/claude-code-agent-view-cli-dashboard-sessions-2026) [Medium] |
| **OpenAI Codex app** (macOS; Windows since Mar 2026) | Parallel agent threads grouped by project; sub-agents with nicknames | Switch threads; approval policy per repo; `spawn_agents_on_csv` fan-out with **progress and ETA** | Child-thread approval prompts visible from the parent | [OpenAI](https://openai.com/index/introducing-the-codex-app/), [IntuitionLabs](https://intuitionlabs.ai/articles/openai-codex-app-ai-coding-agents) [High/Medium] |
| **Cursor 3 Agents Window** (Apr 2026) | Sidebar of every agent session (local, worktree, cloud, SSH); tabs side by side or in a grid | Launch many agents in parallel; Design Mode to point at UI elements | Per-agent status; review of outputs | [Cursor changelog](https://cursor.com/changelog/3-0), [agentpatterns.ai](https://www.agentpatterns.ai/tools/cursor/agents-window/) [High] |
| **ChatGPT agent** | **On-screen narration** of what it is doing; a visual virtual browser | **Pause, stop, take over** the browser at any time; confirmations before important actions | Watch mode, takeover mode, required confirmations | [OpenAI](https://openai.com/index/introducing-chatgpt-agent/), [Help Center](https://help.openai.com/en/articles/11752874-chatgpt-agent) [High] |
| **Manus** | "Manus's Computer" live view, step by step | **Take Over** prompt on CAPTCHA or MFA; stop by closing | Handoff at verification walls | [Manus docs](https://manus.im/docs/features/cloud-browser) [High] |
| **Android 17 / Gemini** | Live view and notifications of task progress | Take over manually at any point | Alert before sensitive tasks | [Android Dev Blog](https://android-developers.googleblog.com/2026/02/the-intelligent-os-making-ai-agents.html) [High] |
| **Windows Agent Workspace** | Separate workspace you can observe | Per-agent Allow Always / Ask / Never; pause by closing | Tamper-evident activity logs | [Microsoft Support](https://support.microsoft.com/en-us/windows/ai/ai-features/experimental-agentic-features) [High] |
| **Hermes Agent dashboard** (StackOwl's reference project) | 15+ pages: Status, Chat (embedded TUI over WebSocket/PTY), Config (150+ fields), API Keys, Sessions, Logs, Analytics, Cron, Profiles, Skills, MCP, Webhooks, Pairing, Channels, System | Edit config, keys, cron, skills, channels; start and stop the gateway; themes and plugin slots | Status **auto-refreshes every 5 s**; per-model token and cost analytics; **no approval UI in the dashboard** (approvals stay in TUI or CLI) | [Hermes docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard), [extending](https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard) [High] |
| **OpenClaw "Mission Control"** (4 community variants) | Live status, gateway health, cron, CPU/memory, Kanban, terminal, vector memory search; per-model cost; org hierarchies | Approval workflows, audit trails, risk-classified approval queue, **emergency stop**, dry run | Cost dashboards; approval queues. Complaints: local-only, heavy enterprise setup, feature overload, a hosted variant shut down on May 30, 2026 | [BetterClaw comparison](https://www.betterclaw.io/blog/openclaw-mission-control), [builderz-labs](https://mc.builderz.dev/), [MeisnerDan](https://github.com/MeisnerDan/mission-control) [Medium] |
| **LangChain Agent Inbox** | An inbox of open agent interrupts, **sorted by priority, not chronology** | Three human-in-the-loop patterns: **notify** (FYI), **question** (unblock), **review** (approve or edit an action) | Modelled on email and support-ticket inboxes | [LangChain blog](https://www.langchain.com/blog/introducing-ambient-agents), [GitHub](https://github.com/langchain-ai/agent-inbox) [High] |
| **Letta ADE** | **Context window viewer**: system instructions, tool schemas, memory blocks, summaries, message buffer | Edit core memory blocks live | Shows exactly what the model receives | [Letta docs](https://docs.letta.com/guides/ade/context-window-viewer/) [High] |
| **Langfuse** | Agent graph (aggregated or expanded, beta since Jul 2026); **timeline that fits any trace on one screen and "zooms like a map"** | Drill into spans | Colour carries observation type | [Langfuse agent graphs](https://langfuse.com/docs/observability/features/agent-graphs), [changelog](https://langfuse.com/changelog) [High] |
| **AgentOps** | Session list plus a waterfall of LLM calls, tools and errors | **Session replay / time travel** | Cost and latency per step | [GitHub](https://github.com/agentops-ai/agentops), [ADK docs](https://adk.dev/integrations/agentops/) [Medium] |
| **Temporal Web UI** | Event-history timeline; related events grouped into one span; green or red by outcome | Filter pending or failed; **pause the live event stream to investigate** | Live updates | [Temporal docs](https://docs.temporal.io/web-ui), [blog](https://temporal.io/blog/lets-visualize-a-workflow) [High] |
| **Approval primitives** (AI SDK, OpenAI Agents SDK) | Pending approvals as UI parts | `needsApproval: true` or a **conditional function** (for example only payments above $1000); partial resolution; cryptographically bound approvals | The model receives the denial and adapts | [AI SDK](https://ai-sdk.dev/docs/agents/tool-approvals), [OpenAI Agents JS](https://openai.github.io/openai-agents-js/guides/human-in-the-loop/) [High] |

### 2.2 What works

- **Attention-first status.** The most-used signal is "this agent needs you" (Agent View), not "this agent is running". Priority-sorted interrupt inboxes (Agent Inbox) beat chat scrollback for multiple concurrent agents. [Medium]
- **Peek without entering.** Agent View's peek panel and Codex's visible child approvals let the operator stay at fleet level. [Medium]
- **Watch, pause and take over as one continuum.** ChatGPT agent, Manus, Android and Windows all converge on this. It is the consumer-grade form of "human control". [High]
- **Graduated approval.** Allow-always / ask / never (Windows) and conditional `needsApproval` (AI SDK) avoid approval fatigue. [High]
- **Ground-truth inspectors.** Letta's context window viewer and Langfuse and Temporal timelines let the user verify what really happened. [High]
- **Pausable live streams.** Temporal lets you freeze the incoming event stream to investigate without losing it. [High]

### 2.3 What does not work

- **Supervision debt.** Parallel agents move the cost from writing to reviewing: "You spend the time you saved writing code on reviewing agent output". The multi-agent overhead is counter-productive for deep single-problem work. [Medium] ([dev.to on Cursor 3](https://dev.to/onepizzateam/cursor-3-shipped-parallel-agents-and-the-community-cant-agree-on-whether-thats-good-1p3n))
- **Tab and pane sprawl.** The pre-Agent-View workflow of "four terminal tabs, a tmux grid, and a mental list" is the problem those products were built to solve. [Medium] ([claudefa.st](https://claudefa.st/blog/guide/agents/agent-view))
- **Config-centric admin panels.** Hermes is broad but form-heavy, polls every 5 s, and keeps approvals out of the dashboard. It is a settings app, not a bridge. [High on the facts; the judgement is *Synthesis*]
- **Feature overload versus setup weight.** The four Mission Control variants cover the full range: too thin (local-only), too heavy (1–2 h Docker set-up), too broad (overwhelming), or hosted and gone. [Medium]
- **Graphs as the primary view.** Node-and-edge agent graphs (Langfuse) answer "what is this agent's shape" well, but they are exactly the flowchart the owner does not want as the main surface. *Synthesis:* keep graphs as a forensic drill-down, not as the bridge.

---

## 3. FUI (fictional user interfaces): JARVIS and its descendants

### 3.1 Who designed what

- **Perception** (New York; creative director John LePore). Iron Man 2 was its first major film, starting from a mocked-up "glass phone". It went on to design Stark's gadgets, the transparent coffee table and household screens, and later Marvel films including Black Panther and Endgame. JARVIS was treated as "a visual representation of Stark's imagination", "as beautiful as it was functional" with "impressionistic and artistic flair". The studio spends "at least half of our time working with real technology clients". [Medium/High] ([Engadget](https://www.engadget.com/2015-12-14-perception-ui-design.html), [Perception](https://www.experienceperception.com/work/iron-man-2/), [Pushing Pixels interview](https://www.pushing-pixels.org/2016/07/20/the-craft-of-screen-graphics-and-movie-user-interfaces-interview-with-john-lepore.html))
- **Cantina Creative.** HUD and interface graphics for Iron Man 2, Iron Man 3, The Avengers, Age of Ultron, Infinity War and Endgame. For Iron Man 3 it moved from "2D graphic elements in 3D space" to a "3D photo-real, holographic approach", with volumetric light, optical flares and reflections from the environment, taking graphics "to a more organic holographic world". [High] ([Maxon](https://www.maxon.net/en/article/cantina-creative-gives-iron-man-3-a-heads-up-with-maxon-cinema-4d), [Cantina](https://www.cantinacreative.com/film/avengers-endgame))
- **Jayse Hansen.** Iron Man's HUD in The Avengers and other Marvel screens. He taught himself to fly from books, videos and simulators to design it. Stereo 3D forced everything to be in focus and readable, so **"everything on screen had a function and could not simply be decoration"**. [Medium] ([The Next Web](https://thenextweb.com/news/jayse-hansen-on-creating-tools-the-avengers-use-to-fight-evil-touch-interfaces-and-project-glass), [Pushing Pixels](https://www.pushing-pixels.org/2012/06/01/the-craft-of-screen-graphics-and-movie-user-interfaces-conversation-with-jayse-hansen.html))
- **Territory Studio** (London, founded 2010 by David Sheldon-Hicks and others). Credits include Prometheus, Guardians of the Galaxy (about 400–500 on-set screens plus about 30 VFX shots, each alien culture with "how they would understand information"), The Martian, Ad Astra and Blade Runner 2049. It grounds science films in "military or scientific references". "Hero screens" must "clearly communicate the narrative point... at a glance". [High] ([Art of VFX](https://www.artofvfx.com/guardians-of-the-galaxy-david-sheldon-hicks-creative-director-territory-studio/), [scifiinterfaces Q&A](https://scifiinterfaces.com/2020/06/23/scifi-interfaces-qa-with-territory-studio/), [Wikipedia](https://en.wikipedia.org/wiki/Territory_Studio))
- **GMUNK (Bradley Munkowitz), Oblivion (2013).** The brief "stressed functionality and minimalism" with "a bright, unified color palette" that works on dark and bright backdrops. A **dot grid** anchors every element. The Bubbleship cockpit was "the most researched" piece. [High] ([GMUNK](https://gmunk.com/OBLIVION-GFX), [Motionographer](https://motionographer.com/2013/04/19/bradley-g-munkowitz-oblivion-screen-graphics/), [HUDS+GUIS](https://www.hudsandguis.com/home/2013/05/02/oblivion-interface-design))
- **The Expanse** (Rhys Yorke, Sumeet Vats and others). "Any screen should look like it could be functional". The team followed "general military design guidelines", consulted a NASA astronaut, and built real interactive on-set screens where possible. The Rocinante's command-line typography reads as "under-the-hood", built for the crew rather than consumers. The Razorback's curved holograms were justified by cockpit space and turbulence. [High] ([HUDS+GUIS](https://www.hudsandguis.com/home/2021/theexpanse), [Pushing Pixels, Rhys Yorke](https://www.pushing-pixels.org/2021/09/04/the-art-and-craft-of-screen-graphics-interview-with-rhys-yorke.html))
- **Games.**
  - **Dead Space** (Dino Ignacio, Visceral): "We were not just diegetic by design, we were diegetic by implementation". Health is a light strip on the suit's spine, and the ship itself is treated as a character whose voice is the UI. [High] ([Game Developer](https://www.gamedeveloper.com/design/video-designing-i-dead-space-i-s-immersive-user-interface), [GDC Vault](https://gdcvault.com/play/1017723/Crafting-Destruction-The-Evolution-of))
  - **Elite Dangerous** (UI by Louise McLennan; audio by Matthew Florianz): a holographic cockpit and a diegetic galaxy map. The audio team deliberately made the hologram UI sound *mechanical and "always struggling, with just enough power"*, backed by an in-fiction rationale. [Medium] ([UXmatters](https://www.uxmatters.com/mt/archives/2015/09/an-interview-with-louise-mclennan-designer-of-elite-dangerous.php), [Florianz](https://www.matthewflorianz.com/audio/matthewflorianz_projects_elitedangerous.html))
- **A real spaceship: SpaceX Crew Dragon.** It replaced the Shuttle's roughly 2,000 switches and breakers with touchscreens whose UI is built on **Chromium**. Astronauts helped "refine... the way your touch is actually registered... to fly it cleanly and not make mistakes", and **hardware emergency buttons** remain in case the displays fail. UI/UX work is credited to Shane Mielke. [Medium/High] ([Space.com](https://www.space.com/spacex-crew-dragon-touchscreen-astronaut-thoughts.html), [TechCrunch](https://techcrunch.com/2020/05/04/this-is-certainly-different-astronauts-on-controlling-the-dragon-spacecraft-via-touchscreen/), [Shane Mielke](https://www.shanemielke.com/work/spacex/crew-dragon-displays/))
- **Scholarship.**
  - Shedroff and Noessel, *Make It So* (Rosenfeld, 2012), covers "why the future glows blue, is bitmapped, and is most definitely sans-serif", and why HAL's calm voice reads as psychopathic while the Enterprise's reads as friendly ([scifiinterfaces book page](https://scifiinterfaces.com/book/)).
  - Dave Addey, *Typeset in the Future*, identifies Eurostile Bold Extended as film's shorthand for "the future" ([Hyperallergic](https://hyperallergic.com/many-stories-are-told-through-the-typography-in-science-fiction-films/)). [High]

### 3.2 Principles that create the "alive, high-tech" feeling

1. **Legible at a glance; hero versus ambient.** Film screens must "communicate their points clearly and quickly" (LePore). Territory designs hero screens for the plot beat and gives ambient screens equal care so the whole world holds together. [High]
2. **Everything has a function.** Hansen's stereo rule, The Expanse's "could be functional", and GMUNK's "functionality and minimalism". Credibility comes from real references: military symbology, NASA, flight. [High]
3. **Periphery-to-centre escalation.** In Noessel's reading of the Iron Man HUD, elements sit on concentric invisible spheres around the head. Gauges stay small in peripheral vision until needed, then grow and centre. JARVIS tracks gaze and **escalates if a warning is ignored**: "attention management is crisis management". [High] ([scifiinterfaces, Iron HUD](https://scifiinterfaces.com/2015/07/13/iron-man-hud-just-the-functions/), [category](https://scifiinterfaces.com/category/marvel-cinematic-universe/iron-man/))
4. **One graphic system.** A dot grid, a unified palette that survives any backdrop, and a consistent type family (GMUNK). This consistency is what makes hundreds of screens read as one ship. [High]
5. **Depth, holography and diegesis.** Layering on depth planes (Noessel) and volumetric light and reflections (Cantina) create the premium feel. Diegesis, where the UI lives *in* the world as on Dead Space's spine, creates immersion. [High]
6. **Typography and colour as genre signals.** Eurostile-like extended sans, glowing blue, bitmapped detail (Addey; Shedroff and Noessel). Blue-dominant with sparse orange and green accents (The Expanse). [High]
7. **Motion carries meaning.** *Synthesis*, supported by §4: ambient idle motion (breathing, slow scanning, drifting particles) signals "powered on". Data-driven motion signals "something happened". Motion is the most detectable and most distracting peripheral cue (Bartram, Ware and Calvert 2003, [IJHCS](https://dl.acm.org/doi/10.1016/S1071-5819(03)00021-1)). The two kinds must therefore be visually distinct. [High for the perception finding]
8. **Sound is information, not garnish.** Principles from sci-fi UI sound designers: be **informative, relevant and appropriate** ("prioritized sounds over a meaningless barrage of beeps"); map pitch to magnitude and timbre to type; articulation over loudness ([A Sound Effect](https://www.asoundeffect.com/sci-fi-ui-sound-effects/)) [Medium]. Elite's "struggling hologram" shows that sound can carry a *material story*. Peep (USENIX LISA 2000) monitored networks as a "sonic ecology" of natural sounds, so admins could listen peripherally ([USENIX](https://www.usenix.org/legacyurl/peep-network-auralizer-monitoring-your-network-sound)). [High]
9. **Voice personality is part of the interface.** A calm voice can read as sinister or as trustworthy depending on context (HAL versus the Enterprise, in *Make It So*). [High]

### 3.3 Why movie UIs fail as real software

- **Built for story, not use.** "For the film the interface is built for one thing and one thing only, and that is to tell the story" (LePore). They are often built *after* filming to match an actor's motions. [High] ([Pushing Pixels](https://www.pushing-pixels.org/2016/07/20/the-craft-of-screen-graphics-and-movie-user-interfaces-interview-with-john-lepore.html), [Userbrain](https://www.userbrain.com/blog/the-5-biggest-usability-fails-in-movies/))
- **Zero learning curve.** Heroes instantly master alien systems. [Medium] ([Userbrain](https://www.userbrain.com/blog/the-5-biggest-usability-fails-in-movies/))
- **Ergonomics.** Minority Report-style interfaces require arms waving at shoulder level for long periods, which causes "gorilla arm". Tom Cruise reportedly had his wrists tied to scaffolding between takes. The term dates back to 1970s light pens on vertical monitors. [Medium] ([Dan Saffer](https://odannyboy.medium.com/why-you-want-but-wont-like-a-minority-report-style-interface-626d0fd6096b), [The Awl](https://www.theawl.com/2013/02/how-minority-report-trapped-us-in-a-world-of-bad-interfaces/), [Springer study](https://link.springer.com/chapter/10.1007/978-3-319-57987-0_41))
- **The JARVIS HUD is a placebo.** Noessel counts that "87% reposition themselves against his field of view without his having asked for it" and that six "risk dangerous mid-flight startle reactions by expanding quickly in place". He argues the HUD exists to make Tony *feel* in control while JARVIS actually flies and fights: "The Iron Man is JARVIS." [High] ([scifiinterfaces](https://scifiinterfaces.com/2015/09/15/tony-stark-is-being-lied-to-by-his-own-creation/)) *Synthesis:* this is the most important warning for an "agentic OS" dashboard. A beautiful bridge that performs activity while hiding what the agents actually decided is the failure mode the owner's word "correct" rules out.
- **Legibility costs.** *Synthesis* [Medium]: glow-on-dark thin type, translucent panels over moving backgrounds, dense micro-text and constant motion all trade contrast and readability for mood. They work for a two-second shot and fatigue over an eight-hour day.

### 3.4 How real products borrowed the feel without the unusability

- **Put spectacle in moments, keep the working surface quiet.**
  - Territory designs the "three-to-four-second moments when you first sit inside a vehicle" for car makers. [Medium] ([Built In](https://builtin.com/articles/sci-fi-ui))
  - Its Amazfit smartwatch work used "a minimalistic look with smooth animated details and transitions". [High] ([scifiinterfaces Q&A](https://scifiinterfaces.com/2020/06/23/scifi-interfaces-qa-with-territory-studio/), [BioSpace](https://www.biospace.com/huami-and-territory-studio-design-world-s-most-futuristic-smartwatch-interfaces))
- **Motion that maps to system state.**
  - Siri's edge glow shows the assistant is active. [Medium] ([Pocket-lint](https://www.pocket-lint.com/how-to-get-new-siri-look-glowing-border/))
  - Gemini's 2026 "Neural Expressive" redesign uses fluid animation and haptics where "the interface moves in ways that correspond to cognitive load" (processing, retrieving, switching modes). [Medium] ([Dezeen](https://www.dezeen.com/2026/05/19/google-rolls-out-neural-expressive-redesign-of-gemini-ai-tool/), [urdesignmag](https://www.urdesignmag.com/google-gemini-neural-expressive-redesign-2026/))
- **The dark cockpit.** Airbus's "lights out" philosophy: when systems are normal, panels are dark. Light means action or awareness is required. Colour is strictly semantic: red for immediate action, amber for failure, green for normal, blue for temporarily selected, white for abnormal switch position. [Medium] ([Aviapro](https://aviaproconsulting.com/article/post/engineering-spotlight-the-ergonomics-of-annunciation-%E2%80%93-airbus-lights-out-vs-boeing-quiet-and-dark), [A318 manual](https://www.manualslib.com/manual/2570669/Airbus-A318.html?page=207))
- **Real data behind the spectacle.** The GitHub homepage globe draws real open and merged pull requests, with adaptive quality (see §6). [High] ([GitHub blog](https://github.blog/engineering/engineering-principles/how-we-built-the-github-globe/))
- **Hardware fallback for critical controls.** Crew Dragon pairs a sci-fi touch UI with physical emergency buttons and interaction tuned against mis-touches. [High]
- **Consistency over novelty.** Oblivion's grid and palette discipline is exactly what design systems do. [High]

---

## 4. Visualising system and agent activity without diagrams

### 4.1 Precedents: living, organic and cockpit-like

| Example | Metaphor | Data truthfulness | Source |
|---|---|---|---|
| **Gource** | An animated tree: directories are branches, files are coloured leaves, contributors float near the files they touch | Replays real version-control logs | [gource.io](https://gource.io/), [GitHub](https://github.com/acaudwell/gource) [High] |
| **code_swarm** (Michael Ogawa, UC Davis) | "Organic information visualization": file particles fly to developer names, and collaborators cluster | Real commit history; published design study (IEEE TVCG) | [UC Davis](https://www.ucdavis.edu/news/visualizing-open-source-software-development), [IEEE](https://ieeexplore.ieee.org/iel5/2945/5290686/05290717.pdf) [High] |
| **Logstalgia** | Pong: requests are balls, the paddle hits successes, 404s fly past | Replays or streams real access logs | [logstalgia.io](https://logstalgia.io/) [High] |
| **Netflix Vizceral** | "Intuition engineering": particle flows between regions and services on WebGL, with zoom from global to region to service | **Dots are sampled**: "exact numbers stop being as important and relative information is much more actionable". Built for failover at a glance | [Netflix TechBlog](https://netflixtechblog.com/vizceral-open-source-acc0c32113fe), [Monitorama notes](https://github.com/zapman449/monitorama_2016_notes/blob/master/Wed01__Intuition.Engineering.at.Netflix__by_Justin.Reynolds.md) [High] |
| **earth.nullschool.net** (Cameron Beccario) | Particles advected by wind, leaving short trails | Each particle is a "seed" moved by real model data (NOAA and others, updated every 3 h) | [About](https://earth.nullschool.net/about), [GitHub](https://github.com/cambecc/earth) [High] |
| **GitHub globe** | A planet with spikes (open PRs) and arcs (merged PRs) | Real PR data; five visual layers | [GitHub blog](https://github.blog/engineering/engineering-principles/how-we-built-the-github-globe/) [High] |
| **Peep** | A sonic ecology (birdsong and similar) for network events | Real network state, mixed into one ambient stream | [USENIX](https://www.usenix.org/legacyurl/peep-network-auralizer-monitoring-your-network-sound) [High] |
| **Calm technology** (Weiser and Brown, Xerox PARC 1995; Amber Case 2015) | Information in the periphery that moves to the centre only when needed | A design principle, not a product | [Wikipedia](https://en.wikipedia.org/wiki/Calm_technology), [Case's principles](https://www.caseorganic.com/post/principles-of-calm-technology) [High] |

### 4.2 Evidence on when motion and embellishment help

- **Animated transitions help tracking.** Heer and Robertson (InfoVis 2007) found animated transitions between chart states significantly improved object tracking and value estimation. Staged animation (separating axis rescale from value change) helps further, and about 1 s is a good duration. [High] ([Stanford/UW PDF](https://idl.cs.washington.edu/files/2007-AnimatedTransitions-InfoVis.pdf))
- **Animation is not automatically better.** Tversky, Morrison and Bétrancourt (2002) found that when animation beat static graphics, the animated version usually carried *more information or interactivity*. Their **Congruence Principle** says the graphic's form should correspond to the concept's structure. [High] ([IJHCS](https://dl.acm.org/doi/10.1006/ijhc.2002.1017), [Stanford PDF](https://hci.stanford.edu/courses/cs448b/papers/Tversky_AnimationFacilitate_IJHCS02.pdf))
- **Motion grabs attention, for better and worse.** Moticons (icons with simple motion) are detected better than colour or shape coding, especially in the periphery, and they also distract. [High] ([Bartram, Ware, Calvert 2003](https://dl.acm.org/doi/10.1016/S1071-5819(03)00021-1))
- **Embellishment aids memory, not accuracy.** In "Useful Junk?" (CHI 2010), embellished charts were interpreted no worse and recalled better 2–3 weeks later. The authors warn that imagery can bias interpretation. [High] ([PDF](https://sites.stat.columbia.edu/gelman/communication/Bateman2010.pdf))
- **Situation awareness has three levels.** Endsley's model: perception, comprehension, **projection of future states**. A control surface should support all three, not just show current state. [High] ([Endsley 1995](https://www.researchgate.net/publication/210198492_Endsley_MR_Toward_a_Theory_of_Situation_Awareness_in_Dynamic_Systems_Human_Factors_Journal_371_32-64))

### 4.3 When a living visualisation stays correct: a truthfulness contract

*Synthesis*, with the grounding for each rule in brackets:

1. **One referent per mover.** Every particle, star, orbit or pulse corresponds to a real entity or event, such as an owl, a task, a tool call, a scheduled job or a message (Hansen's "function, not decoration"; earth.nullschool's data-seeded particles).
2. **Declared sampling.** If volume is sampled (as in Vizceral), the view says so, and exact numbers are one tap away.
3. **Two motion vocabularies.** *Idle* motion (the system is powered and connected) must be low-amplitude and visibly different from *event* motion (something changed). Otherwise ambient drift triggers false alarms in peripheral vision (Bartram).
4. **Honest liveness.** The ambient "alive" animation must be driven by a server heartbeat, not by the client's render loop. When the stream is stale or disconnected, the bridge must visibly *stop breathing* and show the age of the last event. This matters because mobile browsers silently kill sockets while the JavaScript object still looks connected (§6.6). This is the anti-placebo rule from Noessel's JARVIS critique.
5. **Congruent encoding.** Map structure to structure (Tversky): hierarchy as nesting or orbit, flow as flow, time until a scheduled run as distance, health as colour under aviation semantics (dark cockpit).
6. **Drill to ground truth.** Every glyph opens the underlying record (task row, trace, log, approval). Temporal's grouped event spans and Langfuse's zoomable timeline are good forensic layers under the ambient layer.
7. **Quiet when normal.** The dark-cockpit default means a healthy platform should be calm. Colour and motion intensity scale with severity, not with activity volume alone.
8. **Show projection.** Upcoming scheduled jobs, pending approvals and agents about to time out should be visible *before* they happen (Endsley level 3). *Synthesis:* an orbital metaphor, where a job approaches the centre as its due time nears, fits this naturally.
9. **Transitions for continuity.** Use staged animated transitions of about 1 s when the view reorganises (Heer and Robertson), and keep continuous motion for the ambient layer only.
10. **Decoration is allowed only where it cannot be read as data.** Background starfield, glow and chrome are fine. Anything with position, size, speed or colour variation must be data.

*Candidate metaphors mapped to StackOwl's real entities.* These are options for §8, not recommendations:
- **Constellation / star map.** Owls are stars; brightness is activity; links are delegations. Channels (Telegram, Slack, TUI) sit at the edges as ingress beacons.
- **Orbital system.** The core sits at the centre. Scheduled jobs orbit, and their radius shrinks as the due time approaches. Running tasks are satellites that fall inward and land when delivered.
- **Ship bridge with stations.** Diegetic panels per subsystem (helm = task loop, comms = channels, engineering = self-healing and health, science = memory, tactical = permissions and approvals). This maps well to Endsley's levels and to Dead Space-style diegesis.
- **Organism.** A heartbeat for liveness, a circulation of particles for message flow, an immune response for self-healing. It is evocative but the weakest match to discrete tasks and approvals [*Synthesis*, Medium].

---

## 5. Voice presence in a visual interface

### 5.1 How assistants visualise listening, thinking and speaking

- **LiveKit Agents UI** (open source, installed via the shadcn CLI so the source lands in your codebase). Visualizers (bar, grid, radial, wave, and the shader-based **"aura"**) take `state` plus `audioTrack` and share one props interface. The agent states are initializing, connecting, idle, **listening, thinking, speaking**, failed. Its rationale is that visualizers ensure "users know the agent is listening or speaking". [High] ([LiveKit docs](https://docs.livekit.io/frontends/agents-ui/audio-visualizer/prebuilt/), [LiveKit blog](https://livekit.com/blog/design-voice-ai-interfaces-with-agents-ui), [AgentState reference](https://docs.livekit.io/reference/agents-js/types/agents.voice.AgentState.html))
- **ElevenLabs UI** (open source, built on shadcn/ui). Its Orb is built with Three.js, reacts to audio, and colours agent states (idle, listening, talking). [High] ([ElevenLabs blog](https://elevenlabs.io/blog/elevenlabs-ui), [ui.elevenlabs.io](https://ui.elevenlabs.io/))
- **Pipecat Voice UI Kit** (open source; React components, hooks, templates, debug console; Tailwind 4; responsive across desktop, tablet and mobile). [High] ([Pipecat docs](https://docs.pipecat.ai/client/voice-ui-kit), [GitHub](https://github.com/pipecat-ai/voice-ui-kit))
- **ChatGPT.** Advanced Voice launched a blue animated orb in Sept 2024, replacing the black dots demoed in May. OpenAI later moved voice *into the chat view* instead of a full-screen orb. [Medium] ([TechCrunch](https://techcrunch.com/2024/09/24/openai-rolls-out-advanced-voice-mode-with-more-voices-and-a-new-look), [WebProNews](https://www.webpronews.com/the-death-of-the-overlay-how-openais-integrated-voice-mode-signals-the-end-of-static-computing/))
- **Siri.** A glowing, colour-shifting border around the whole screen. [Medium] ([Pocket-lint](https://www.pocket-lint.com/how-to-get-new-siri-look-glowing-border/))
- **Gemini Live (2026).** A pill-shaped blue waveform that grows and moves with the user's loudness; Live is merged into the main chat. [Medium] ([Android Authority](https://www.androidauthority.com/gemini-overlay-and-live-ui-changes-3655577/), [Engadget](https://www.engadget.com/2176568/google-redesigned-gemini-comes-with-a-new-interface-and-ai-models/))
- **Hardware assistants.** Smart-speaker LED patents describe light patterns modelled on human cues: an "attentive wake-up spin" followed by "gentle breathing" for listening, and rising and falling LED counts for thinking. [Medium] (Google patent family, e.g. [US 10,602,585](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10602585))
- **Implementation pattern.** A fragment-shader sphere with Perlin-noise displacement, colours per state, and Fresnel glow that intensifies with Web Audio FFT energy. [Low: community projects] ([voice-orb](https://voice-orb.netlify.app/), [react-ai-voice-visualizer](https://github.com/chevgan/react-ai-voice-visualizer))

### 5.2 Turn-taking and interruption

- **Latency targets** [Low/Medium, vendor engineering blogs]:
  - turn gap of about 200–400 ms from end of agent speech to the next turn
  - barge-in under 150 ms
  - false barge-in below 2%
  - TTS flush below 60 ms

  ([FutureAGI](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026/), [SyncSoft](https://www.syncsoft.ai/en/blog/voice-agent-barge-in-vad-tuning-2026))
- **The 2026 shift is from energy-threshold voice activity detection (VAD) to learned turn detection** that tells backchannel ("mm-hm") apart from a real interruption. Examples: Deepgram Flux at about 250 ms end-of-turn, Krisp Turn Prediction under 200 ms. [Low/Medium] ([Gradium](https://gradium.ai/content/semantic-vad-voice-agents-turn-detection-2026), [FutureAGI](https://futureagi.com/blog/voice-ai-barge-in-turn-taking-2026/))
- **Treat interruption as an event lifecycle,** not a toggle. Correction, backchannel, noise and escalation each need different handling. [Low/Medium] ([Hamming](https://hamming.ai/resources/voice-agent-interruption-handling-runbook))
- **Full duplex is available open source.** Kyutai's Moshi models the user's and the agent's audio as parallel streams, which removes explicit turns. Latency is about 200 ms on an L4 GPU. The weights are CC-BY 4.0. [High] ([GitHub](https://github.com/kyutai-labs/moshi), [arXiv 2410.00037](https://arxiv.org/abs/2410.00037))

*Synthesis on visual cues for turn-taking:*
- **Listening** must visibly react to *the user's own* microphone level. That is the proof of being heard.
- **Thinking** must look different from listening (for example, inward motion instead of outward pulses), so silence is never ambiguous.
- **Speaking** is driven by the *agent's* output audio amplitude.
- On **barge-in**, the speaking visual must collapse at the moment the audio is flushed, not when the server acknowledges. Otherwise the screen lies for a few hundred milliseconds.
- A persistent, subtle **mic-live indicator** is required whenever audio is being captured. This matches the Windows principle that agent activity must be visible.

### 5.3 Keeping voice and screen in sync

- **One state machine, server-authoritative.** *Synthesis:* the voice pipeline's state (listening, thinking, speaking, interrupted) should be the same event stream the bridge renders. Visual state is derived from it and never inferred separately in the browser.
- **Word-level TTS timestamps.** Word and phoneme timings let captions, karaoke-style highlighting and "the owl is saying this about *that* panel" cues line up with audio. When a TTS engine lacks them, forced alignment through speech-to-text can recover them. [Medium] ([Hume](https://dev.hume.ai/docs/text-to-speech-tts/timestamps), [Soniox wiki](https://soniox.com/wiki/timestamps-forced-alignment))
- **On-screen reference resolution.** iOS 27 lets Siri resolve "the third one" from what is visible. The dashboard equivalent is to give the voice agent the current view state, so "pause that job" refers to the highlighted orbit. [Medium] ([Dracode](https://dracode.dev/blog/2026-06-08-09-wwdc-2026-siri-2-app-intents/))
- **Streaming progress.** App Intents 2.0 streams progress for long actions. Voice should narrate briefly while the bridge shows detail, the ChatGPT agent "narration plus live view" pattern. [Medium]

### 5.4 Self-hosted voice feasibility (English only, Jetson)

[Medium: community projects, not benchmarks]
- A Jetson Orin Nano assistant with Whisper for speech-to-text and Piper for TTS reports Piper at under 500 ms per sentence on CPU, and Whisper at about 11x realtime with CUDA ([Jarvis-home](https://github.com/itsMustafamr/Jarvis-home)).
- A Whisper plus Kokoro (82M parameters) loop runs on tensor cores at about 15 W total ([dev.to](https://dev.to/yankoaleksandrov/i-built-a-full-voice-pipeline-on-a-eu399-edge-ai-box-whisper-kokoro-on-tensor-cores-386j)).
- A LiveKit-based local stack uses streaming Nemotron speech-to-text (partial transcripts, so lower latency than Whisper) plus Kokoro, with a Jetson profile ([local-voice-ai](https://github.com/ShayneP/local-voice-ai)).
- Pipecat and LiveKit are both open-source frameworks with self-hostable transports.

---

## 6. Web feasibility in 2026, mobile and desktop

### 6.1 WebGPU and WebGL

| Browser / platform | WebGPU status | Source |
|---|---|---|
| Chrome / Edge on Windows, macOS, ChromeOS | Shipped since v113 | [gpuweb status](https://github.com/gpuweb/gpuweb/wiki/Implementation-Status), [web.dev (Nov 2025)](https://web.dev/blog/webgpu-supported-major-browsers) [High] |
| Chrome Android | v121 on Android 12+ with ARM, Qualcomm or Intel GPUs; Imagination on Android 16+ from v139; Samsung Xclipse pending | [gpuweb status](https://github.com/gpuweb/gpuweb/wiki/Implementation-Status) [High] |
| Chrome Linux | Intel Gen12+ from v144; NVIDIA (driver 535.183.01+, Wayland) from v147; others behind flags | same [High] |
| Safari (macOS, iOS, iPadOS, visionOS) | Shipped in Safari 26 (iOS 26); older iOS has no WebGPU | same; [webgpu.com](https://www.webgpu.com/news/webgpu-hits-critical-mass-all-major-browsers/) [High] |
| Firefox | Windows v141 (Jul 2025); Apple Silicon macOS v145/147; **Linux and Android still Nightly**, Linux expected in 2026 | same [High] |
| WebGPU compatibility mode (OpenGL ES 3.1 / D3D11 devices) | `featureLevel: "compatibility"`; origin trial ran to Chrome 145 (Apr 2026) | [Chrome blog](https://developer.chrome.com/blog/new-in-webgpu-139), [blink-dev](https://groups.google.com/a/chromium.org/g/blink-dev/c/N3RlLGCOTJ4) [High] |
| WebGL 2 | Available in all current major engines (only Internet Explorer never shipped it) | [caniuse](https://caniuse.com/webgl2), [LambdaTest](https://www.lambdatest.com/web-technologies/webgl2) [High] |

- "About 95% of users have WebGPU-capable browsers" appears in a Three.js consultancy blog. Treat it as an estimate. [Low] ([Utsubo](https://www.utsubo.com/blog/threejs-2026-what-changed))
- **Jetson as a display.** Users report Chromium on Jetson Orin showing OpenGL, Vulkan and WebGPU disabled in `chrome://gpu`, and Chrome on Linux generally still renders through GL. [Low/Medium] ([NVIDIA forum](https://forums.developer.nvidia.com/t/agx-orin-webgl-enable/245085), [piveral](https://nvidia-jetson.piveral.com/jetson-orin-nano/chromium-electron-applications-hang-on-launch-on-gnome-wayland-for-nvidia-jetson-orin-nano/), [Luciad](https://dev.luciad.com/portal/productDocumentation/LuciadRIA/docs/articles/tutorial/getting_started/setup_webgpu.html)) *Synthesis:* the Jetson serves the app, and phones and desktops render it. Only a Jetson-attached kiosk display would hit this limitation.

### 6.2 Open-source rendering libraries

- **Three.js `WebGPURenderer`.** Production-usable since r171 (Sept 2025). It uses WebGPU by default with an automatic WebGL 2 fallback. **TSL (Three Shader Language)** compiles one shader source to WGSL or GLSL. The claimed gains of 30–50% on dense scenes, lower CPU overhead and compute shaders for particles come from one consultancy, so treat them as indicative. [High for the architecture; Low for the percentages] ([three.js manual](https://threejs.org/manual/en/webgpurenderer.html), [Utsubo](https://www.utsubo.com/blog/threejs-2026-what-changed))
- **react-three-fiber.** v9 supports WebGPU via an async `gl` factory, and most Drei helpers work (with some post-processing edge cases). v10, in development, makes WebGPU a first-class `renderer` prop. `frameloop="demand"` renders only on change: "saves battery and keeps noisy fans in check". [High/Medium] ([R3F v9 migration](https://r3f.docs.pmnd.rs/tutorials/v9-migration-guide), [R3F scaling performance](https://r3f.docs.pmnd.rs/advanced/scaling-performance), [Utsubo](https://www.utsubo.com/blog/webgpu-threejs-migration-guide))
- **PixiJS v8 (2D).** WebGPU and WebGL backends. The v8 `ParticleContainer` renders about **1,000,000 particles at 60 fps versus about 200,000 regular sprites**. WebGPU is *not automatically faster* because PixiJS is often CPU-bound, and it helps most with many batch breaks (filters, masks, blend modes). [High] ([PixiJS blog](https://pixijs.com/blog/particlecontainer-v8), [renderers guide](https://pixijs.com/8.x/guides/components/renderers))
- **Voice UI components** (§5): LiveKit Agents UI, ElevenLabs UI, Pipecat Voice UI Kit, all React and open source. **Check each licence before adoption.**

### 6.3 PWA installability

- **iOS 26:** every site added to the Home Screen opens as a web app by default, with or without a manifest. Users can toggle this off. [High/Medium] ([MacRumors](https://www.macrumors.com/how-to/save-safari-bookmark-web-app-iphone-home-screen/), [iDownloadBlog](https://www.idownloadblog.com/2025/06/17/apple-ios-26-safari-web-apps-home-screen-bookmarks/), [Michael Tsai](https://mjtsai.com/blog/2025/10/03/web-apps-in-ios-26/))
- **Web push on iOS:** available from 16.4, but **only for home-screen web apps**, not Safari tabs. It requires explicit permission, with no silent push or background wake. Safari 18.4 added Declarative Web Push and Screen Wake Lock. [Medium] ([MobiLoud](https://www.mobiloud.com/blog/progressive-web-apps-ios/), [MagicBell](https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide))
- **Microphone and camera** are available to iOS home-screen web apps. [Medium] ([MobiLoud](https://www.mobiloud.com/blog/progressive-web-apps-ios/))

### 6.4 Battery and thermal cost of an always-animated UI

- **Hidden tabs:** `requestAnimationFrame` is paused in most browsers in background tabs and hidden iframes. [High] ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame))
- **iOS Low Power Mode** throttles `requestAnimationFrame` *and CSS animations* to **30 fps**, and this is effectively undetectable by the page. macOS Safari does the same on laptops in low power mode. [High/Medium] ([WebKit bug 168837](https://bugs.webkit.org/show_bug.cgi?id=168837), [Motion magazine](https://motion.dev/magazine/when-browsers-throttle-requestanimationframe), [Popmotion](https://popmotion.io/blog/20180104-when-ios-throttles-requestanimationframe/))
- **Chrome Energy Saver** reduces the frame rate of animations and video. Google publishes no exact figure. [High] ([Chrome for Developers](https://developer.chrome.com/en/blog/memory-and-energy-saver-mode))
- **Anecdotal drain:** a continuously animating page drained 4% battery in 15 minutes against under 1% without animation. [Low: single GitHub issue] ([cytoscape #2657](https://github.com/cytoscape/cytoscape.js/issues/2657))
- **Thermal:** phones rely on passive cooling and throttle sustained GPU load within minutes. Sustained performance runs at about 50–65% of burst, so target 60–70% of peak capacity. [Low: benchmark/vendor sites] ([Abratabia](https://www.abratabia.com/mobile-browser-performance/battery-and-thermal.php), [volume-shader.org](https://www.volume-shader.org/learn/mobile-gpu-benchmark-test))
- **Measured web-asset power** (M1 powermetrics): SVG turbulence filters spike energy draw in Safari and Firefox, WebGL is CPU-efficient, and declarative CSS animations can be optimised away when not visible. [Medium] ([Torchbox](https://torchbox.com/wagtail-cms-services/blog/how-much-power-do-web-assets-use/), [WebKit: how web content affects power](https://webkit.org/blog/8970/how-web-content-can-affect-power-usage/))
- **Proven mitigations** (GitHub globe):
  - monitor FPS, and "if we fail to maintain 55.5 FPS over the last 50 frames" degrade through four quality tiers (pixel ratio 2.0 to 1.5, geometry about 12k to about 8k circles, less frequent raycasts, slower data animation)
  - antialiasing off
  - an SVG placeholder before WebGL loads

  [High] ([GitHub blog](https://github.blog/engineering/engineering-principles/how-we-built-the-github-globe/), [homepage perf](https://github.blog/engineering/user-experience/making-githubs-new-homepage-fast-and-performant/))

*Synthesis:* an "alive" bridge that is kind to batteries uses these techniques:
- **render on demand** plus a low-rate ambient tick (for example 15–30 fps for idle breathing, full rate only during event transitions)
- an **adaptive quality ladder**
- **pause on `visibilitychange`**
- a DOM or SVG **"static bridge"** tier for low-power devices

### 6.5 Reduced motion and accessibility

- **WCAG 2.2.2 Pause, Stop, Hide (Level A):** moving content that starts automatically, lasts more than 5 s and sits alongside other content needs a mechanism to pause, stop or hide it. **2.3.3 Animation from Interactions (AAA):** motion triggered by interaction must be disableable. `prefers-reduced-motion` is a sufficient technique for 2.3.3. Whether it satisfies 2.2.2 on its own is under discussion at W3C, so a visible on-page control is the safe reading. **2.3.1:** no more than three flashes per second. [High] ([W3C 2.2.2](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html), [W3C 2.3.3](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html), [w3c/wcag #4319](https://github.com/w3c/wcag/issues/4319))
- **Prevalence:** 35% of US adults aged 40+ show some vestibular dysfunction on postural testing; chronic motion sensitivity is estimated at 28% of the general population. [Medium] ([A11Y Project](https://www.a11yproject.com/posts/understanding-vestibular-disorders/), [clinicaltrials.gov](https://clinicaltrials.gov/study/NCT06128707))
- *Synthesis:* a GPU canvas is opaque to screen readers. Every entity drawn on the bridge needs a parallel DOM or ARIA representation (list or tree of owls, tasks, approvals) that is also the keyboard path. Text must never live only inside the canvas.

### 6.6 Real-time transport to the browser

- **SSE (Server-Sent Events):** one-way, auto-reconnect, and `Last-Event-ID` resume. Over **HTTP/1.1** browsers cap about **6 connections per origin, shared across all tabs**. Over **HTTP/2** streams multiplex, with the limit set by the server (typically 100–128 concurrent streams). [High/Medium] ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events), [server-sent-events.com](https://www.server-sent-events.com/sse-protocol-fundamentals-architecture/http2-and-http3-for-event-streams/))
- **WebSocket:** bidirectional. **iOS kills sockets of backgrounded PWAs.** Typical failures are close code 1005, or a socket that hangs and never fires `close`. iOS freezes JavaScript about 30 s after backgrounding. The recommended pattern is a `visibilitychange` reconnect plus a heartbeat timeout of 45 s or more. [Medium] ([openclaw #2672](https://github.com/openclaw/openclaw/issues/2672), [trpc #4078](https://github.com/trpc/trpc/issues/4078), [SSE resume guide](https://www.server-sent-events.com/frontend-consumption-client-patterns/mobile-background-tab-handling/resuming-sse-streams-after-mobile-tab-suspension/)) Note: OpenClaw, a sibling self-hosted agent platform, hit exactly this bug with event-sequence gaps.
- **WebTransport:** Baseline since **Safari 26.4 (March 2026)**. It gives low-latency bidirectional streams and datagrams over HTTP/3. [High/Medium] ([WebKit Safari 26.4](https://webkit.org/blog/17862/webkit-features-for-safari-26-4/), [webrtc.ventures](https://webrtc.ventures/2026/04/webtransport-is-now-baseline-what-it-means-for-real-time-media/))
- **Voice audio:** the open-source voice UI kits ship WebRTC transports (Pipecat's `small-webrtc-transport`, LiveKit). *Synthesis:* WebRTC for media, a separate event channel for bridge state.
- **Repository note** (fact, not a recommendation): `src/stackowl/control_plane/auth.py` records that "middleware does NOT run for a WebSocket upgrade". Any socket-based event channel therefore needs explicit authentication at upgrade time.

---

## 7. Design tensions

1. **Cinematic versus usable.** Film UI is tuned for a two-second read and a camera, while a dashboard is used for hours. *Resolution space:* spectacle in *moments* (boot, owl creation, incident, voice activation) and a calm working surface (Territory's car-entry moments, Airbus dark cockpit, the Gemini and Siri approach).
2. **Alive versus battery and thermals.** Continuous GPU animation drains phones and throttles within minutes, and iOS Low Power Mode silently halves frame rate. *Resolution space:* render-on-demand, low-rate ambient ticks, adaptive quality ladders (GitHub globe), pause when hidden, a static tier.
3. **Spectacle versus truthful state.** This is the placebo HUD problem (Noessel): motion that performs activity reads as real activity. The worst case is a client animation that keeps breathing after the stream died. *Resolution space:* the truthfulness contract in §4.3, with server-driven liveness, declared sampling and drill-to-record.
4. **Ambient motion versus attention.** Motion is the strongest peripheral cue (Bartram), so an always-moving bridge constantly tugs attention and blunts real alerts. *Resolution space:* separate idle and event motion vocabularies; severity-scaled intensity; quiet-when-healthy.
5. **Fleet autonomy versus supervision burden.** More parallel owls means more review and approval load (Cursor 3). *Resolution space:* "needs you" as the primary state, a priority inbox (notify, question, review), conditional approvals, peek without entering.
6. **Immersion (GPU canvas) versus accessibility (DOM).** Canvas is invisible to assistive technology, and motion harms vestibular users. *Resolution space:* a hybrid where the DOM carries all text and controls and the canvas carries only ambient spatial context; reduced-motion and visible pause controls.
7. **Desktop density versus mobile clarity.** A bridge with stations does not fit a 6-inch screen, and mid-air or precise gestures fail on touch. *Resolution space:* one data model with two compositions (bridge versus pocket), or a single spatial view with semantic zoom.
8. **Voice versus screen authority.** If voice and visuals derive state separately, they drift (speaking visual after a barge-in; "that job" ambiguity). *Resolution space:* one server-side state machine; word timestamps; view context passed to the voice agent.
9. **Real-time versus honest staleness on mobile.** Sockets die silently in the background. "Live" must be verifiable. *Resolution space:* heartbeat, `Last-Event-ID` resume, a visible "last event N s ago", and replay of the gap on resume.
10. **Custom visual identity versus off-the-shelf components.** Voice orbs and dashboards exist as open-source React kits (LiveKit, ElevenLabs, Pipecat, Hermes plugin model), but adopting them imports their look and assumptions. The existing StackOwl owl mark is fixed.
11. **Sci-fi genre signals versus brand and legibility.** Eurostile-style extended caps, glow-on-dark and translucent layers signal "future" but cost contrast and reading speed.
12. **Sound as information versus shared spaces.** Sonification (Peep, Elite) aids peripheral monitoring but is unwelcome in offices and on phones.
13. **One more "OS" versus trust.** Microsoft's backlash shows users react against agent layers that feel imposed or opaque. A dashboard that claims control must actually give it: pause, takeover and audit, not just visuals.

---

## 8. Decisions the owner must make

1. **What is the bridge's primary job?**
   (a) An *ambient* glanceable bridge (calm, peripheral) with drill-down.
   (b) An *operating console* optimised for commanding owls.
   (c) Separate ambient, console and forensic modes.
   (d) An ambient bridge on the landing view plus an always-present command strip (text and voice).
2. **Which spatial metaphor carries the platform?**
   (a) Constellation or star map, with owls as stars.
   (b) Orbital system, where scheduled jobs approach the core as due time nears.
   (c) Ship bridge with diegetic stations (helm, comms, engineering, science, tactical).
   (d) Organism (pulse, circulation, immune response).
   (e) A hybrid, for example an orbital core inside a bridge frame.
3. **How strict is the truthfulness contract?**
   (a) Strict: every mover maps to a record, idle motion is visibly distinct, liveness is server-driven, sampling is declared.
   (b) Strict on the working surface, cinematic only during transitions and "moments".
   (c) Decorative motion allowed anywhere.
4. **What does "healthy" look like?**
   (a) Dark cockpit: quiet and dim when normal, lit on exception.
   (b) Busy and visibly working at all times.
   (c) User-selectable.
5. **Where do approvals and consent live?**
   (a) A priority inbox in the dashboard, as the single source, mirrored to channels.
   (b) They stay in channels (Telegram, Slack), and the dashboard shows them read-only.
   (c) Both, with first-to-answer wins.
   Also: should takeover (pause, stop, take over an owl mid-task) be mandatory for every running task?
6. **Rendering tier strategy?**
   (a) WebGPU-first Three.js/R3F with automatic WebGL 2 fallback.
   (b) 2D GPU (PixiJS) with faux depth.
   (c) SVG/CSS only.
   (d) Hybrid: DOM for text and controls, GPU canvas only for the ambient layer, and a static tier for low-power or reduced-motion.
7. **Motion and battery budget?**
   (a) Full-rate continuous animation.
   (b) A capped ambient tick (15–30 fps) with full rate only on events.
   (c) Render-on-demand only.
   And what happens under reduced-motion: freeze to a static bridge, crossfades only, or a user-chosen intensity?
8. **Mobile parity?**
   (a) The same bridge scaled down.
   (b) A dedicated "pocket" composition from the same data model (inbox, voice, one focused view).
   (c) Mobile as voice-first companion, desktop as full bridge.
9. **Real-time transport?**
   (a) SSE over HTTP/2 plus plain POST for commands.
   (b) WebSocket for everything.
   (c) WebTransport.
   In every case, with heartbeat, resume from last event ID, gap replay, and a visible staleness indicator.
10. **Voice architecture, when voice arrives?**
    (a) Cascaded pipeline (streaming speech-to-text, then the platform's LLM loop, then TTS) via Pipecat or LiveKit.
    (b) Full-duplex speech model (Moshi-class) for conversation, with the owl loop behind it.
    (c) Push-to-talk first, always-listening later.
    Also: wake word or not, and must a mic-live indicator be always visible?
11. **Voice presence form?**
    (a) Central orb.
    (b) Edge glow around the bridge (Siri-like).
    (c) A waveform pill in the command strip (Gemini-like).
    (d) The owl mark itself animates as the presence, without redesigning the mark.
12. **Sound design?**
    (a) Silent by default, earcons opt-in.
    (b) A minimal earcon vocabulary on (approval needed, task delivered, failure).
    (c) Full ambient sonification of platform activity (Peep-style) as an opt-in mode.
13. **Visual identity?**
    (a) Overt sci-fi genre signals (extended caps, glow, holographic translucency).
    (b) A restrained "real cockpit" style (aviation colour semantics, high contrast, owl brand).
    (c) Themeable, as with Hermes' YAML themes and plugin slots.
14. **Build or adopt the voice and visual primitives?**
    (a) Adopt open-source kits (LiveKit Agents UI, ElevenLabs UI, Pipecat Voice UI Kit) and restyle.
    (b) Build bespoke shaders and components.
    (c) Adopt for transport and state, build the visuals.
    Licence review is needed for any adopted kit.
15. **Is the Jetson ever the display?**
    (a) No, server only; phones and desktops render.
    (b) Yes, a kiosk bridge on the Jetson, which would require validating Chromium GPU acceleration on Jetson Linux first.
