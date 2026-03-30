---
name: dictation_voice
description: Convert speech to text using macOS Dictation feature, with support for continuous dictation and commands
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎤"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: start, stop, or transcribe"
    default: "transcribe"
  duration:
    type: number
    description: "Dictation duration in seconds"
    default: 30
  language:
    type: string
    description: "Language code (en-US, es-ES, fr-FR, etc.)"
    default: "en-US"
  output_format:
    type: string
    description: "Output: text, clipboard, or file"
    default: "text"
  output_file:
    type: string
    description: "Output file path (for file format)"
    default: "~/Desktop/dictation_$(date +%Y%m%d_%H%M%S).txt"
required: []
steps:
  - id: check_dictation
    tool: ShellTool
    args:
      command: "defaults read com.apple.speech.recognition.assistant enableDictation 2>/dev/null || echo 'Dictation may be disabled'"
      mode: "local"
    timeout_ms: 5000
  - id: enable_dictation
    tool: ShellTool
    args:
      command: "defaults write com.apple.speech.recognition.assistant enableDictation 1 && echo 'Dictation enabled'"
      mode: "local"
    timeout_ms: 5000
  - id: start_dictation
    tool: ShellTool
    args:
      command: "open -a 'Digital Hub' && echo 'Dictation started - speak now'"
      mode: "local"
    timeout_ms: 5000
  - id: stop_dictation
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 36' && echo 'Dictation stopped'"
      mode: "local"
    timeout_ms: 5000
  - id: read_clipboard
    tool: ShellTool
    args:
      command: "pbpaste"
      mode: "local"
    timeout_ms: 5000
  - id: save_to_file
    tool: ShellTool
    args:
      command: "pbpaste > '{{output_file}}' && echo 'Saved to {{output_file}}'"
      mode: "local"
    timeout_ms: 5000
  - id: open_dictation
    tool: ShellTool
    args:
      command: "open -a 'System Preferences' && echo 'Opening Keyboard settings for Dictation'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Dictation result:\n\nClipboard content:\n{{read_clipboard.output}}\n\nStatus: Dictation completed"
    depends_on: [read_clipboard]
    inputs: [read_clipboard.output]
---

# Dictation Voice

Convert speech to text using macOS Dictation.

## Usage

Transcribe from clipboard:
```
/dictation_voice
```

Enable dictation:
```
action=enable
```

Open dictation settings:
```
action=start
```

## Actions

- **transcribe**: Get dictation from clipboard (after using Dictation)
- **start**: Open System Settings for dictation setup
- **stop**: Stop ongoing dictation
- **enable**: Enable macOS Dictation feature

## Parameters

- **duration**: Recording duration in seconds
- **language**: Language code (en-US, es-ES, fr-FR, de-DE, etc.)
- **output_format**: Where to save result (text, clipboard, file)

## How to Use

1. Press **Fn** (or Globe key on newer Macs) twice to start dictation
2. Speak your text
3. Press Fn again to stop
4. Run this skill to retrieve the text

## Examples

### Start dictation setup
```
action=start
```

### Enable dictation
```
action=enable
```

### Save dictation to file
```
action=transcribe
output_format=file
output_file=~/Documents/meeting_notes.txt
```

## Notes

- Requires macOS Dictation to be enabled
- System Settings > Keyboard > Dictation
- Requires microphone permission
- Best results in quiet environment