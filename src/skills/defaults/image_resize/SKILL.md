---
name: image_resize
description: Resize, compress, or convert images using macOS built-in tools or ImageMagick
openclaw:
  emoji: "🖼️"
  os: [darwin]
---

# Image Resize

Resize and compress images.

## Steps

1. **Check available tools:**
   ```bash
   run_shell_command("which sips convert 2>/dev/null")
   ```
2. **Resize using sips (macOS built-in):**
   ```bash
   run_shell_command("sips --resampleWidth <width> <input.jpg> --out <output.jpg>")
   ```
3. **Convert format:**
   ```bash
   run_shell_command("sips -s format png <input.jpg> --out <output.png>")
   ```

## Examples

### Resize to 800px wide

```bash
run_shell_command("sips --resampleWidth 800 photo.jpg --out photo_small.jpg")
```

## Error Handling

- **Unsupported format:** Convert to supported format first.
- **sips fails:** Fall back to ImageMagick: `convert <input> -resize <size> <output>`.
