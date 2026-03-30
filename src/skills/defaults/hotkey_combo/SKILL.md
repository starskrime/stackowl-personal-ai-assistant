---
name: hotkey_combo
description: Execute powerful keyboard shortcut combinations for system control, app switching, and workflow automation
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎹"
  os: [darwin]
parameters:
  combo:
    type: string
    description: "Hotkey name: copy, paste, select_all, save, undo, redo, find, new_tab, close_tab, screenshot, spotlight, force_quit"
  custom_key:
    type: string
    description: "Custom key (for custom combos)"
  modifiers:
    type: string
    description: "Modifiers: cmd, shift, opt, ctrl"
    default: "cmd"
required: [combo]
steps:
  - id: combo_copy
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"c\" using command down' && echo 'Copied'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_paste
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"v\" using command down' && echo 'Pasted'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_select_all
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"a\" using command down' && echo 'Selected all'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_save
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"s\" using command down' && echo 'Saved'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_undo
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"z\" using command down' && echo 'Undone'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_redo
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"z\" using {command down, shift down}' && echo 'Redone'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_find
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"f\" using command down' && echo 'Find opened'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_new_tab
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"t\" using command down' && echo 'New tab'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_close_tab
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"w\" using command down' && echo 'Tab closed'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_screenshot
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"3\" using {command down, shift down}' && echo 'Screenshot'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_spotlight
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \" \" using command down' && echo 'Spotlight opened'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_force_quit
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"Escape\" using {command down, option down, control down}' && echo 'Force Quit opened'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_minimize
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"m\" using command down' && echo 'Minimized'"
      mode: "local"
    timeout_ms: 5000
  - id: combo_switch_app
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"Tab\" using command down' && echo 'App switched'"
      mode: "local"
    timeout_ms: 5000
  - id: custom_hotkey
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{custom_key}}\" using {{modifiers}} down' && echo 'Custom hotkey executed'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Hotkey executed: '{{combo}}'\n\nDone."
    depends_on: [combo_copy]
    inputs: [combo_copy.output]
---

# Hotkey Combo

Execute keyboard shortcuts instantly.

## Usage

Copy:
```
combo=copy
```

Paste:
```
combo=paste
```

Undo:
```
combo=undo
```

## Available Combos

| Combo | Action | Keys |
|-------|--------|------|
| copy | Copy | ⌘C |
| paste | Paste | ⌘V |
| select_all | Select All | ⌘A |
| save | Save | ⌘S |
| undo | Undo | ⌘Z |
| redo | Redo | ⌘⇧Z |
| find | Find | ⌘F |
| new_tab | New Tab | ⌘T |
| close_tab | Close Tab | ⌘W |
| screenshot | Screenshot | ⌘⇧3 |
| spotlight | Spotlight | ⌘Space |
| force_quit | Force Quit | ⌃⌥⌘Esc |
| minimize | Minimize | ⌘M |
| switch_app | Switch App | ⌘Tab |

## Custom Combo

```
combo=custom
custom_key=n
modifiers=cmd,shift
```

## Examples

### Quick copy
```
combo=copy
```

### New browser tab
```
combo=new_tab
```

### Force quit frozen app
```
combo=force_quit
```

## Notes

- Works in any application
- Use custom for any key combination
- Can chain multiple combos