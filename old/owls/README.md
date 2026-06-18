# Owls

Each subfolder contains one owl definition.

## Coordinator owl (Noctua)

There must be exactly one `type: coordinator` owl. It is the default — it handles all messages and routes to specialists when appropriate.

## Specialist owls

Create a folder `owls/{name}/specialized_owl.md` with `type: specialist`. The body of the markdown file is injected as additional context when this owl is active.

Example: `owls/codeExpert/specialized_owl.md`

```yaml
---
name: CodeExpert
type: specialist
emoji: 💻
role: "Senior Software Engineer"
keywords: [code, bug, function, class, typescript, python, debugging, refactor]
domains: [software engineering, debugging, code review]
challengeLevel: high
verbosity: concise
tone: technical
---

Focus on correctness first, performance second. Always suggest tests.
When reviewing code, identify the root cause — not just the symptom.
```

## Session pinning

When a user's message routes to a specialist, the session is pinned to that specialist. All subsequent messages go directly to the specialist — skipping routing — until the user types `@noctua` to return to the coordinator.
