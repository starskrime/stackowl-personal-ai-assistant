---
name: media-generate
description: Generate an image, produce speech audio, or analyze an image — and deliver the resulting artifact to the user.
when_to_use: When the user wants an image created, speech or audio produced from text, or an image described and analyzed. Covers generation (image, audio) and perception (vision analysis) tasks, always ending with delivery of the artifact or findings.
version: 0.1.0
tags: [image, audio, speech, vision, media, generation]
author: stackowl-builtin
license: MIT
---

# Media Generation and Analysis

Claiming "here is your image" before confirming that generation actually
succeeded — and that the file was delivered — is an overclaim. This skill
enforces that every media operation produces a real, confirmed artifact or
finding and that it reaches the user before any success is reported.

## Steps

1. **Determine the media task type.**
   - Image creation → use `image_generate`.
   - Speech or audio from text → use `tts`.
   - Describe or analyze an existing image → use `vision_analyze`.

2. **For image creation, call `image_generate`.** Pass the prompt and any
   style or format parameters. Capture the returned file path or result. Do not
   proceed to delivery if the call returned an error or an empty result.

3. **For speech or audio, call `tts`.** Pass the text to be spoken and any
   voice or format parameters. Capture the returned audio file path. Do not
   proceed if the result is absent or the call failed.

4. **For image analysis, call `vision_analyze`.** Pass the image path or
   reference. Read the description or structured findings returned. The
   analysis is the artifact — no separate delivery step is needed unless the
   user also wants the findings saved.

5. **Deliver the produced file to the user with `send_file`.** For generated
   images and audio, pass the confirmed file path to `send_file` so the user
   receives the artifact directly. Confirm that `send_file` succeeded before
   reporting completion.

## Verification

Before reporting success to the user:

- **Confirm the artifact was produced.** The generation call must have returned
  a real file path or a non-empty result. An empty result or error means
  generation failed — do not proceed to delivery.
- **Confirm delivery succeeded.** Check that `send_file` completed without
  error. A file that exists on disk but was not delivered is not a success.
- **Never say "here is your image" or "here is your audio" if generation or
  delivery failed.** Report the failure honestly and include the error detail.
- **For vision analysis, quote the actual findings.** Do not describe an image
  from memory or assumption — only report what `vision_analyze` returned.

## Pitfalls

- **Claiming a media artifact that was not created.** If `image_generate` or
  `tts` returns an error, the artifact does not exist. Reporting it as though
  it does is an overclaim.
- **Describing an image without running `vision_analyze`.** Describing image
  content from the file name, prompt, or prior context — rather than from the
  tool output — produces hallucinated descriptions.
- **Forgetting to deliver the file.** A generated image sitting on disk has not
  reached the user. Always follow generation with `send_file`.
- **Treating a partial result as success.** If `send_file` fails after
  successful generation, the task is incomplete. Report the partial state
  honestly.
