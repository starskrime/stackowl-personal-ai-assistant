---
name: compose_announcement
description: Draft a formal announcement for teams, clients, or public communication with appropriate tone and structure
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📢"
parameters:
  audience:
    type: string
    description: "Target audience (team, clients, public)"
  topic:
    type: string
    description: "The news or announcement topic"
  tone:
    type: string
    description: "Tone (formal, celebratory, urgent)"
    default: "formal"
  channel:
    type: string
    description: "Distribution channel (email, slack, blog)"
    default: "email"
required: [audience, topic]
steps:
  - id: draft_announcement
    type: llm
    prompt: "Draft a {{tone}} announcement for {{audience}} about: {{topic}}. Channel: {{channel}}.\n\nFormat with:\n- Clear subject line / headline\n- Context: why this matters\n- Details: what is changing / happening\n- Impact: how it affects the audience\n- Next steps / call-to-action\n- Contact for questions\n\nKeep it professional and appropriately {{tone}}."
    depends_on: []
  - id: present_draft
    type: llm
    prompt: "Format the announcement nicely for {{channel}} distribution.\n\n{{draft_announcement.output}}"
    depends_on: [draft_announcement]
    inputs: [draft_announcement.output]
---

# Compose Announcement

Draft professional announcements for various audiences.

## Steps

1. **Collect details:**
   - Audience (team, clients, public)
   - Topic/news to announce
   - Tone (formal, celebratory, urgent)
   - Distribution channel (email, Slack, blog)

2. **Draft the announcement** following best practices:
   - Clear subject line / headline
   - Context: why this matters
   - Details: what is changing / happening
   - Impact: how it affects the audience
   - Next steps / call-to-action
   - Contact for questions

3. **Present draft** for user review and editing.

4. **Send via appropriate channel** (email, Slack, etc.).

## Examples

### Team announcement

```
audience="team"
topic="New Feature Launch — Project Phoenix"
tone="celebratory"
channel="slack"
```

## Error Handling

- **No audience specified:** Ask user who the announcement is for.
- **Sensitive topic:** Flag and suggest review by a second person before sending.
