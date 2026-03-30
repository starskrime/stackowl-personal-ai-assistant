---
name: cover_letter
description: Draft a personalized cover letter for a job application using the job description and user's experience
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "💌"
parameters:
  job_url:
    type: string
    description: "URL to the job listing (optional if job_description provided)"
    default: ""
  job_description:
    type: string
    description: "Job description text (use if no URL)"
    default: ""
  company_name:
    type: string
    description: "Company name"
    default: ""
  user_experience:
    type: string
    description: "User's experience/background to highlight"
    default: ""
required: []
steps:
  - id: fetch_job_description
    tool: ShellTool
    args:
      command: "curl -sL '{{job_url}}' 2>/dev/null | python3 -c \"import sys; import re; html=sys.stdin.read(); text=re.sub(r'<[^>]+>', ' ', html); print(' '.join(text.split())[:5000])\" || echo '{{job_description}}'"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: write_cover_letter
    type: llm
    prompt: "Write a personalized cover letter for a job. \n\nCompany: {{company_name}}\n\nJob Description:\n{{#if fetch_job_description.output}}{{fetch_job_description.output}}{{else}}{{job_description}}{{/if}}\n\nCandidate Experience:\n{{user_experience}}\n\nInclude:\n- Opening: Why this role excites you\n- Body: Match experience to job requirements\n- Closing: Call to action\n- Professional tone, 300-400 words\n\nPresent as formatted text ready to use."
    depends_on: [fetch_job_description]
    inputs: [fetch_job_description.output]
---

# Cover Letter Writer

Draft personalized cover letters.

## Steps

1. **Collect inputs:**
   - Job description (URL or pasted text)
   - User's experience/background
   - Company name
2. **If URL provided, fetch job description:**
   ```
   web_crawl url="<job_listing_url>"
   ```
3. **Draft cover letter:**
   - Opening: Why this role excites you
   - Body: Match your experience to requirements
   - Closing: Call to action
   - Professional tone, 300-400 words
4. **Present draft** for review.

## Examples

### Draft from job URL

```
job_url="https://example.com/jobs/senior-engineer"
company_name="Example Corp"
```

## Error Handling

- **No experience provided:** Ask user for their background or resume file.
- **Job page blocked:** Ask user to paste the job description.
