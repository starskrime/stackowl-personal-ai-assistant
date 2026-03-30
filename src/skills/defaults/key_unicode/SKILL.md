---
name: key_unicode
description: Type unicode characters, emoji, special symbols, and accented characters that aren't on keyboard
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔣"
parameters:
  char:
    type: string
    description: "Unicode character, emoji, or symbol to type"
  category:
    type: string
    description: "Category: emoji, math, arrows, currency, symbols, accented"
    default: "emoji"
required: [char]
steps:
  - id: type_emoji
    tool: ShellTool
    args:
      command: "echo '{{char}}' | pbcopy && osascript -e 'tell application \"System Events\" to keystroke \"v\" using command down'"
      mode: "local"
    timeout_ms: 5000
  - id: type_unicode_char
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; c=\"{{char}}\"; [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, ord(c), True)) and Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, ord(c), False)) for c in c]' 2>/dev/null || echo '{{char}}' | pbcopy"
      mode: "local"
    timeout_ms: 5000
  - id: clipboard_method
    tool: ShellTool
    args:
      command: "echo -n '{{char}}' | pbcopy && osascript -e 'tell application \"System Events\" to keystroke \"v\" using command down' && echo 'Typed: {{char}}'"
      mode: "local"
    timeout_ms: 5000
  - id: show_emoji_picker
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \" \" using {command down, control down}' && echo 'Emoji picker opened'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Unicode character: '{{char}}'\n\nTyped successfully."
    depends_on: [clipboard_method]
    inputs: [clipboard_method.output]
---

# Unicode Characters

Type emoji, symbols, and special characters.

## Usage

Type an emoji:
```
char=😀
```

Type a math symbol:
```
char=∞
category=math
```

Open emoji picker:
```
char=emoji_picker
```

## Categories

- **emoji**: 😀 😍 🚀 ⭐
- **math**: ∞ ÷ × ± √ π
- **arrows**: → ← ↑ ↓ ↔ ⇄
- **currency**: $ € £ ¥ ₹
- **symbols**: © ® ™ § ¶
- **accented**: á é í ó ñ ü

## Common Emoji

```
😀 grin    😍 love   🚀 rocket  ⭐ star
👍 thumbs  👋 wave   🎉 party  🔥 fire
💡 idea    ✅ check  ❌ cross  ⚠️ warn
📁 folder  📄 doc    💾 save   🔍 search
```

## Examples

### Type star rating
```
char=⭐⭐⭐⭐⭐
```

### Type arrows
```
char=→ ↓ ← ↑
```

### Type accented
```
char=Hola
category=accented
```

## Notes

- Uses clipboard + paste method
- Ctrl+Cmd+Space opens emoji picker
- Works in any text field