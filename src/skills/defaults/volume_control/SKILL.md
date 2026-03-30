---
name: volume_control
description: Get or set the system volume level and toggle mute on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔊"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: get, set, mute, or unmute"
    default: "get"
  level:
    type: number
    description: "Volume level 0-100 (for set action)"
required: []
steps:
  - id: get_volume
    tool: ShellTool
    args:
      command: "osascript -e 'output volume of (get volume settings)'"
      mode: "local"
    timeout_ms: 5000
  - id: get_mute_status
    tool: ShellTool
    args:
      command: "osascript -e 'output muted of (get volume settings)'"
      mode: "local"
    timeout_ms: 5000
  - id: set_volume
    tool: ShellTool
    args:
      command: "osascript -e 'set volume output volume {{level}}'"
      mode: "local"
    timeout_ms: 5000
  - id: mute_audio
    tool: ShellTool
    args:
      command: "osascript -e 'set volume output muted true'"
      mode: "local"
    timeout_ms: 5000
  - id: unmute_audio
    tool: ShellTool
    args:
      command: "osascript -e 'set volume output muted false'"
      mode: "local"
    timeout_ms: 5000
  - id: verify_volume
    tool: ShellTool
    args:
      command: "osascript -e 'output volume of (get volume settings)'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Volume control - action: '{{action}}'\n\nCurrent volume: {{get_volume.output}}%\nMuted: {{get_mute_status.output}}\n\n{{#if_eq action 'set'}}Set to: {{level}}%\nNew level: {{verify_volume.output}}{{/if_eq}}\n{{#if_eq action 'mute'}}Muted: true{{/if_eq}}\n{{#if_eq action 'unmute'}}Muted: false{{/if_eq}}"
    depends_on: [get_volume]
    inputs: [get_volume.output, get_mute_status.output, verify_volume.output]
---

# Volume Control

Get or set macOS system volume.

## Usage

Get current volume:
```
/volume_control
```

Set to 50%:
```
action=set
level=50
```

Mute:
```
action=mute
```

Unmute:
```
action=unmute
```

## Actions

- **get** (default): Show current volume level
- **set**: Set volume to specific level (0-100)
- **mute**: Mute audio output
- **unmute**: Unmute audio output

## Examples

### Get current level
```
action=get
```

### Set to 75%
```
action=set
level=75
```

### Mute
```
action=mute
```

## Notes

- Volume range: 0-100
- Changes are immediate
- Affects all system audio