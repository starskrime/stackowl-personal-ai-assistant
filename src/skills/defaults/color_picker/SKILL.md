---
name: color_picker
description: Pick colors from screen, get color values in HEX, RGB, HSL formats, and generate color palettes
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎨"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: pick, palette, convert, generate"
    default: "pick"
  x:
    type: number
    description: "X coordinate for color pick"
  y:
    type: number
    description: "Y coordinate for color pick"
  color:
    type: string
    description: "Color value to convert"
    default: "#3498db"
  count:
    type: number
    description: "Number of colors for palette"
    default: 5
required: []
steps:
  - id: pick_color
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c '\nimport Quartz, os\nfrom AppKit import NSScreen\np = Quartz.NSEvent.mouseLocation()\ns = NSScreen.mainScreen().frame()\nx, y = int(p.x), int(s.size.height - p.y)\ne = Quartz.CGEventCreateMouseEvent(None, 1, Quartz.CGPoint(x, y), 0)\nprint(f\"Pick color at ({x}, {y})\")\n'"
      mode: "local"
    timeout_ms: 5000
  - id: eyedropper
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"c\" using {command down, option down}' && echo 'Color picked - check clipboard'"
      mode: "local"
    timeout_ms: 5000
  - id: open_color_picker
    tool: ShellTool
    args:
      command: "open -a 'Digital Color Meter' && echo 'Color Meter opened'"
      mode: "local"
    timeout_ms: 5000
  - id: screenshot_color
    tool: ShellTool
    args:
      command: "screencapture -x /tmp/colorpick.png && /usr/bin/python3 -c 'from PIL import Image; img=Image.open(\"/tmp/colorpick.png\"); print(img.getpixel(({{x}}, {{y}})))' 2>/dev/null || echo 'Install PIL for pixel color'"
      mode: "local"
    timeout_ms: 10000
  - id: convert_hex_rgb
    tool: ShellTool
    args:
      command: "python3 -c 'c=\"{{color}}\".strip(\"#\"); rgb=tuple(int(c[i:i+2],16) for i in (0,2,4)); print(f\"HEX: #{{c.upper()}}\\nRGB: rgb({{rgb[0]}}, {{rgb[1]}}, {{rgb[2]}})\\nHSL: { {h} } \")'"
      mode: "local"
    timeout_ms: 5000
  - id: generate_palette
    tool: ShellTool
    args:
      command: "python3 -c '\nimport colorsys\nbase = \"{{color}}\".strip(\"#\") or \"3498db\"\nr = int(base[0:2], 16)/255\ng = int(base[2:4], 16)/255\nb = int(base[4:6], 16)/255\nfor i in range({{count}}):\n    h = (i / {{count}}) * 360\n    s = 0.7 + (i % 3) * 0.1\n    v = 0.9 - (i % 2) * 0.2\n    rgb = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h/360, s, v))\n    print(f\"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}\")\n'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Color picker action: '{{action}}'\n\nColor: {{color}}\n\nPalette generated."
    depends_on: [pick_color]
    inputs: [pick_color.output]
---

# Color Picker

Pick colors from screen and work with colors.

## Usage

Open color picker:
```
/color_picker
```

Pick from coordinates:
```
action=pick
x=500
y=300
```

Convert color:
```
action=convert
color=#3498db
```

Generate palette:
```
action=palette
color=#3498db
count=5
```

## Actions

- **pick**: Pick color at cursor or coordinates
- **convert**: Convert between HEX, RGB, HSL
- **palette**: Generate color palette
- **generate**: Generate from base color

## Examples

### Pick from screen
```
action=pick
x=100
y=200
```

### Convert HEX to RGB
```
action=convert
color=#FF5733
```

### Generate 5-tone palette
```
action=palette
color=#3498db
count=5
```

## Color Formats

| Format | Example |
|--------|---------|
| HEX | #3498db |
| RGB | rgb(52, 152, 219) |
| HSL | hsl(204, 72%, 53%) |
| Swift | UIColor(0.2, 0.6, 0.86) |
| CSS | rgb(52, 152, 219) |

## macOS Color Tools

- **Digital Color Meter**: Built-in app for precise picking
- **⌘⇧C**: Copy color value in Color Meter
- **⌘C**: Copy color as Hex