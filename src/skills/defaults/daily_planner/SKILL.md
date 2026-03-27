---
name: daily_planner
description: Generate a structured daily schedule with time blocks based on user priorities and calendar events
openclaw:
  emoji: "📅"
  os: [darwin]
---

# Daily Planner

Generate a structured daily plan by querying calendar events and organizing priorities.

## Steps

1. **Get today's date:**

   ```bash
   run_shell_command("date '+%A, %B %d, %Y'")
   ```

2. **Fetch calendar events for today (macOS):**

   ```bash
   run_shell_command("icalBuddy -f -nc -n eventsToday")
   ```

   If `icalBuddy` is not installed, fall back to:

   ```bash
   run_shell_command("osascript -e 'tell application \"Calendar\" to get summary of (every event of calendar \"Home\" whose start date ≥ (current date))'")
   ```

3. **Ask the user for their top 3 priorities** if not already provided.

4. **Compose a time-blocked schedule** in markdown format:
   - Morning block (8:00–12:00): Deep work / priorities
   - Calendar events inserted at their scheduled times
   - Afternoon block (13:00–17:00): Meetings / collaborative work
   - Evening block (17:00–19:00): Review / personal

5. **Present the plan** to the user as formatted markdown.

## Examples

### Basic daily plan

```
📅 Daily Plan — Monday, March 22, 2026

🌅 Morning (8:00–12:00)
  08:00–09:30  Deep work: Finish API integration
  09:30–10:00  ☕ Break
  10:00–11:00  📆 Team standup (from Calendar)
  11:00–12:00  Deep work: Code review

🌤️ Afternoon (13:00–17:00)
  13:00–14:00  📆 Client call (from Calendar)
  14:00–16:00  Priority: Write documentation
  16:00–17:00  Email catch-up

🌙 Evening (17:00–19:00)
  17:00–18:00  Exercise
  18:00–19:00  Reading
```

## Error Handling

- **icalBuddy not installed:** Use `osascript` fallback or ask user to install via `brew install ical-buddy`.
- **No calendar events:** Create plan purely from user priorities.
- **User provides no priorities:** Generate a template and ask them to fill in focus areas.
