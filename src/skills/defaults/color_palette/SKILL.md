---
name: color_palette
description: Generate harmonious color palettes from a base color with hex codes, RGB values, and CSS variables
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎨"
parameters:
  base_color:
    type: string
    description: "Base color in hex format (e.g., #3B82F6) or color name"
  include_css:
    type: boolean
    description: "Include CSS custom properties in output"
    default: false
required: [base_color]
steps:
  - id: generate_palette
    type: llm
    prompt: "Generate a harmonious color palette from base color '{{base_color}}'. Include:\n1. Complementary color (opposite on color wheel)\n2. Analogous colors (adjacent colors)\n3. Triadic colors (three evenly spaced)\n4. Dark shade\n5. Light tint\n\nFor each color provide: hex code, RGB values, and a descriptive name. Also provide CSS custom properties if requested (include_css={{include_css}}). Format as markdown."
    depends_on: []
  - id: present_palette
    type: llm
    prompt: "Format the color palette as a visually appealing markdown presentation with color swatches using emoji or code blocks. Base color: {{base_color}}"
    depends_on: [generate_palette]
    inputs: [generate_palette.output]
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
base_color="#3B82F6"
include_css=true
```

## Error Handling

- **Invalid hex:** Validate format and suggest correction.
