---
name: fact_check
description: Verify a claim or statement by searching multiple authoritative sources and presenting evidence
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✅"
parameters:
  claim:
    type: string
    description: "The claim or statement to verify"
required: [claim]
steps:
  - id: search_primary
    tool: duckduckgo_search
    args:
      query: "{{claim}} fact check"
      num: 5
  - id: search_evidence
    tool: duckduckgo_search
    args:
      query: "{{claim}} evidence"
      num: 5
  - id: search_authoritative
    tool: duckduckgo_search
    args:
      query: "{{claim}} snopes OR politifact OR reuters"
      num: 5
  - id: analyze
    type: llm
    prompt: "Based on the search results for the claim '{{claim}}', evaluate the claim and present a verdict: Confirmed (supported by multiple reliable sources), Partially true (context matters), False (contradicted by evidence), or Unverifiable (insufficient evidence). Cite the sources found."
    depends_on: [search_primary, search_evidence, search_authoritative]
    inputs:
      [
        search_primary.output,
        search_evidence.output,
        search_authoritative.output,
      ]
---

# Fact Check

Verify claims using multiple sources.

## Usage

```bash
/fact_check claim="<statement to verify>"
```

## Parameters

- **claim**: The claim or statement to verify

## Examples

### Check a claim

```
claim="does drinking water help weight loss"
```

## Error Handling

- **Controversial topic:** Present multiple perspectives rather than a single verdict.
- **No fact-check sources:** Note this and present raw evidence for user to judge.
