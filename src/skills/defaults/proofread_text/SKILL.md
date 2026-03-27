---
name: proofread_text
description: Check text for grammar, spelling, punctuation, and style errors with suggested corrections
openclaw:
  emoji: "📝"
---

# Proofread Text

Check text for grammar and style issues.

## Steps

1. **Get text** from user input or file:
   ```bash
   read_file("<file_path>")
   ```
2. **Analyze for:**
   - Spelling errors
   - Grammar mistakes
   - Punctuation issues
   - Passive voice overuse
   - Sentence length / readability
   - Word repetition
3. **Present corrections** as a diff:
   - ~~incorrect~~ → **corrected**
   - Explanation of each fix
4. **Apply fixes** if user approves:
   ```bash
   write_file("<file_path>", "<corrected_text>")
   ```

## Examples

### Proofread a document

```bash
read_file("~/Documents/report.md")
```

## Error Handling

- **Very long document:** Process section by section.
- **Multiple languages:** Ask which language to proofread in.
