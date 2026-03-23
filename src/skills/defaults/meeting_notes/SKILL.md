---
name: meeting_notes
description: Capture, structure, and save meeting notes with action items, decisions, and attendee tracking
openclaw:
  emoji: "📝"
---

# Meeting Notes

Structure and save meeting notes from raw input provided by the user.

## Steps

1. **Collect meeting details from user:**
   - Meeting title / topic
   - Attendees
   - Raw notes or transcript

2. **Structure the notes** into a standard template:
   ```markdown
   # Meeting: <title>
   **Date:** <today's date>
   **Attendees:** <names>

   ## Key Discussion Points
   - <point 1>
   - <point 2>

   ## Decisions Made
   - <decision 1>

   ## Action Items
   - [ ] <action> — Owner: <name> — Due: <date>

   ## Next Steps
   - <next meeting date/topic>
   ```

3. **Save the notes:**
   ```bash
   write_file("~/Documents/meetings/<date>_<title>.md", "<structured notes>")
   ```

4. **Confirm** the file was saved and offer to send a summary to attendees via email.

## Examples

### Save meeting notes
```bash
run_shell_command("mkdir -p ~/Documents/meetings")
write_file("~/Documents/meetings/2026-03-22_sprint_review.md", "<structured content>")
```

## Error Handling

- **No attendees specified:** Mark as "Attendees: Not recorded" and continue.
- **Directory doesn't exist:** Create it with `mkdir -p`.
- **User provides raw audio transcript:** Summarize key points, extract action items, discard filler words.
