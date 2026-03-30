---
name: daily_planner
description: Generate a structured daily schedule with time blocks based on user priorities and calendar events
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📅"
  os: [darwin]
parameters:
  priorities:
    type: string
    description: "Top 3 priorities for today (comma-separated)"
    default: ""
required: []
steps:
  - id: get_date
    tool: ShellTool
    args:
      command: "date '+%A, %B %d, %Y'"
      mode: "local"
    timeout_ms: 5000
  - id: get_calendar_events
    tool: ShellTool
    args:
      command: "icalBuddy -f -nc -n eventsToday 2>/dev/null || osascript -e 'tell application \"Calendar\" to get summary of (every event of calendar \"Home\" whose start date ≥ (current date))' 2>/dev/null || echo 'No calendar access or no events'"
      mode: "local"
    timeout_ms: 10000
  - id: generate_plan
    type: llm
    prompt: "Create a daily plan for {{get_date.stdout}}.\n\nCalendar events:\n{{get_calendar_events.stdout}}\n\nUser priorities:\n{{priorities}}\n\nGenerate a time-blocked schedule in markdown with:\n- Morning block (8:00–12:00): Deep work / priorities\n- Calendar events inserted at their scheduled times\n- Afternoon block (13:00–17:00): Meetings / collaborative work\n- Evening block (17:00–19:00): Review / personal\n\nUse emoji for time blocks and activities."
    depends_on: [get_date, get_calendar_events]
    inputs: [get_date.stdout, get_calendar_events.stdout]
---

# Daily Planner

Generate a structured daily plan by querying calendar events and organizing priorities.

## Steps

1. **Get today's date:**

   ```bash
   date '+%A, %B %d, %Y'
   ```

2. **Fetch calendar events for today (macOS):**

   ```bash
   icalBuddy -f -nc -n eventsToday
   ```

   If `icalBuddy` is not installed, fall back to:

   ```bash
   osascript -e 'tell application "Calendar" to get summary of (every event of calendar "Home" whose start date ≥ (current date))'
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
priorities="Finish API integration, Code review, Write documentation"
```

## Error Handling

- **icalBuddy not installed:** Use `osascript` fallback or ask user to install via `brew install ical-buddy`.
- **No calendar events:** Create plan purely from user priorities.
- **User provides no priorities:** Generate a template and ask them to fill in focus areas.
