---
name: audio_convert
description: Convert audio files between formats like MP3, WAV, AAC, and FLAC using ffmpeg
openclaw:
  emoji: "🎵"
---

# Audio Convert

Convert audio between formats using ffmpeg.

## Steps

1. **Check ffmpeg is available:**
   ```bash
   run_shell_command("which ffmpeg || echo 'ffmpeg not found'")
   ```
2. **Convert:**
   ```bash
   run_shell_command("ffmpeg -i <input.wav> -codec:a libmp3lame -qscale:a 2 <output.mp3>")
   ```
3. **Confirm** with output file size.

## Examples

### WAV to MP3

```bash
run_shell_command("ffmpeg -i audio.wav -codec:a libmp3lame -qscale:a 2 audio.mp3")
```

## Error Handling

- **ffmpeg not installed:** `brew install ffmpeg`.
- **Unsupported format:** List supported codecs with `ffmpeg -codecs`.
