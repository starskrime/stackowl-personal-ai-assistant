---
name: summarize_thread
description: Summarize a long email thread or chat conversation into key points, decisions, and action items
openclaw:
  emoji: "📌"
---

# Summarize Thread

Condense a long email or chat thread into a structured summary.

## Steps

1. **Receive the thread content** from the user (pasted text, file path, or email).

2. **If provided as a file, read it:**

   ```bash
   read_file("<file_path>")
   ```

3. **Extract and structure:**
   - **Key points** (3–5 bullet points)
   - **Decisions made** (if any)
   - **Action items** with owners
   - **Unresolved questions**
   - **Overall sentiment/tone**

4. **Present the summary** in a clean format.

## Examples

### Summarize from a file

```bash
read_file("~/Downloads/long_email_thread.txt")
```

## Error Handling

- **Thread too long (>10000 chars):** Process in chunks, summarize each, then create meta-summary.
- **No clear action items:** Note "No explicit action items identified."
- **Multiple languages in thread:** Translate non-primary language sections first.
