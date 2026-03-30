---
name: video_compress
description: Compress video files to reduce file size while maintaining acceptable quality using ffmpeg
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎬"
parameters:
  input:
    type: string
    description: "Input video file"
  output:
    type: string
    description: "Output video file"
  quality:
    type: number
    description: "Quality 0-100 (lower = smaller file, default: 28)"
    default: 28
  preset:
    type: string
    description: "Encoding speed: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow"
    default: "fast"
required: [input, output]
steps:
  - id: check_input
    tool: ShellTool
    args:
      command: "ls -lh {{input}}"
      mode: "local"
    timeout_ms: 5000
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
  - id: compress_video
    tool: ShellTool
    args:
      command: "ffmpeg -i {{input}} -vcodec libx264 -crf {{quality}} -preset {{preset}} -acodec aac -b:a 128k {{output}}"
      mode: "local"
    timeout_ms: 600000
  - id: check_output
    tool: ShellTool
    args:
      command: "ls -lh {{output}} 2>/dev/null || echo 'Output not created'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Video compression completed:\n\nInput: {{input}}\n{{check_input.output}}\n\nOutput: {{output}}\n{{check_output.output}}\n\nSettings: CRF={{quality}}, preset={{preset}}\n\nProvide a summary of the compression."
    depends_on: [check_input]
    inputs: [check_input.output, check_output.output]
---

# Video Compress

Compress video files using ffmpeg.

## Usage

```bash
/video_compress input.mp4 output.mp4
```

With quality setting:
```
input=video.mp4
output=video_small.mp4
quality=24
```

## Parameters

- **input**: Source video file
- **output**: Destination file
- **quality**: CRF value 0-51 (lower = better quality, default: 28)
- **preset**: Encoding speed (default: fast)

## Quality Guide

- **18-23**: High quality, large file
- **24-28**: Good balance (recommended)
- **29-35**: Smaller file, visible quality loss
- **36+**: Very small, poor quality

## Presets

- **ultrafast**: Largest output, fastest encode
- **fast**: Good balance (default)
- **slow**: Smaller output, slower encode

## Examples

### High quality compress
```
input=video.mp4
output=video_hq.mp4
quality=20
preset=medium
```

### Small file
```
input=video.mp4
output=video_small.mp4
quality=32
preset=ultrafast
```

## Notes

- Requires ffmpeg (auto-installed if missing)
- Output may be larger if already compressed