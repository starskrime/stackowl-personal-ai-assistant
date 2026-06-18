# Organizational Owl Structure — Design Spec

## Overview

Transform StackOwl from a single-agent system into an organizational structure where each specialized owl has:
- A well-defined role and responsibility
- Explicit permissions (tools it can/cannot use)
- Its own AI model and provider configuration
- Credential management (API keys, tokens)
- Skill whitelists

This enables:
- **User independence** - Multiple family members/colleagues each have their own owls
- **Task boundaries** - Owls can only do what they're specialized for
- **Access control** - Tool permissions, data scope, channel restrictions

## Storage Structure

```
workspace/owls/
├── TradingBot/
│   ├── specialized_owl.md   # Role, personality, expertise, permissions, model config
│   └── credentials/
│       └── secrets.md       # API keys, tokens (gitignored)
├── Researcher/
│   ├── specialized_owl.md
│   └── credentials/
│       └── secrets.md
└── Coder/
    ├── specialized_owl.md
    └── credentials/
        └── secrets.md
```

The main/secretary owl (Noctua) continues using the existing `OWL.md` pattern. Only specialized owls get the new folder structure.

## File Formats

### specialized_owl.md

```markdown
# TradingBot

## Identity
name: TradingBot
role: Stock trading assistant
emoji: 📈

## Personality
challengeLevel: high
verbosity: balanced
tone: casual but precise

## Expertise
domains:
  - stock market analysis
  - portfolio management
  - trading strategies

## Model Config
provider: anthropic
model: claude-sonnet-4-20250514
maxTokens: 4096

## Permissions
allowedTools:
  - shell
  - calculator
  - web_search
  - web_crawl
deniedTools:
  - write
  - edit
  - delete
capabilityConstraints:
  - "Cannot execute trades directly"
  - "Cannot access personal finances outside trading accounts"

## Routing Rules
keywords:
  - stock
  - trading
  - portfolio
  - shares
  - market

## Skills
allowed:
  - trading-strategies
  - market-analysis
```

### credentials/secrets.md

```markdown
# TradingBot Credentials

ALPHA_VANTAGE_KEY=your_api_key_here
TRADING_API_TOKEN=your_token_here
```

## Components

### 1. SpecializedOwlRegistry

A new registry class that:
- Scans `workspace/owls/` directory on startup
- Loads `specialized_owl.md` from each subfolder
- Provides lookup by name, expertise domain, routing keywords
- Does NOT load credentials - those are retrieved via tool at runtime

```typescript
class SpecializedOwlRegistry {
  async loadAll(workspacePath: string): Promise<void>
  get(name: string): SpecializedOwlSpec | undefined
  listAll(): SpecializedOwlSpec[]
  getByExpertise(domain: string): SpecializedOwlSpec[]
  getByKeyword(keyword: string): SpecializedOwlSpec[]
}
```

### 2. SpecializedOwlSpec Interface

```typescript
interface SpecializedOwlSpec {
  name: string
  role: string
  emoji: string
  personality: {
    challengeLevel: "low" | "medium" | "high" | "relentless"
    verbosity: "concise" | "balanced" | "verbose"
    tone: string
  }
  expertise: string[]
  model: {
    provider: string
    model: string
    maxTokens?: number
  }
  permissions: {
    allowedTools: string[]
    deniedTools: string[]
    capabilityConstraints: string[]
  }
  routingRules: {
    keywords: string[]
  }
  skills: {
    allowed: string[]
  }
  credentialsPath?: string  // Path to credentials folder
}
```

### 3. CredentialsTool

A new tool that allows owls to retrieve their own credentials at runtime:

```typescript
// Tool definition
{
  name: "credentials_get",
  description: "Retrieve a credential value by key name",
  parameters: {
    key: { type: "string", description: "The credential key to retrieve" }
  }
}
```

- Each specialized owl can only access credentials in its own folder
- Credentials are NOT injected into system prompt (security)
- Owl calls tool when it needs to use an API

### 4. Permission Enforcement

Permissions are **self-restriction** - the owl sees its constraints in system prompt and follows them. The system prompt includes:

```
## Your Constraints
- You are a Stock trading assistant
- You can ONLY use these tools: shell, calculator, web_search, web_crawl
- You must NEVER use these tools: write, edit, delete
- You CANNOT execute trades directly
- You CANNOT access personal finances outside trading accounts
```

### 5. Routing (Secretary Enhancement)

Secretary routes based on multiple signals:
- **Explicit mention**: `@TradingBot what are my AAPL shares?`
- **owl.specialization** (description match)
- **owl.routingRules.keywords** (keyword match - existing implementation)
- **owl.expertise** (domain match)

When routing to a specialized owl:
1. Get base `OwlInstance` from `OwlRegistry`
2. Get `SpecializedOwlSpec` from `SpecializedOwlRegistry`
3. Merge: base capabilities + specialized constraints + model config
4. Set `specialistPrompt` with role and constraints

### 6. Creation Wizard

Interactive CLI wizard that asks questions in order:

```
/specialization create TradingBot

1. Role: "What should this owl do?"
   → Natural language description

2. Personality:
   - Challenge level: low/medium/high/relentless
   - Verbosity: concise/balanced/verbose
   - Tone: casual/formal/professional

3. Expertise:
   - What topics does it know?
   - User enters keywords

4. Permissions (Tools):
   - Allowed tools: select from list
   - Denied tools: select from list

5. Permissions (Capabilities):
   - "What should this owl NEVER do?"
   → Natural language constraints

6. Model Config:
   - Provider: select from available
   - Model: specific model name
   - Max tokens: number

7. Credentials:
   - "Does this owl need API keys?" (y/n)
   - If yes, enter key-value pairs

8. Skills:
   - Suggested skills (auto-detected based on role)
   - User can add/remove

→ Preview specialized_owl.md → Confirm → Create files
```

## Data Flow

### Loading

```
Startup
  ├── OwlRegistry.loadAll() → Load base owls (OWL.md)
  └── SpecializedOwlRegistry.loadAll() → Load specs (specialized_owl.md)

Database (owls table)
  └── Discovery only - names and basic info for Secretary
```

### Routing

```
User message
  ├── Explicit: @TradingBot ...
  │     → Direct invoke, use TradingBot spec
  │
  └── Implicit: "analyze my portfolio"
        SecretaryRouter.match()
          ├── Match keywords → TradingBot
          ├── Match expertise → TradingBot
          └── Match specialization → TradingBot
        → Merge base owl + spec → Activate
```

### Credential Access

```
Owl needs API key
  → credentials_get { key: "ALPHA_VANTAGE_KEY" }
  → Tool reads from workspace/owls/TradingBot/credentials/secrets.md
  → Returns value (never stored, always retrieved on-demand)
```

## Implementation Phases

### Phase 1: Storage & Registry
- Create `SpecializedOwlSpec` interface
- Create `SpecializedOwlRegistry` class
- Create parser for `specialized_owl.md`
- File creation/deletion commands

### Phase 2: Wizard
- Interactive creation wizard
- Question flow (role → personality → expertise → permissions → model → credentials → skills)
- Preview and confirm
- File generation

### Phase 3: Credential Tool
- `credentials_get` tool implementation
- Path isolation (owl can only access its own credentials folder)
- Tool registration per specialized owl

### Phase 4: Routing Enhancement
- Multi-factor routing (keywords, expertise, specialization)
- Explicit mention handling (`@OwlName`)
- Spec merging with base owl

### Phase 5: Constraint Injection
- Add constraints to `specialistPrompt`
- Model config override when activating specialized owl

## Backward Compatibility

- Existing owls (OWL.md pattern) continue to work unchanged
- `owls` table in DB unchanged
- Default/Secretary owl uses existing flow
- Only specialized owls get new structure

## Security Considerations

- Credentials folder is gitignored
- Each owl can only read its own credentials
- No credentials in system prompt
- Credentials retrieved only when tool called
