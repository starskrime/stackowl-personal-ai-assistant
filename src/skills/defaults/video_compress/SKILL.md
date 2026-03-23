---
name: video_compress
description: Compress video files to reduce file size while maintaining acceptable quality using ffmpeg
openclaw:
  emoji: "🎬"
---
# Video Compress
Compress videos using ffmpeg.
## Steps
1. **Show original file size:**
   ```bash
   run_shell_command("ls -lh <input.mp4>")
   ```
2. **Compress:**
   ```bash
   run_shell_command("ffmpeg -i <input.mp4> -vcodec libx264 -crf 28 -preset fast <output.mp4>")
   ```
   CRF 18 = high quality, 28 = low quality/small file.
3. **Compare sizes.**
## Examples
### Compress a video
```bash
run_shell_command("ffmpeg -i video.mp4 -vcodec libx264 -crf 24 -preset medium video_compressed.mp4")
```
## Error Handling
- **ffmpeg not installed:** `brew install ffmpeg`.
- **Output larger than input:** Use lower CRF or different preset.
