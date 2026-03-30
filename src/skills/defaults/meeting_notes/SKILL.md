---
name: meeting_notes
description: Capture, structure, and save meeting notes with action items, decisions, and attendee tracking
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📝"
parameters:
  title:
    type: string
    description: "Meeting title or topic"
  attendees:
    type: string
    description: "Comma-separated list of attendees"
  raw_notes:
    type: string
    description: "Raw notes or transcript"
required: [title]
steps:
  - id: create_meetings_dir
    tool: ShellTool
    args:
      command: "mkdir -p ~/Documents/meetings"
      mode: "local"
    timeout_ms: 5000
  - id: generate_notes
    tool: WriteFileTool
    args:
      path: "~/Documents/meetings/$(date +%Y-%m-%d)_{{title}}.md"
      content: "# Meeting: {{title}}\n\n**Date:** $(date +%Y-%m-%d)\n**Attendees:** {{attendees}}\n\n## Key Discussion Points\n\n- \n\n## Decisions Made\n\n- \n\n## Action Items\n\n- [ ]  — Owner:  — Due: \n\n## Next Steps\n\n- \n"
  - id: confirm_save
    tool: ShellTool
    args:
      command: "ls -la ~/Documents/meetings/$(date +%Y-%m-%d)_{{title}}.md"
      mode: "local"
    timeout_ms: 5000
---

# Meeting Notes

Structure and save meeting notes from raw input provided by the user.

## Usage

```bash
/meeting_notes title=<title> attendees=<names> raw_notes=<notes>
```

## Parameters

- **title**: Meeting title or topic
- **attendees**: Comma-separated list of attendees
- **raw_notes**: Raw notes or transcript

## Examples

### Save meeting notes

```
title=Sprint Review
attendees=Alice, Bob, Charlie
raw_notes=Discussed the new feature roadmap...
```

## Error Handling

- **No attendees specified:** Mark as "Attendees: Not recorded" and continue.
- **Directory doesn't exist:** Create it with `mkdir -p`.
- **User provides raw audio transcript:** Summarize key points, extract action items, discard filler words.
