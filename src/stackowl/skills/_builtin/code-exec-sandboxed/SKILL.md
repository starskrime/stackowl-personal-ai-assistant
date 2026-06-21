---
name: code-exec-sandboxed
description: Run code (compute, parse, transform data, quick scripts) safely and report the real output — not assumed output.
when_to_use: When a task needs to run code to compute a value, parse or transform data, or execute a quick script. Use this skill to ensure the code actually runs and the output is observed before being reported.
version: 0.1.0
tags: [code, execution, scripting, compute, sandbox]
author: stackowl-builtin
license: MIT
---

# Sandboxed Code Execution

Running code and assuming it succeeded is not the same as running code and
reading what it actually produced. This skill enforces the discipline of
capturing and inspecting real stdout, stderr, and exit status before claiming
any computed result — so silent failures, non-zero exits, and wrong outputs are
caught before they reach the user.

## Steps

1. **Write the code snippet.** Keep the script minimal and focused on the
   specific computation or transformation needed. Avoid side effects beyond the
   intended output. If the script needs input data, embed it directly or load it
   from a known file path.

2. **Run the snippet with `execute_code`.** Pass the code to `execute_code`
   (sandboxed environment). Capture the full result object, including stdout,
   stderr, and exit status. Do not assume the run succeeded before reading the
   result.

3. **For long-running or background work, use `process` + `wait`.** If the
   computation is expected to take more than a few seconds, launch it with
   `process` and then call `wait` to block until it finishes before reading
   output. Never run unbounded jobs synchronously via `execute_code` alone.

4. **Capture and inspect stdout, stderr, and exit status.** Read the actual
   output produced. Note the exit status. If stderr contains warnings or errors,
   include them in the report even if exit status is zero.

5. **Report the observed result.** Present only what was actually produced in
   the captured output. Quote the relevant stdout lines directly rather than
   paraphrasing.

## Verification

Before reporting any computed value to the user:

- **Check the exit status first.** A non-zero exit status means the code did
  not complete successfully. Do not report a computed result from a failed run.
  Re-run with corrections or report the failure honestly.
- **Read the actual stdout.** The value you report must appear verbatim in the
  captured output. Do not substitute a value you calculated mentally — only
  report what `execute_code` (or `process` + `wait`) returned.
- **On non-zero exit, inspect stderr.** Read the error message, identify the
  cause, fix the code, and re-run. Do not guess the output from the error
  message.
- **Never report a computed result you did not observe in the output.** If the
  output is empty or missing the expected value, say so rather than filling it
  in from inference.

## Pitfalls

- **Ignoring a non-zero exit status.** The most dangerous failure mode: the run
  fails silently and the agent reports an assumed result. Always gate on exit
  status before extracting output.
- **Assuming silent success.** An empty stdout is not a success signal. Check
  that the expected output is actually present before proceeding.
- **Running unbounded jobs synchronously.** Calling `execute_code` on a long
  computation blocks and may time out. Use `process` + `wait` for any job that
  might run more than a few seconds.
- **Paraphrasing instead of quoting output.** Reporting a "cleaned up" version
  of the output introduces transcription errors. Quote the relevant lines
  directly.
