---
name: screen_recording
description: Record screen videos with audio, take screen recordings with selectable region, and manage recordings
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎬"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: start, stop, region, full, audio"
    default: "start"
  path:
    type: string
    description: "Output file path"
    default: "~/Desktop/screen_recording_$(date +%Y%m%d_%H%M%S).mov"
  include_audio:
    type: boolean
    description: "Include system audio"
    default: false
  duration:
    type: number
    description: "Recording duration in seconds (0 = until stopped)"
    default: 0
required: []
steps:
  - id: check_recording
    tool: ShellTool
    args:
      command: "pgrep -l 'screencapture|screenrecord' || echo 'No recording in progress'"
      mode: "local"
    timeout_ms: 5000
  - id: start_recording_full
    tool: ShellTool
    args:
      command: "echo 'Starting QuickTime screen recording...' && open -a QuickTime\ Player && sleep 1 && osascript -e 'tell application \"QuickTime Player\" to activate' -e 'tell application \"System Events\" to keystroke \"n\" using command down' && echo 'Use File > New Screen Recording'"
      mode: "local"
    timeout_ms: 10000
  - id: start_region_recording
    tool: ShellTool
    args:
      command: "echo 'Starting region recording via screencapture...' && screencapture -v -R0,0,1920,1080 '{{path}}' &"
      mode: "local"
    timeout_ms: 5000
  - id: stop_recording
    tool: ShellTool
    args:
      command: "pkill -f 'screencapture' && echo 'Recording stopped'"
      mode: "local"
    timeout_ms: 5000
  - id: list_recordings
    tool: ShellTool
    args:
      command: "ls -lt ~/Desktop/*.mov ~/Desktop/*.mp4 2>/dev/null | head -10"
      mode: "local"
    timeout_ms: 5000
  - id: recording_info
    tool: ShellTool
    args:
      command: "mdls '{{path}}' 2>/dev/null | grep -E 'kMDItemDuration|kMDItemPixelWidth|kMDItemPixelHeight' || echo 'File info unavailable'"
      mode: "local"
    timeout_ms: 5000
  - id: quicktime_record
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"QuickTime Player\" to new screen recording' && echo 'QuickTime recording started'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Screen recording status:\n\n{{check_recording.output}}\n\nRecent recordings:\n{{list_recordings.output}}"
    depends_on: [check_recording]
    inputs: [list_recordings.output]
---

# Screen Recording

Record screen videos on macOS.

## Usage

Start recording:
```
/screen_recording
```

Stop recording:
```
action=stop
```

Record region:
```
action=region
```

## Actions

- **start**: Start full-screen recording
- **stop**: Stop current recording
- **region**: Record selected region
- **full**: Full screen recording
- **audio**: Recording with system audio

## Parameters

- **path**: Output file path
- **include_audio**: Include audio (requires permission)
- **duration**: Max duration in seconds (0 = unlimited)

## Examples

### Start recording
```
action=start
```

### Stop and save
```
action=stop
path=~/Desktop/my_recording.mov
```

### Record 30 seconds
```
action=start
duration=30
path=~/Desktop/short_clip.mov
```

## Output Formats

- **QuickTime**: .mov (best quality)
- **Converted**: .mp4 via HandBrake or ffmpeg

## macOS Built-in

1. **⌘⇧5**: Screen capture menu with recording
2. **QuickTime Player**: File > New Screen Recording
3. **screencapture command**: CLI recording

## Notes

- System audio requires Screen Recording permission
- Microphone requires separate permission
- Videos saved to Desktop by default