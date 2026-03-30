---
name: app_launcher
description: Launch, quit, or check if a macOS application is running
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🚀"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action to perform: launch, quit, or status"
    default: "launch"
  app_name:
    type: string
    description: "Name of the application (e.g., Safari, Slack)"
required: [app_name]
steps:
  - id: check_status
    tool: ShellTool
    args:
      command: "pgrep -x '{{app_name}}' && echo 'Running' || echo 'Not running'"
      mode: "local"
    timeout_ms: 5000
  - id: launch_app
    tool: ShellTool
    args:
      command: "open -a '{{app_name}}'"
      mode: "local"
    timeout_ms: 10000
    on_failure: launch_failed
  - id: launch_failed
    tool: ShellTool
    args:
      command: "mdfind 'kMDItemKind == Application' -name '{{app_name}}' | head -5"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: quit_app
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{app_name}}\" to quit'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "The user requested action '{{action}}' for app '{{app_name}}'.\n\nCurrent status: {{check_status.output}}\n\nProvide a brief confirmation of what happened."
    depends_on: [check_status]
    inputs: [check_status.output]
---

# App Launcher

Launch, quit, or check status of macOS applications.

## Usage

Launch an app:
```
/app_launcher Safari
```

Quit an app:
```
/app_launcher --action quit Slack
```

Check status:
```
/app_launcher --action status Safari
```

## Actions

- **launch** (default): Open the application
- **quit**: Close the application
- **status**: Check if the app is running

## Examples

### Launch Safari
```
app_name=Safari
action=launch
```

### Quit Slack
```
app_name=Slack
action=quit
```

## Error Handling

- **App not found:** Suggests similar app names via mdfind
- **App crashed:** Use process_kill skill to force quit