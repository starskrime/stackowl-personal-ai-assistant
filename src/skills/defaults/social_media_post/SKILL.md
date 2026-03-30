---
name: social_media_post
description: Create optimized social media posts for Twitter/X, LinkedIn, or Instagram with hashtags and engagement hooks
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📱"
parameters:
  platform:
    type: string
    description: "Target platform: twitter, linkedin, instagram"
    default: "twitter"
  topic:
    type: string
    description: "The main topic or key points for the post"
  tone:
    type: string
    description: "Tone: professional, casual, humorous, inspirational"
    default: "casual"
  include_cta:
    type: boolean
    description: "Include a call-to-action"
    default: true
required: [topic]
steps:
  - id: draft_twitter
    type: llm
    prompt: "Draft a Twitter/X post (280 chars max) about: {{topic}}\n\nRequirements:\n- Punchy opening hook in first line\n- Relevant hashtags (3-5)\n- Include CTA if include_cta=true\n- Tone: {{tone}}\n\nFormat with emojis."
    depends_on: []
    inputs: [topic, tone, include_cta]
  - id: draft_linkedin
    type: llm
    prompt: "Draft a LinkedIn post (1300 chars max) about: {{topic}}\n\nRequirements:\n- Professional tone\n- Include insights/thought leadership\n- 2-3 relevant hashtags\n- Include CTA if include_cta=true\n\nFormat with line breaks and emojis for readability."
    depends_on: []
    inputs: [topic, tone, include_cta]
  - id: draft_instagram
    type: llm
    prompt: "Draft an Instagram caption about: {{topic}}\n\nRequirements:\n- Visual-first storytelling approach\n- 30 hashtags max (in comments style)\n- Include CTA if include_cta=true\n- Tone: {{tone}}\n\nUse emojis and line breaks for visual appeal."
    depends_on: []
    inputs: [topic, tone, include_cta]
  - id: present_posts
    type: llm
    prompt: "Present all three platform drafts clearly labeled. Offer variations or adaptations.\n\nTwitter: {{draft_twitter.output}}\nLinkedIn: {{draft_linkedin.output}}\nInstagram: {{draft_instagram.output}}"
    depends_on: [draft_twitter, draft_linkedin, draft_instagram]
    inputs: [draft_twitter.output, draft_linkedin.output, draft_instagram.output]
---

# Create Social Media Post

Draft platform-optimized social media content.

## Usage

```bash
/social_media_post platform=twitter topic="Just launched StackOwl v2.0!"
/social_media_post platform=linkedin topic="My thoughts on AI in 2026" tone=professional
```

## Parameters

- **platform**: Target platform: twitter, linkedin, instagram (default: twitter)
- **topic**: The main topic or key points for the post (required)
- **tone**: Tone: professional, casual, humorous, inspirational (default: casual)
- **include_cta**: Include a call-to-action (default: true)

## Examples

### Twitter post about a product launch

```
🚀 Just launched StackOwl v2.0!

Your personal AI assistant just got 100 new skills:
✅ Code review
✅ Daily planning
✅ Research automation

Try it now → github.com/stackowl

#AI #PersonalAssistant #OpenSource #DevTools
```

## Error Handling

- **Exceeds character limit:** Shorten and present alternatives.
- **No platform specified:** Draft for Twitter (shortest format) and offer to adapt.
