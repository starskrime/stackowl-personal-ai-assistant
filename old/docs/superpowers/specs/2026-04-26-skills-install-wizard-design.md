# `/skills install` Wizard — Design Spec

## Goal

Add a `/skills install` slash command to the StackOwl chat interface (CLI + Telegram) that guides the user through a multi-turn wizard to install a skill from ClawHub, GitHub, or a local path.

## Architecture

A single `SkillInstallWizard` class owns the full state machine. `GatewayCore` holds an in-memory map of active wizards keyed by session ID. The Telegram adapter handles inline keyboard callback queries and routes them into the wizard alongside normal text input.

**Tech Stack:** TypeScript, existing `SkillInstaller`, `ClawHubClient`, grammY (Telegram)

---

## Wizard Flow

```
/skills install
  └─ "Choose source:" + options
       ├─ ClawHub  → "Enter search keyword:"
       │              → show up to 5 results
       │              → user picks one → install → done
       ├─ GitHub   → "Enter path (e.g. github:user/repo/skill):"
       │              → install → done
       └─ Local    → "Enter path (e.g. ./my-skill):"
                      → install → done
```

**State machine:**
```
choose_source → search_clawhub → pick_clawhub → done
             → enter_github                   → done
             → enter_local                    → done
```

`/cancel` at any step exits immediately with "Cancelled."

---

## Components

### `src/skills/wizard.ts` (new)

`SkillInstallWizard` class.

```typescript
interface WizardResponse {
  text: string;
  done: boolean;
  inlineKeyboard?: { text: string; data: string }[][];  // Telegram only
}
```

Constructor: `(workspacePath: string, clawHubClient: ClawHubClient)`

Methods:
- `start(): WizardResponse` — returns source selection prompt
- `step(input: string): Promise<WizardResponse>` — advances state, returns next prompt or result

Internal state:
```typescript
type WizardStep =
  | 'choose_source'
  | 'search_clawhub'
  | 'pick_clawhub'
  | 'enter_github'
  | 'enter_local'
  | 'done';

interface WizardState {
  step: WizardStep;
  searchResults?: ClawHubSkill[];
}
```

### `src/gateway/core.ts` (modify)

- Add `private wizardSessions = new Map<string, SkillInstallWizard>()`
- At top of message handler, before LLM dispatch: check `wizardSessions.get(sessionId)`. If active, call `wizard.step(text)`, send response, delete wizard if `done=true`.
- On `/skills install`: create `new SkillInstallWizard(workspacePath, clawHubClient)`, store in map, send `wizard.start()`.
- On new `/skills install` while wizard active: replace existing wizard silently.

### `src/gateway/adapters/telegram.ts` (modify)

- Handle `callback_query` events from grammY.
- Call `ctx.answerCallbackQuery()` to clear Telegram spinner.
- Route `callbackQuery.data` into the active wizard via `wizard.step(data)`.
- Render `WizardResponse.inlineKeyboard` using grammY's `InlineKeyboard` builder.

---

## Telegram Inline Keyboard

**Source menu:** 3 buttons in one row.
```
[ClawHub]  [GitHub]  [Local]
```
`callbackData`: `"clawhub"`, `"github"`, `"local"`

**Search results:** Up to 5 buttons, one per row, showing skill name + short description.
`callbackData`: the skill slug (e.g. `"git_branch"`)

If more than 5 results: append `[Show more]` button with `callbackData: "more"`, re-queries ClawHub with offset +5.

**CLI:** Same `WizardResponse.text` content as numbered list. Wizard accepts `"1"`, `"2"`, `"3"` (and `"clawhub"`, `"github"`, `"local"`) as valid source input. `inlineKeyboard` field is ignored.

---

## Error Handling

| Situation | Response | Wizard state |
|---|---|---|
| Invalid input at source menu | "Please enter 1, 2, or 3." | re-prompt `choose_source` |
| ClawHub search returns 0 results | "No skills found for \"X\". Try another keyword:" | re-prompt `search_clawhub` |
| ClawHub API unreachable | "ClawHub unavailable. Try again later." | `done=true` |
| GitHub path 404 / bad format | "Could not fetch skill: \<error\>. Try again or /cancel" | re-prompt `enter_github` |
| Local path missing SKILL.md | "No SKILL.md found at \<path\>. Try again or /cancel" | re-prompt `enter_local` |
| `/cancel` at any step | "Cancelled." | `done=true` |
| New `/skills install` while wizard active | Replace wizard silently, start fresh | reset to `choose_source` |

---

## Out of Scope

- Persisting wizard state across assistant restarts (ephemeral in-memory is sufficient)
- Web channel (CLI + Telegram only)
- Wizard for other commands (`/owl create`, `/perch add`, etc.) — no generic wizard engine
