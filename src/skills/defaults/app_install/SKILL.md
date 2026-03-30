---
name: app_install
description: Install, uninstall, and manage macOS applications from App Store, Homebrew, or direct downloads
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📱"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: install, uninstall, list, search, or update"
    default: "list"
  app_name:
    type: string
    description: "Application name or bundle ID"
  source:
    type: string
    description: "Source: appstore, brew, or direct"
    default: "brew"
  path:
    type: string
    description: "Path to .app file or dmg (for direct install)"
required: []
steps:
  - id: list_apps
    tool: ShellTool
    args:
      command: "ls /Applications | head -50"
      mode: "local"
    timeout_ms: 10000
  - id: list_brews
    tool: ShellTool
    args:
      command: "brew list --cask 2>/dev/null | head -50"
      mode: "local"
    timeout_ms: 10000
  - id: search_brew
    tool: ShellTool
    args:
      command: "brew search '{{app_name}}' 2>/dev/null | head -20"
      mode: "local"
    timeout_ms: 15000
  - id: search_appstore
    tool: ShellTool
    args:
      command: "mdfind 'kMDItemKind == \"Application\"' 2>/dev/null | grep -i '{{app_name}}' | head -10"
      mode: "local"
    timeout_ms: 15000
  - id: install_brew
    tool: ShellTool
    args:
      command: "brew install --cask '{{app_name}}' --no-quarantine"
      mode: "local"
    timeout_ms: 300000
  - id: install_appstore
    tool: ShellTool
    args:
      command: "open -a 'App Store' && echo 'Please search for {{app_name}} in App Store'"
      mode: "local"
    timeout_ms: 10000
  - id: uninstall_brew
    tool: ShellTool
    args:
      command: "brew uninstall --cask '{{app_name}}'"
      mode: "local"
    timeout_ms: 60000
  - id: uninstall_app
    tool: ShellTool
    args:
      command: "rm -rf '/Applications/{{app_name}}.app' && echo 'Removed from Applications'"
      mode: "local"
    timeout_ms: 10000
  - id: update_brew
    tool: ShellTool
    args:
      command: "brew update && brew upgrade --cask '{{app_name}}'"
      mode: "local"
    timeout_ms: 300000
  - id: app_info
    tool: ShellTool
    args:
      command: "mdls '/Applications/{{app_name}}.app' 2>/dev/null | grep -E 'kMDItemName|kMDItemVersion|kMDItemKind' || echo 'Not found in Applications'"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "App management action: '{{action}}'\n\n{{#if_eq action 'list'}}Installed apps:\n{{list_apps.output}}\n\nBrew casks:\n{{list_brews.output}}{{/if_eq}}\n{{#if_eq action 'search'}}Brew results:\n{{search_brew.output}}\n\nSystem results:\n{{search_appstore.output}}{{/if_eq}}\n{{#if_eq action 'install'}}Installing {{app_name}} from {{source}}{{/if_eq}}"
    depends_on: [list_apps]
    inputs: [list_apps.output, list_brews.output, search_brew.output]
---

# App Install

Install, uninstall, and manage macOS applications.

## Usage

List installed apps:
```
/app_install
```

Search for an app:
```
action=search
app_name=slack
```

Install from Homebrew:
```
action=install
app_name=slack
source=brew
```

Install from App Store:
```
action=install
app_name=Slack
source=appstore
```

Uninstall:
```
action=uninstall
app_name=slack
source=brew
```

Update:
```
action=update
app_name=slack
```

## Actions

- **list**: Show installed applications
- **search**: Search for an app
- **install**: Install an application
- **uninstall**: Remove an application
- **update**: Update an installed app
- **info**: Get app metadata

## Sources

- **brew**: Homebrew Cask (default)
- **appstore**: Mac App Store
- **direct**: Direct .app or .dmg file

## Examples

### List apps
```
action=list
```

### Install Chrome
```
action=install
app_name=google-chrome
source=brew
```

### Remove app
```
action=uninstall
app_name=Slack
source=brew
```

## Notes

- Homebrew requires command line tools
- App Store installs require user interaction
- Some apps need sudo for system-wide install