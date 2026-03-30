---
name: system_update
description: Check for available macOS system updates and Homebrew package updates
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔄"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: check, install, or brew"
    default: "check"
steps:
  - id: check_softwareupdate
    tool: ShellTool
    args:
      command: "softwareupdate --list 2>&1"
      mode: "local"
    timeout_ms: 30000
  - id: check_brew
    tool: ShellTool
    args:
      command: "brew update && brew outdated 2>&1"
      mode: "local"
    timeout_ms: 60000
  - id: install_updates
    tool: ShellTool
    args:
      command: "softwareupdate --install --all 2>&1"
      mode: "local"
    timeout_ms: 300000
  - id: upgrade_brew
    tool: ShellTool
    args:
      command: "brew upgrade 2>&1"
      mode: "local"
    timeout_ms: 300000
  - id: analyze
    type: llm
    prompt: "System update check:\n\nmacOS updates:\n{{check_softwareupdate.output}}\n\nHomebrew updates:\n{{check_brew.output}}\n\nProvide a summary of available updates."
    depends_on: [check_softwareupdate]
    inputs: [check_softwareupdate.output, check_brew.output]
---

# System Update Check

Check for macOS and Homebrew updates.

## Usage

Check for updates:
```
/system_update
```

Install all updates:
```
action=install
```

Check Homebrew only:
```
action=brew
```

## Actions

- **check** (default): List available updates
- **install**: Install macOS updates
- **brew**: Check Homebrew updates

## Examples

### Check for updates
```
action=check
```

### Install all macOS updates
```
action=install
```

### Check Homebrew packages
```
action=brew
```

## Notes

- Some updates require restart
- Run with sudo for system-wide updates
- Homebrew is optional (skipped if not installed)