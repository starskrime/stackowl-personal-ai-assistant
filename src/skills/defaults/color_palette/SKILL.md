---
name: color_palette
description: Generate harmonious color palettes from a base color with hex codes, RGB values, and CSS variables
openclaw:
  emoji: "🎨"
---

# Color Palette Generator

Generate color palettes from a base color.

## Steps

1. **Get base color** from user (hex, name, or description).
2. **Generate palette** using color theory:
   - Complementary (opposite on color wheel)
   - Analogous (adjacent colors)
   - Triadic (three evenly spaced colors)
3. **Present palette** with hex codes, RGB values, and preview:
   ```
   🎨 Palette from #3B82F6:
   Primary:      #3B82F6 (Blue)
   Complement:   #F6923B (Orange)
   Analogous 1:  #3BF6C8 (Teal)
   Analogous 2:  #6B3BF6 (Purple)
   Dark shade:   #1E3A5F
   Light tint:   #DBEAFE
   ```
4. **Generate CSS variables** if requested.

## Examples

### Generate from blue

```
🎨 Base: #3B82F6
  --color-primary: #3B82F6;
  --color-secondary: #F6923B;
```

## Error Handling

- **Invalid hex:** Validate format and suggest correction.
