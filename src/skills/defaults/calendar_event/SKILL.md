---
name: calendar_event
description: Create, list, and manage calendar events in macOS Calendar app via AppleScript
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📅"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: create, list, or delete"
    default: "list"
  title:
    type: string
    description: "Event title"
  start_date:
    type: string
    description: "Start date/time (YYYY-MM-DD HH:MM)"
  end_date:
    type: string
    description: "End date/time (YYYY-MM-DD HH:MM)"
  calendar:
    type: string
    description: "Calendar name"
    default: "Calendar"
  location:
    type: string
    description: "Event location"
  notes:
    type: string
    description: "Event notes/description"
  days:
    type: number
    description: "Days ahead to list (for list action)"
    default: 7
required: []
steps:
  - id: list_calendars
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Calendar\" to get name of calendars' | tr ',' '\n'"
      mode: "local"
    timeout_ms: 10000
  - id: list_events
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Calendar\" to tell calendar \"{{calendar}}\" to get events where (start date > (current date)) and (start date < (current date) + {{days}} * days)' 2>/dev/null | head -50"
      mode: "local"
    timeout_ms: 15000
  - id: create_event
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Calendar\"\n tell calendar \"{{calendar}}\"\n make new event at end with properties {summary:\"{{title}}\", start date:date \"{{start_date}}\", end date:date \"{{end_date}}\"{{#if location}}, location:\"{{location}}\"{{/if}}{{#if notes}}, description:\"{{notes}}\"{{/if}}}\n end tell\n end tell' && echo 'Event created'"
      mode: "local"
    timeout_ms: 15000
  - id: today_events
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Calendar\" to tell calendar \"{{calendar}}\" to get events where (start date > (current date)) and (start date < (current date + 1 * days))' 2>/dev/null | head -20"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Calendar action: '{{action}}'\n\nAvailable calendars:\n{{list_calendars.output}}\n\n{{#if_eq action 'list'}}Upcoming events:\n{{list_events.output}}\n\nToday's events:\n{{today_events.output}}{{/if_eq}}\n{{#if_eq action 'create'}}Event created: {{title}}\nFrom: {{start_date}}\nTo: {{end_date}}{{/if_eq}}"
    depends_on: [list_calendars]
    inputs: [list_calendars.output, list_events.output]
---

# Calendar Event

Create, list, and manage calendar events.

## Usage

List upcoming events:
```
/calendar_event
```

List events in specific calendar:
```
action=list
calendar=Work
days=14
```

Create event:
```
action=create
title=Team Meeting
start_date=2024-03-29 14:00
end_date=2024-03-29 15:00
```

## Actions

- **list**: Show upcoming events
- **create**: Create a new calendar event
- **delete**: Remove an event

## Parameters

- **title**: Event title
- **start_date**: Start (YYYY-MM-DD HH:MM)
- **end_date**: End (YYYY-MM-DD HH:MM)
- **calendar**: Calendar name (default: Calendar)
- **location**: Event location (optional)
- **notes**: Event description (optional)
- **days**: Days ahead to list (default: 7)

## Examples

### Meeting tomorrow
```
action=create
title=Project Review
start_date=2024-03-30 10:00
end_date=2024-03-30 11:00
calendar=Work
location=Conference Room A
```

### List personal calendar
```
action=list
calendar=Personal
days=30
```

## Notes

- Date format: YYYY-MM-DD HH:MM
- 24-hour format required
- Calendar must exist before adding events