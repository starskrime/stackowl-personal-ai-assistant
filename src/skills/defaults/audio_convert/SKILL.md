---
name: audio_convert
description: Convert audio files between formats like MP3, WAV, AAC, and FLAC using ffmpeg
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎵"
parameters:
  input:
    type: string
    description: "Input audio file path"
  output:
    type: string
    description: "Output audio file path"
  format:
    type: string
    description: "Output format: mp3, wav, aac, flac, ogg"
    default: "mp3"
  quality:
    type: string
    description: "Quality setting (varies by format)"
    default: "2"
required: [input, output]
steps:
  - id: check_ffmpeg
    tool: ShellTool
    args:
      command: "which ffmpeg || echo 'NOT_FOUND'"
      mode: "local"
    timeout_ms: 5000
  - id: install_ffmpeg
    tool: ShellTool
    args:
      command: "brew install ffmpeg"
      mode: "local"
    timeout_ms: 120000
    optional: true
  - id: convert_mp3
    tool: ShellTool
    args:
      command: "ffmpeg -i {{input}} -codec:a libmp3lame -qscale:a {{quality}} {{output}}"
      mode: "local"
    timeout_ms: 120000
    optional: true
  - id: convert_wav
    tool: ShellTool
    args:
      command: "ffmpeg -i {{input}} {{output}}"
      mode: "local"
    timeout_ms: 120000
    optional: true
  - id: convert_aac
    tool: ShellTool
    args:
      command: "ffmpeg -i {{input}} -codec:a aac -q:a {{quality}} {{output}}"
      mode: "local"
    timeout_ms: 120000
    optional: true
  - id: convert_flac
    tool: ShellTool
    args:
      command: "ffmpeg -i {{input}} -codec:a flac {{output}}"
      mode: "local"
    timeout_ms: 120000
    optional: true
  - id: convert_ogg
    tool: ShellTool
    args:
      command: "ffmpeg -i {{input}} -codec:a libvorbis -q:a {{quality}} {{output}}"
      mode: "local"
    timeout_ms: 120000
    optional: true
  - id: verify_output
    tool: ShellTool
    args:
      command: "ls -lh {{output}} && ffprobe -hide_banner {{output}} 2>&1 | head -10"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Audio conversion completed.\n\nFFmpeg status: {{check_ffmpeg.output}}\n\nOutput: {{verify_output.output}}\n\nProvide a brief summary of the converted file."
    depends_on: [check_ffmpeg, verify_output]
    inputs: [check_ffmpeg.output, verify_output.output]
---

# Audio Convert

Convert audio files between formats using ffmpeg.

## Usage

```bash
/audio_convert input.wav output.mp3
```

With options:
```
input=audio.wav
output=audio.mp3
format=mp3
quality=2
```

## Supported Formats

- **mp3** - Most compatible, use quality 0-9 (lower = better)
- **wav** - Uncompressed, large files
- **aac** - Apple format, good quality
- **flac** - Lossless compression
- **ogg** - Open format, good for streaming

## Examples

### WAV to MP3
```
input=song.wav
output=song.mp3
format=mp3
quality=2
```

### Convert to AAC
```
input=audio.wav
output=audio.m4a
format=aac
quality=100
```

## Error Handling

- **ffmpeg not installed:** Auto-installs via Homebrew
- **Unsupported format:** Lists supported codecs
- **Large file:** Uses streaming conversion to manage memory