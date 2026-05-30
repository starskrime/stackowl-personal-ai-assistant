"""Cron-prompt security scanner — block injection / exfiltration payloads.

A scheduled (cron) prompt runs later in a FRESH, fully-tool-enabled session with
no user watching, so a malicious prompt smuggled into a job is a high-value
attack: it can quietly read secrets, ship them off-box, or override the agent's
instructions on every future tick. :func:`scan_cron_prompt` runs a set of
critical-severity pattern families over a candidate prompt BEFORE it is ever
persisted (and again on every update — an attacker may mutate a benign job).

The pattern families (ported algorithm; provenance recorded only in the E7-S1
research/planning artifacts, never named in source per platform policy):

* **prompt-injection / instruction-override** — "ignore previous instructions",
  "disregard your rules", "system prompt override", "do not tell the user".
* **secret reads** — ``cat``-ing ``.env`` / credential / ``.netrc`` / ``.pgpass``
  files; touching ``authorized_keys``; editing ``/etc/sudoers``.
* **destructive** — ``rm -rf /``.
* **secret exfiltration** — a secret-shaped shell variable
  (``$...KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/API...``) embedded directly in a
  ``curl``/``wget`` destination URL, POST/FORM payload, or ``Authorization``
  header to an arbitrary host.
* **invisible-unicode injection** — zero-width / bidi-control characters used to
  hide an instruction from a human reviewer while the model still reads it.

Returns a ``(ok, reason)`` pair: ``(True, None)`` when the prompt is clean, or
``(False, "<human-readable reason>")`` when a family matches. It NEVER raises and
NEVER mutates the prompt — callers decide what to do with a block.

Note on multilinguality: these are SECURITY patterns matching shell command
shapes and a small set of override idioms, NOT a natural-language stopword list.
Word-ish matches use ``re.UNICODE`` so ``\\w`` spans non-ASCII letters rather
than silently failing closed on non-Latin scripts.

RESIDUAL RISK (accepted): this is a regex gate. It catches the known command
shapes and override idioms above, plus a homoglyph/fullwidth NFKC normalization
pre-pass, but it FUNDAMENTALLY cannot catch paraphrase — a sufficiently novel
natural-language re-wording of "ignore your instructions" or an exfil channel
not enumerated here can slip through. That is a documented, accepted limitation:
the scanner is one defence layer (it re-runs on create AND update), not a proof.
"""

from __future__ import annotations

import re
import unicodedata

from stackowl.infra.observability import log

# Hard cap on the prompt length the scanner will inspect. A cron prompt longer
# than this is itself suspicious AND would let a crafted payload drive regex
# backtracking for an unbounded time on the async event loop, so we block-if-over
# rather than scanning a truncated prefix (safer default for a security gate).
_MAX_SCAN_LEN = 8192

# Maximum width of any inner "rest of line" span inside a threat regex. Bounding
# these (instead of unbounded ``[^\n]*``) makes every pattern linear-time and
# kills the catastrophic-backtracking ReDoS class while still spanning a normal
# shell one-liner. 200 chars comfortably covers a real command segment.
_SPAN = r"[^\n]{0,200}"

# A secret-shaped shell variable, e.g. ``$API_KEY`` / ``${DB_PASSWORD}`` /
# ``$MY_SECRET_TOKEN``. Anchors the exfil families below.
_SECRET_VAR_RE = r"\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)\w*\}?"

# A secret read source: an env reference (``os.environ``/``$...KEY...``) OR a
# sensitive on-disk file. Anchors the base64/hex-piped exfil heuristic below.
_SECRET_SRC_RE = (
    rf"(?:os\.environ|process\.env|{_SECRET_VAR_RE}|\.env\b|"
    r"id_rsa|id_ed25519|\.aws/credentials|\.netrc|\.pgpass)"
)

# (compiled pattern, family-id) — instruction-override + secret-read + destructive.
_THREAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        # Broadened: forget/ignore/disregard/pay-no-attention-to … (previous|prior|
        # all|above|the) … (instructions|rules|guidelines|them), with flexible
        # filler — no longer hard-requires a your|all|any qualifier.
        re.compile(
            r"(?:forget|ignore|disregard|pay\s+no\s+attention\s+to)\s+"
            r"(?:\w+\s+)*?(?:previous|prior|all|above|the)\s+"
            r"(?:\w+\s+)*?(?:instructions|rules|guidelines|them)",
            re.IGNORECASE | re.UNICODE,
        ),
        "prompt_injection",
    ),
    (re.compile(r"do\s+not\s+tell\s+the\s+user", re.IGNORECASE | re.UNICODE), "deception_hide"),
    (re.compile(r"system\s+prompt\s+override", re.IGNORECASE | re.UNICODE), "sys_prompt_override"),
    (
        re.compile(
            r"disregard\s+(?:your|all|any)\s+(?:instructions|rules|guidelines)",
            re.IGNORECASE | re.UNICODE,
        ),
        "disregard_rules",
    ),
    (
        # Secret reads beyond ``cat`` — also less/more/head/tail/"print the
        # contents of" against the sensitive-file set (now incl. ssh keys + aws).
        re.compile(
            r"(?:cat|less|more|head|tail|print\s+the\s+contents\s+of)\s+" + _SPAN
            + r"(?:\.env|credentials|\.netrc|\.pgpass|id_rsa|id_ed25519|\.aws/credentials)",
            re.IGNORECASE,
        ),
        "read_secrets",
    ),
    (re.compile(r"authorized_keys", re.IGNORECASE), "ssh_backdoor"),
    (re.compile(r"/etc/sudoers|visudo", re.IGNORECASE), "sudoers_mod"),
    (re.compile(r"rm\s+-rf\s+/", re.IGNORECASE), "destructive_root_rm"),
)

# (compiled pattern, family-id) — secret exfiltration via curl/wget, plus
# non-curl channels (/dev/tcp, nc/ncat, python -c / perl -e) and a base64/hex
# pipe heuristic (a secret source piped into base64/xxd near a network verb).
_EXFIL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        # Bash /dev/tcp redirect to an arbitrary host:port.
        re.compile(r"/dev/tcp/[^\s/]+/\d+", re.IGNORECASE),
        "exfil_dev_tcp",
    ),
    (
        # netcat shipping bytes to a host + port.
        re.compile(r"\b(?:nc|ncat)\s+" + _SPAN + r"\b[\w.-]+\s+\d{2,5}\b", re.IGNORECASE),
        "exfil_netcat",
    ),
    (
        # A python -c / perl -e one-liner that references a secret-shaped var or
        # an environment read.
        re.compile(
            rf"(?:python[0-9.]*\s+-c|perl\s+-e)\s+{_SPAN}"
            rf"(?:os\.environ|process\.env|ENV\s*[\[{{]|getenv|{_SECRET_VAR_RE})",
            re.IGNORECASE,
        ),
        "exfil_interpreter_env",
    ),
    (
        # A secret source piped into base64/xxd in the vicinity of a network
        # verb (curl/wget/nc) — a classic encode-then-ship heuristic.
        re.compile(
            rf"{_SECRET_SRC_RE}{_SPAN}\|{_SPAN}(?:base64|xxd){_SPAN}"
            r"(?:curl|wget|\bnc\b|ncat|/dev/tcp)",
            re.IGNORECASE,
        ),
        "exfil_encode_pipe",
    ),
    (
        re.compile(rf"curl\s+{_SPAN}https?://[^\s\"'`]*{_SECRET_VAR_RE}", re.IGNORECASE),
        "exfil_curl_url",
    ),
    (
        re.compile(rf"wget\s+{_SPAN}https?://[^\s\"'`]*{_SECRET_VAR_RE}", re.IGNORECASE),
        "exfil_wget_url",
    ),
    (
        re.compile(
            rf"curl\s+{_SPAN}(?:--data(?:-raw|-binary|-urlencode)?|-d|--form|-F)\s+{_SPAN}{_SECRET_VAR_RE}",
            re.IGNORECASE,
        ),
        "exfil_curl_data",
    ),
    (
        re.compile(rf"wget\s+{_SPAN}--post-(?:data|file)={_SPAN}{_SECRET_VAR_RE}", re.IGNORECASE),
        "exfil_wget_post",
    ),
    (
        re.compile(
            rf"curl\s+{_SPAN}(?:-H|--header)\s+[\"']Authorization:\s*(?:Bearer|token)\s+{_SECRET_VAR_RE}[\"']",
            re.IGNORECASE,
        ),
        "exfil_curl_auth_header",
    ),
)

# Zero-width and bidirectional-control characters used to hide a payload from a
# human reviewer while the model still ingests it.
_INVISIBLE_CHARS: frozenset[str] = frozenset(
    {
        "​",
        "‌",
        "‍",
        "⁠",
        "﻿",
        "‪",
        "‫",
        "‬",
        "‭",
        "‮",
    }
)


def scan_cron_prompt(prompt: str) -> tuple[bool, str | None]:
    """Scan a candidate cron prompt for critical injection/exfil payloads.

    Returns ``(True, None)`` when clean, or ``(False, reason)`` when a pattern
    family matches. Pure and total — never raises, never mutates ``prompt``.
    """
    log.tool.debug(
        "cron_security.scan: entry",
        extra={"_fields": {"prompt_len": len(prompt)}},
    )

    # Length gate FIRST: block (do not scan) anything over the cap. A prompt this
    # long is itself suspicious for a scheduled job, and scanning it could let a
    # crafted payload drive regex backtracking on the async event loop. Blocking
    # is the safe default for a security gate.
    if len(prompt) > _MAX_SCAN_LEN:
        reason = (
            f"prompt too long to safely scan "
            f"({len(prompt)} chars > {_MAX_SCAN_LEN} cap) — refusing to schedule"
        )
        log.tool.warning(
            "cron_security.scan: blocked — prompt over scan cap",
            extra={"_fields": {"prompt_len": len(prompt), "cap": _MAX_SCAN_LEN}},
        )
        return (False, reason)

    for char in _INVISIBLE_CHARS:
        if char in prompt:
            reason = (
                f"prompt contains an invisible unicode character "
                f"(U+{ord(char):04X}) — a possible hidden injection"
            )
            log.tool.warning(
                "cron_security.scan: blocked — invisible unicode",
                extra={"_fields": {"codepoint": f"U+{ord(char):04X}"}},
            )
            return (False, reason)

    # NFKC pre-pass: fold fullwidth forms + many confusables back to canonical
    # ASCII so a homoglyph/fullwidth-evading payload ("Ｉgnore", Cyrillic "е")
    # still matches the families below. Cheap; runs after the invisible-char
    # check (which must see the raw prompt). NFKC does not strip zero-width
    # marks, so the order is safe.
    normalized = unicodedata.normalize("NFKC", prompt)

    for pattern, family in (*_THREAT_PATTERNS, *_EXFIL_PATTERNS):
        if pattern.search(normalized):
            reason = (
                f"prompt matches the '{family}' threat family — scheduled "
                f"prompts must not contain injection or exfiltration payloads"
            )
            log.tool.warning(
                "cron_security.scan: blocked — threat family matched",
                extra={"_fields": {"family": family}},
            )
            return (False, reason)

    log.tool.debug("cron_security.scan: exit — clean")
    return (True, None)
