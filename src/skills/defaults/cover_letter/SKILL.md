---
name: cover_letter
description: Draft a personalized cover letter for a job application using the job description and user's experience
openclaw:
  emoji: "💌"
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
web_crawl url="https://example.com/jobs/senior-engineer"
```
## Error Handling
- **No experience provided:** Ask user for their background or resume file.
- **Job page blocked:** Ask user to paste the job description.
