---
name: fact_check
description: Verify a claim or statement by searching multiple authoritative sources and presenting evidence
openclaw:
  emoji: "✅"
---

# Fact Check

Verify claims using multiple sources.

## Steps

1. **Identify the claim** to verify.
2. **Search for evidence:**
   ```
   web_search query="<claim> fact check"
   web_search query="<claim> evidence"
   web_search query="<claim> snopes OR politifact OR reuters"
   ```
3. **Evaluate sources** for reliability and recency.
4. **Present verdict:**
   - ✅ **Confirmed** — supported by multiple reliable sources
   - ⚠️ **Partially true** — context matters
   - ❌ **False** — contradicted by evidence
   - ❓ **Unverifiable** — insufficient evidence
5. **Cite sources** with URLs.

## Examples

### Check a claim

```
web_search query="does drinking water help weight loss fact check"
```

## Error Handling

- **Controversial topic:** Present multiple perspectives rather than a single verdict.
- **No fact-check sources:** Note this and present raw evidence for user to judge.
