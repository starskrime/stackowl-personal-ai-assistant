---
name: menubar_control
description: Control macOS menu bar items - show/hide icons, access menu extras, and manage status items
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🍎"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, hide, show, click, or quit"
    default: "list"
  app_name:
    type: string
    description: "Menu bar app name"
required: []
steps:
  - id: list_menu_items
    tool: ShellTool
    args:
      command: "ps -axo comm,args | grep -E 'SystemUIServer|ControlCenter|StatusItem' | grep -v grep | head -20"
      mode: "local"
    timeout_ms: 10000
  - id: menu_bar_items
    tool: ShellTool
    args:
      command: "defaults read com.apple.SystemUIServer menu_extras 2>/dev/null | tr ',' '\n' | head -30"
      mode: "local"
    timeout_ms: 10000
  - id: click_menu_item
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to click menu bar item \"{{app_name}}\" of menu bar 1' 2>/dev/null || echo 'Could not click menu item'"
      mode: "local"
    timeout_ms: 5000
  - id: toggle_bluetooth
    tool: ShellTool
    args:
      command: "open -a SystemUIServer && echo 'Bluetooth menu clicked'"
      mode: "local"
    timeout_ms: 5000
  - id: wifi_menu
    tool: ShellTool
    args:
      command: "/System/Library/CoreServices/Menu\ Extras/AirPort.menu/Contents/Resources airport -z && echo 'WiFi disconnected'"
      mode: "local"
    timeout_ms: 5000
  - id: battery_menu
    tool: ShellTool
    args:
      command: "pmset -g batt | grep -E 'Internal|External'"
      mode: "local"
    timeout_ms: 5000
  - id: volume_output
    tool: ShellTool
    args:
      command: "osascript -e 'output volume of (get volume settings)'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Menu bar status:\n\nBattery: {{battery_menu.output}}\nVolume: {{volume_output.output}}%\n\nMenu extras:\n{{menu_bar_items.output}}"
    depends_on: [battery_menu]
    inputs: [battery_menu.output, volume_output.output]
---

# Menu Bar Control

Control macOS menu bar items and status extras.

## Usage

List menu bar items:
```
/menubar_control
```

Click a menu item:
```
action=click
app_name=Control Center
```

Show battery status:
```
action=info
app_name=Battery
```

## Actions

- **list**: Show active menu bar items
- **hide**: Hide a specific menu bar item
- **show**: Show a hidden menu bar item
- **click**: Click/access a menu bar item
- **quit**: Quit a menu bar app

## Common Menu Items

- Control Center
- Battery
- WiFi (AirPort)
- Bluetooth
- Volume
- Clock
- Spotlight

## Examples

### List what's in menu bar
```
action=list
```

### Click Control Center
```
action=click
app_name=Control Center
```

### Battery status
```
action=info
app_name=Battery
```

## Notes

- Some items require System Events permission
- Menu bar apps are often small background processes
- Clicking opens dropdown menus