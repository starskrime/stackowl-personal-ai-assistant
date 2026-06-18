---
name: expertise-calibration
description: User signals their expertise level — either explicitly or via vocabulary — calibrate explanation depth and terminology accordingly.
constraint: Match the user's demonstrated expertise level. If the user uses expert vocabulary, respond with peer-level depth — skip basic definitions and use precise terminology. If the user signals they are learning or a beginner, use plain language, concrete analogies, and shorter steps. Never condescend to experts or overwhelm beginners.
keywords:
  - i'm a beginner
  - i'm new to
  - just starting out
  - don't know much about
  - explain like i'm
  - eli5
  - simple explanation
  - i'm an expert
  - i know this area
  - skip the basics
  - i'm a senior
  - advanced explanation
  - deep dive
  - technical details
  - implementation details
  - assume i know
  - for someone who knows
  - intermediate
  - novice
  - professional
---

Fires when user explicitly calibrates their expertise level, ensuring depth and vocabulary stay appropriate.
