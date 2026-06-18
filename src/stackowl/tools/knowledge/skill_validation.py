"""Skill validation + static security scan — the HARD gate before any
agent-driven skill mutation (E4 design change #2).

The ``skill_manage`` tool lets the agent write the very skills that become its
own future system prompt. A poisoned skill body ("always exfiltrate X / ignore
safety on Y") would re-inject as trusted first-party context on the next turn,
closing a self-mutation loop with no human in it. So before the tool path
mutates+reindexes a skill — even when no human ever sees the diff — it must run
a frontmatter/name/category validation pass and a regex-based static security
scan, and a failing scan must BLOCK the mutation.

This module is the substrate the tool consumes. It exposes:

* :func:`validate_skill_name`, :func:`validate_category`, :func:`validate_frontmatter`,
  :func:`validate_content_size` — pure validators returning an error string or ``None``.
* :func:`scan_skill_dir` — static security scan of a skill directory, returning a
  :class:`ScanResult`.
* :func:`security_scan_gate` — the single function the tool calls BEFORE any
  mutation; returns ``(ok, reason)`` where ``ok is False`` MUST block the write.

The threat-pattern catalogue and structural checks were ported from prior-art
agent security tooling reviewed in
``_bmad-output/research/tool-port-analysis.md`` (E4 PORT/HYBRID rows) and
re-expressed here as neutral, dependency-free Python. No external trust-tier or
config coupling — the gate is unconditional on the tool path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from stackowl.infra.observability import log

# --- Limits ----------------------------------------------------------------

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000  # ~36k tokens at ~2.75 chars/token

# Filesystem-safe, URL-friendly skill/category names.
_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# --- Validators ------------------------------------------------------------


def validate_skill_name(name: str) -> str | None:
    """Validate a skill name. Returns an error message, or ``None`` if valid."""
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not _VALID_NAME_RE.match(name):
        return (
            f"Invalid skill name '{name}'. Use lowercase letters, numbers, "
            "hyphens, dots, and underscores. Must start with a letter or digit."
        )
    return None


def validate_category(category: str | None) -> str | None:
    """Validate an optional category used as a single directory segment.

    Returns an error message, or ``None`` if valid (including when ``category``
    is ``None`` or blank — categories are optional).
    """
    if category is None:
        return None
    if not isinstance(category, str):
        return "Category must be a string."
    category = category.strip()
    if not category:
        return None
    if "/" in category or "\\" in category:
        return (
            f"Invalid category '{category}'. Categories must be a single "
            "directory name (no path separators)."
        )
    if len(category) > MAX_NAME_LENGTH:
        return f"Category exceeds {MAX_NAME_LENGTH} characters."
    if not _VALID_NAME_RE.match(category):
        return (
            f"Invalid category '{category}'. Use lowercase letters, numbers, "
            "hyphens, dots, and underscores. Categories must be a single "
            "directory name."
        )
    return None


def validate_frontmatter(content: str) -> str | None:
    """Validate that SKILL.md content has well-formed frontmatter + body.

    Requires a YAML frontmatter block delimited by ``---`` lines with at least
    ``name`` and ``description`` keys, and a non-empty body after the closing
    delimiter. Returns an error message, or ``None`` if valid.
    """
    if not content.strip():
        return "Content cannot be empty."
    if not content.startswith("---"):
        return (
            "SKILL.md must start with YAML frontmatter (---). "
            "See existing skills for the format."
        )
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return (
            "SKILL.md frontmatter is not closed. Ensure you have a closing "
            "'---' line."
        )
    yaml_content = content[3 : end_match.start() + 3]
    try:
        parsed = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        return f"YAML frontmatter parse error: {exc}"
    if not isinstance(parsed, dict):
        return "Frontmatter must be a YAML mapping (key: value pairs)."
    if "name" not in parsed:
        return "Frontmatter must include 'name' field."
    if "description" not in parsed:
        return "Frontmatter must include 'description' field."
    if len(str(parsed["description"])) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    body = content[end_match.end() + 3 :].strip()
    if not body:
        return (
            "SKILL.md must have content after the frontmatter "
            "(instructions, procedures, etc.)."
        )
    return None


def validate_content_size(content: str, label: str = "SKILL.md") -> str | None:
    """Check that content is within the per-write character limit.

    Returns an error message, or ``None`` if within bounds.
    """
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return (
            f"{label} content is {len(content):,} characters "
            f"(limit: {MAX_SKILL_CONTENT_CHARS:,}). Consider splitting into a "
            "smaller SKILL.md with supporting files."
        )
    return None


# --- Security scan ----------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One static-scan hit within a skill file."""

    pattern_id: str
    severity: str  # "critical" | "high" | "medium" | "low"
    category: str
    file: str
    line: int
    match: str
    description: str


@dataclass(frozen=True)
class ScanResult:
    """Outcome of a skill-directory scan."""

    skill_name: str
    verdict: str  # "safe" | "caution" | "dangerous"
    findings: list[Finding] = field(default_factory=list)
    summary: str = ""


# Threat patterns: (regex, pattern_id, severity, category, description).
# Matched case-insensitively, line by line. Ported and re-expressed from
# prior-art agent skill-security tooling (see module docstring).
_THREAT_PATTERNS: tuple[tuple[str, str, str, str, str], ...] = (
    # Exfiltration: shell commands leaking secrets.
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)",
     "env_exfil_curl", "critical", "exfiltration",
     "curl command interpolating secret environment variable"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)",
     "env_exfil_wget", "critical", "exfiltration",
     "wget command interpolating secret environment variable"),
    (r"fetch\s*\([^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|API)",
     "env_exfil_fetch", "critical", "exfiltration",
     "fetch() call interpolating secret environment variable"),
    (r"httpx?\.(get|post|put|patch)\s*\([^\n]*(KEY|TOKEN|SECRET|PASSWORD)",
     "env_exfil_httpx", "critical", "exfiltration",
     "HTTP library call with secret variable"),
    (r"requests\.(get|post|put|patch)\s*\([^\n]*(KEY|TOKEN|SECRET|PASSWORD)",
     "env_exfil_requests", "critical", "exfiltration",
     "requests library call with secret variable"),
    # Exfiltration: reading credential stores.
    (r"base64[^\n]*env",
     "encoded_exfil", "high", "exfiltration",
     "base64 encoding combined with environment access"),
    (r"\$HOME/\.ssh|\~/\.ssh",
     "ssh_dir_access", "high", "exfiltration",
     "references user SSH directory"),
    (r"\$HOME/\.aws|\~/\.aws",
     "aws_dir_access", "high", "exfiltration",
     "references user AWS credentials directory"),
    (r"\$HOME/\.gnupg|\~/\.gnupg",
     "gpg_dir_access", "high", "exfiltration",
     "references user GPG keyring"),
    (r"\$HOME/\.kube|\~/\.kube",
     "kube_dir_access", "high", "exfiltration",
     "references Kubernetes config directory"),
    (r"\$HOME/\.docker|\~/\.docker",
     "docker_dir_access", "high", "exfiltration",
     "references Docker config (may contain registry creds)"),
    (r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)",
     "read_secrets_file", "critical", "exfiltration",
     "reads known secrets file"),
    # Exfiltration: programmatic env access.
    (r"printenv|env\s*\|",
     "dump_all_env", "high", "exfiltration",
     "dumps all environment variables"),
    (r"os\.environ\b(?!\s*\.get\s*\(\s*[\"']PATH)",
     "python_os_environ", "high", "exfiltration",
     "accesses os.environ (potential env dump)"),
    (r"os\.getenv\s*\(\s*[^\)]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)",
     "python_getenv_secret", "critical", "exfiltration",
     "reads secret via os.getenv()"),
    (r"process\.env\[",
     "node_process_env", "high", "exfiltration",
     "accesses process.env (Node.js environment)"),
    (r"ENV\[.*(?:KEY|TOKEN|SECRET|PASSWORD)",
     "ruby_env_secret", "critical", "exfiltration",
     "reads secret via Ruby ENV[]"),
    # Exfiltration: DNS and staging.
    (r"\b(dig|nslookup|host)\s+[^\n]*\$",
     "dns_exfil", "critical", "exfiltration",
     "DNS lookup with variable interpolation (possible DNS exfiltration)"),
    (r">\s*/tmp/[^\s]*\s*&&\s*(curl|wget|nc|python)",
     "tmp_staging", "critical", "exfiltration",
     "writes to a temp file then exfiltrates"),
    # Exfiltration: markdown/link based.
    (r"!\[.*\]\(https?://[^\)]*\$\{?",
     "md_image_exfil", "high", "exfiltration",
     "markdown image URL with variable interpolation (image-based exfil)"),
    (r"\[.*\]\(https?://[^\)]*\$\{?",
     "md_link_exfil", "high", "exfiltration",
     "markdown link with variable interpolation"),
    # Prompt injection.
    (r"ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+instructions",
     "prompt_injection_ignore", "critical", "injection",
     "prompt injection: ignore previous instructions"),
    (r"you\s+are\s+(?:\w+\s+)*now\s+",
     "role_hijack", "high", "injection",
     "attempts to override the agent's role"),
    (r"do\s+not\s+(?:\w+\s+)*tell\s+(?:\w+\s+)*the\s+user",
     "deception_hide", "critical", "injection",
     "instructs agent to hide information from user"),
    (r"system\s+prompt\s+override",
     "sys_prompt_override", "critical", "injection",
     "attempts to override the system prompt"),
    (r"pretend\s+(?:\w+\s+)*(you\s+are|to\s+be)\s+",
     "role_pretend", "high", "injection",
     "attempts to make the agent assume a different identity"),
    (r"disregard\s+(?:\w+\s+)*(your|all|any)\s+(?:\w+\s+)*(instructions|rules|guidelines)",
     "disregard_rules", "critical", "injection",
     "instructs agent to disregard its rules"),
    (r"output\s+(?:\w+\s+)*(system|initial)\s+prompt",
     "leak_system_prompt", "high", "injection",
     "attempts to extract the system prompt"),
    (r"(when|if)\s+no\s*one\s+is\s+(watching|looking)",
     "conditional_deception", "high", "injection",
     "conditional instruction to behave differently when unobserved"),
    (r"act\s+as\s+(if|though)\s+(?:\w+\s+)*you\s+(?:\w+\s+)*(have\s+no|don't\s+have)\s+(?:\w+\s+)*(restrictions|limits|rules)",
     "bypass_restrictions", "critical", "injection",
     "instructs agent to act without restrictions"),
    (r"translate\s+.*\s+into\s+.*\s+and\s+(execute|run|eval)",
     "translate_execute", "critical", "injection",
     "translate-then-execute evasion technique"),
    (r"<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->",
     "html_comment_injection", "high", "injection",
     "hidden instructions in HTML comments"),
    (r"<\s*div\s+style\s*=\s*[\"'][\s\S]*?display\s*:\s*none",
     "hidden_div", "high", "injection",
     "hidden HTML div (invisible instructions)"),
    # Destructive operations.
    (r"rm\s+-rf\s+/",
     "destructive_root_rm", "critical", "destructive",
     "recursive delete from root"),
    (r"rm\s+(-[^\s]*)?r.*\$HOME|\brmdir\s+.*\$HOME",
     "destructive_home_rm", "critical", "destructive",
     "recursive delete targeting home directory"),
    (r"chmod\s+777",
     "insecure_perms", "medium", "destructive",
     "sets world-writable permissions"),
    (r">\s*/etc/",
     "system_overwrite", "critical", "destructive",
     "overwrites system configuration file"),
    (r"\bmkfs\b",
     "format_filesystem", "critical", "destructive",
     "formats a filesystem"),
    (r"\bdd\s+.*if=.*of=/dev/",
     "disk_overwrite", "critical", "destructive",
     "raw disk write operation"),
    (r"shutil\.rmtree\s*\(\s*[\"'/]",
     "python_rmtree", "high", "destructive",
     "Python rmtree on absolute or root-relative path"),
    (r"truncate\s+-s\s*0\s+/",
     "truncate_system", "critical", "destructive",
     "truncates system file to zero bytes"),
    # Persistence.
    (r"\bcrontab\b",
     "persistence_cron", "medium", "persistence",
     "modifies cron jobs"),
    (r"\.(bashrc|zshrc|profile|bash_profile|bash_login|zprofile|zlogin)\b",
     "shell_rc_mod", "medium", "persistence",
     "references shell startup file"),
    (r"authorized_keys",
     "ssh_backdoor", "critical", "persistence",
     "modifies SSH authorized keys"),
    (r"ssh-keygen",
     "ssh_keygen", "medium", "persistence",
     "generates SSH keys"),
    (r"systemd.*\.service|systemctl\s+(enable|start)",
     "systemd_service", "medium", "persistence",
     "references or enables systemd service"),
    (r"/etc/init\.d/",
     "init_script", "medium", "persistence",
     "references init.d startup script"),
    (r"launchctl\s+load|LaunchAgents|LaunchDaemons",
     "macos_launchd", "medium", "persistence",
     "macOS launch agent/daemon persistence"),
    (r"/etc/sudoers|visudo",
     "sudoers_mod", "critical", "persistence",
     "modifies sudoers (privilege escalation)"),
    (r"git\s+config\s+--global\s+",
     "git_config_global", "medium", "persistence",
     "modifies global git configuration"),
    # Network: reverse shells and tunnels.
    (r"\bnc\s+-[lp]|ncat\s+-[lp]|\bsocat\b",
     "reverse_shell", "critical", "network",
     "potential reverse shell listener"),
    (r"\bngrok\b|\blocaltunnel\b|\bserveo\b|\bcloudflared\b",
     "tunnel_service", "high", "network",
     "uses tunneling service for external access"),
    (r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}",
     "hardcoded_ip_port", "medium", "network",
     "hardcoded IP address with port"),
    (r"0\.0\.0\.0:\d+|INADDR_ANY",
     "bind_all_interfaces", "high", "network",
     "binds to all network interfaces"),
    (r"/bin/(ba)?sh\s+-i\s+.*>/dev/tcp/",
     "bash_reverse_shell", "critical", "network",
     "bash interactive reverse shell via /dev/tcp"),
    (r"python[23]?\s+-c\s+[\"']import\s+socket",
     "python_socket_oneliner", "critical", "network",
     "Python one-liner socket connection (likely reverse shell)"),
    (r"socket\.connect\s*\(\s*\(",
     "python_socket_connect", "high", "network",
     "Python socket connect to arbitrary host"),
    (r"webhook\.site|requestbin\.com|pipedream\.net|hookbin\.com",
     "exfil_service", "high", "network",
     "references known data exfiltration/webhook testing service"),
    (r"pastebin\.com|hastebin\.com|ghostbin\.",
     "paste_service", "medium", "network",
     "references paste service (possible data staging)"),
    # Obfuscation: encoding and eval.
    (r"base64\s+(-d|--decode)\s*\|",
     "base64_decode_pipe", "high", "obfuscation",
     "base64 decodes and pipes to execution"),
    (r"\\x[0-9a-fA-F]{2}.*\\x[0-9a-fA-F]{2}.*\\x[0-9a-fA-F]{2}",
     "hex_encoded_string", "medium", "obfuscation",
     "hex-encoded string (possible obfuscation)"),
    (r"\beval\s*\(\s*[\"']",
     "eval_string", "high", "obfuscation",
     "eval() with string argument"),
    (r"\bexec\s*\(\s*[\"']",
     "exec_string", "high", "obfuscation",
     "exec() with string argument"),
    (r"echo\s+[^\n]*\|\s*(bash|sh|python|perl|ruby|node)",
     "echo_pipe_exec", "critical", "obfuscation",
     "echo piped to interpreter for execution"),
    (r"compile\s*\(\s*[^\)]+,\s*[\"'].*[\"']\s*,\s*[\"']exec[\"']\s*\)",
     "python_compile_exec", "high", "obfuscation",
     "Python compile() with exec mode"),
    (r"getattr\s*\(\s*__builtins__",
     "python_getattr_builtins", "high", "obfuscation",
     "dynamic access to Python builtins (evasion technique)"),
    (r"__import__\s*\(\s*[\"']os[\"']\s*\)",
     "python_import_os", "high", "obfuscation",
     "dynamic import of os module"),
    (r"codecs\.decode\s*\(\s*[\"']",
     "python_codecs_decode", "medium", "obfuscation",
     "codecs.decode (possible ROT13 or encoding obfuscation)"),
    (r"String\.fromCharCode|charCodeAt",
     "js_char_code", "medium", "obfuscation",
     "JavaScript character code construction (possible obfuscation)"),
    (r"atob\s*\(|btoa\s*\(",
     "js_base64", "medium", "obfuscation",
     "JavaScript base64 encode/decode"),
    (r"chr\s*\(\s*\d+\s*\)\s*\+\s*chr\s*\(\s*\d+",
     "chr_building", "high", "obfuscation",
     "building string from chr() calls (obfuscation)"),
    (r"\\u[0-9a-fA-F]{4}.*\\u[0-9a-fA-F]{4}.*\\u[0-9a-fA-F]{4}",
     "unicode_escape_chain", "medium", "obfuscation",
     "chain of unicode escapes (possible obfuscation)"),
    # Process execution in scripts.
    (r"os\.system\s*\(",
     "python_os_system", "high", "execution",
     "os.system() — unguarded shell execution"),
    (r"os\.popen\s*\(",
     "python_os_popen", "high", "execution",
     "os.popen() — shell pipe execution"),
    (r"child_process\.(exec|spawn|fork)\s*\(",
     "node_child_process", "high", "execution",
     "Node.js child_process execution"),
    (r"Runtime\.getRuntime\(\)\.exec\(",
     "java_runtime_exec", "high", "execution",
     "Java Runtime.exec() — shell execution"),
    # Path traversal.
    (r"\.\./\.\./\.\.",
     "path_traversal_deep", "high", "traversal",
     "deep relative path traversal (3+ levels up)"),
    (r"/etc/passwd|/etc/shadow",
     "system_passwd_access", "critical", "traversal",
     "references system password files"),
    (r"/proc/self|/proc/\d+/",
     "proc_access", "high", "traversal",
     "references /proc filesystem (process introspection)"),
    # Crypto mining.
    (r"xmrig|stratum\+tcp|monero|coinhive|cryptonight",
     "crypto_mining", "critical", "mining",
     "cryptocurrency mining reference"),
    # Supply chain: curl/wget pipe to shell.
    (r"curl\s+[^\n]*\|\s*(ba)?sh",
     "curl_pipe_shell", "critical", "supply_chain",
     "curl piped to shell (download-and-execute)"),
    (r"wget\s+[^\n]*-O\s*-\s*\|\s*(ba)?sh",
     "wget_pipe_shell", "critical", "supply_chain",
     "wget piped to shell (download-and-execute)"),
    (r"curl\s+[^\n]*\|\s*python",
     "curl_pipe_python", "critical", "supply_chain",
     "curl piped to Python interpreter"),
    # Privilege escalation.
    (r"^allowed-tools\s*:",
     "allowed_tools_field", "high", "privilege_escalation",
     "skill declares allowed-tools (pre-approves tool access)"),
    (r"\bsudo\b",
     "sudo_usage", "high", "privilege_escalation",
     "uses sudo (privilege escalation)"),
    (r"setuid|setgid|cap_setuid",
     "setuid_setgid", "critical", "privilege_escalation",
     "setuid/setgid (privilege escalation mechanism)"),
    (r"NOPASSWD",
     "nopasswd_sudo", "critical", "privilege_escalation",
     "NOPASSWD sudoers entry (passwordless privilege escalation)"),
    # Agent config persistence.
    (r"AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules",
     "agent_config_mod", "critical", "persistence",
     "references agent config files (could persist malicious instructions)"),
    (r"\.claude/settings|\.codex/config",
     "other_agent_config", "high", "persistence",
     "references other agent configuration files"),
    # Hardcoded secrets embedded in the skill itself.
    (r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*[\"'][A-Za-z0-9+/=_-]{20,}",
     "hardcoded_secret", "critical", "credential_exposure",
     "possible hardcoded API key, token, or secret"),
    (r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----",
     "embedded_private_key", "critical", "credential_exposure",
     "embedded private key"),
    (r"ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{80,}",
     "github_token_leaked", "critical", "credential_exposure",
     "GitHub personal access token in skill content"),
    (r"sk-[A-Za-z0-9]{20,}",
     "openai_key_leaked", "critical", "credential_exposure",
     "possible API key in skill content"),
    (r"AKIA[0-9A-Z]{16}",
     "aws_access_key_leaked", "critical", "credential_exposure",
     "AWS access key ID in skill content"),
    # Jailbreak patterns.
    (r"\bDAN\s+mode\b|Do\s+Anything\s+Now",
     "jailbreak_dan", "critical", "injection",
     "DAN (Do Anything Now) jailbreak attempt"),
    (r"\bdeveloper\s+mode\b.*\benabled?\b",
     "jailbreak_dev_mode", "critical", "injection",
     "developer mode jailbreak attempt"),
    (r"(respond|answer|reply)\s+without\s+(?:\w+\s+)*(restrictions|limitations|filters|safety)",
     "remove_filters", "critical", "injection",
     "instructs agent to respond without safety filters"),
    # Context window exfiltration.
    (r"(include|output|print|send|share)\s+(?:\w+\s+)*(conversation|chat\s+history|previous\s+messages|context)",
     "context_exfil", "high", "exfiltration",
     "instructs agent to output/share conversation history"),
    (r"(send|post|upload|transmit)\s+.*\s+(to|at)\s+https?://",
     "send_to_url", "high", "exfiltration",
     "instructs agent to send data to a URL"),
)

# Structural limits.
_MAX_FILE_COUNT = 50
_MAX_TOTAL_SIZE_KB = 1024
_MAX_SINGLE_FILE_KB = 256

# Text extensions worth scanning (skip binary).
_SCANNABLE_EXTENSIONS = frozenset({
    ".md", ".txt", ".py", ".sh", ".bash", ".js", ".ts", ".rb",
    ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".conf",
    ".html", ".css", ".xml", ".tex", ".r", ".jl", ".pl", ".php",
})

# Binary/executable extensions that have no place in a skill.
_SUSPICIOUS_BINARY_EXTENSIONS = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".com",
    ".msi", ".dmg", ".app", ".deb", ".rpm",
})

# Invisible / bidi unicode used to hide injected instructions.
_INVISIBLE_CHARS: dict[str, str] = {
    "​": "zero-width space",
    "‌": "zero-width non-joiner",
    "‍": "zero-width joiner",
    "⁠": "word joiner",
    "⁣": "invisible separator",
    "﻿": "BOM/zero-width no-break space",
    "‪": "LTR embedding",
    "‫": "RTL embedding",
    "‬": "pop directional",
    "‭": "LTR override",
    "‮": "RTL override",
    "⁦": "LTR isolate",
    "⁧": "RTL isolate",
    "⁩": "pop directional isolate",
}

# Compile once at import for scan throughput.
_COMPILED_PATTERNS: tuple[tuple[re.Pattern[str], str, str, str, str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), pid, sev, cat, desc)
    for pat, pid, sev, cat, desc in _THREAT_PATTERNS
)


def scan_file(file_path: Path, rel_path: str = "") -> list[Finding]:
    """Scan a single file for threat patterns and invisible unicode.

    Returns a list of :class:`Finding` (deduplicated per pattern per line).
    Non-scannable extensions (other than ``SKILL.md``) and unreadable files
    yield an empty list.
    """
    if not rel_path:
        rel_path = file_path.name
    if (
        file_path.suffix.lower() not in _SCANNABLE_EXTENSIONS
        and file_path.name != "SKILL.md"
    ):
        return []
    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    findings: list[Finding] = []
    lines = content.split("\n")
    seen: set[tuple[str, int]] = set()
    for pattern, pid, severity, category, description in _COMPILED_PATTERNS:
        for i, line in enumerate(lines, start=1):
            if (pid, i) in seen:
                continue
            if pattern.search(line):
                seen.add((pid, i))
                matched = line.strip()
                if len(matched) > 120:
                    matched = matched[:117] + "..."
                findings.append(Finding(
                    pattern_id=pid, severity=severity, category=category,
                    file=rel_path, line=i, match=matched, description=description,
                ))
    for i, line in enumerate(lines, start=1):
        for char, name in _INVISIBLE_CHARS.items():
            if char in line:
                findings.append(Finding(
                    pattern_id="invisible_unicode", severity="high",
                    category="injection", file=rel_path, line=i,
                    match=f"U+{ord(char):04X} ({name})",
                    description=f"invisible unicode character {name} "
                                "(possible text hiding/injection)",
                ))
                break  # one finding per line for invisible chars
    return findings


def _check_structure(skill_dir: Path) -> list[Finding]:
    """Structural anomaly checks: file count, sizes, binaries, symlink escape."""
    findings: list[Finding] = []
    file_count = 0
    total_size = 0
    for f in skill_dir.rglob("*"):
        if not f.is_file() and not f.is_symlink():
            continue
        rel = str(f.relative_to(skill_dir))
        file_count += 1
        if f.is_symlink():
            try:
                resolved = f.resolve()
                if not resolved.is_relative_to(skill_dir.resolve()):
                    findings.append(Finding(
                        pattern_id="symlink_escape", severity="critical",
                        category="traversal", file=rel, line=0,
                        match=f"symlink -> {resolved}",
                        description="symlink points outside the skill directory",
                    ))
            except OSError:
                findings.append(Finding(
                    pattern_id="broken_symlink", severity="medium",
                    category="traversal", file=rel, line=0,
                    match="broken symlink",
                    description="broken or circular symlink",
                ))
            continue
        try:
            size = f.stat().st_size
            total_size += size
        except OSError:
            continue
        if size > _MAX_SINGLE_FILE_KB * 1024:
            findings.append(Finding(
                pattern_id="oversized_file", severity="medium",
                category="structural", file=rel, line=0,
                match=f"{size // 1024}KB",
                description=f"file is {size // 1024}KB "
                            f"(limit: {_MAX_SINGLE_FILE_KB}KB)",
            ))
        ext = f.suffix.lower()
        if ext in _SUSPICIOUS_BINARY_EXTENSIONS:
            findings.append(Finding(
                pattern_id="binary_file", severity="critical",
                category="structural", file=rel, line=0,
                match=f"binary: {ext}",
                description=f"binary/executable file ({ext}) "
                            "should not be in a skill",
            ))
    if file_count > _MAX_FILE_COUNT:
        findings.append(Finding(
            pattern_id="too_many_files", severity="medium",
            category="structural", file="(directory)", line=0,
            match=f"{file_count} files",
            description=f"skill has {file_count} files "
                        f"(limit: {_MAX_FILE_COUNT})",
        ))
    if total_size > _MAX_TOTAL_SIZE_KB * 1024:
        findings.append(Finding(
            pattern_id="oversized_skill", severity="high",
            category="structural", file="(directory)", line=0,
            match=f"{total_size // 1024}KB total",
            description=f"skill is {total_size // 1024}KB total "
                        f"(limit: {_MAX_TOTAL_SIZE_KB}KB)",
        ))
    return findings


def _determine_verdict(findings: list[Finding]) -> str:
    """Map findings to an overall verdict.

    Any ``critical`` finding → ``dangerous``; any other finding → ``caution``;
    none → ``safe``.
    """
    if not findings:
        return "safe"
    if any(f.severity == "critical" for f in findings):
        return "dangerous"
    return "caution"


def _build_summary(name: str, verdict: str, findings: list[Finding]) -> str:
    if not findings:
        return f"{name}: clean scan, no threats detected"
    categories = sorted({f.category for f in findings})
    return f"{name}: {verdict} — {len(findings)} finding(s) in {', '.join(categories)}"


def scan_skill_dir(skill_path: Path) -> ScanResult:
    """Static security scan of a skill directory (or single file).

    Runs structural checks, regex threat-pattern matching, and invisible-unicode
    detection over every scannable file. Returns a :class:`ScanResult` carrying
    the verdict and findings.
    """
    # 1. ENTRY
    log.tool.debug(
        "[knowledge] scan_skill_dir: entry",
        extra={"_fields": {"path": str(skill_path)}},
    )
    skill_name = skill_path.name
    all_findings: list[Finding] = []
    if skill_path.is_dir():
        all_findings.extend(_check_structure(skill_path))
        for f in skill_path.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(skill_path))
                all_findings.extend(scan_file(f, rel))
    elif skill_path.is_file():
        all_findings.extend(scan_file(skill_path, skill_path.name))
    verdict = _determine_verdict(all_findings)
    summary = _build_summary(skill_name, verdict, all_findings)
    # 4. EXIT
    log.tool.debug(
        "[knowledge] scan_skill_dir: exit",
        extra={"_fields": {
            "skill": skill_name, "verdict": verdict, "findings": len(all_findings),
        }},
    )
    return ScanResult(
        skill_name=skill_name, verdict=verdict,
        findings=all_findings, summary=summary,
    )


def format_scan_report(result: ScanResult) -> str:
    """Render a compact, human-readable scan report (verdict + findings)."""
    lines = [f"Scan: {result.skill_name}  Verdict: {result.verdict.upper()}"]
    if result.findings:
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        for f in sorted(result.findings, key=lambda x: order.get(x.severity, 4)):
            sev = f.severity.upper().ljust(8)
            cat = f.category.ljust(14)
            loc = f"{f.file}:{f.line}".ljust(28)
            # Don't echo the matched text for high/critical findings — those are
            # exactly the secret/exfil hits whose match could surface a real
            # credential back into the model context. Category + location is enough
            # for the agent to locate and fix it (MINOR-2).
            shown = "[redacted]" if f.severity in ("critical", "high") else f.match[:60]
            lines.append(f'  {sev} {cat} {loc} "{shown}"')
    return "\n".join(lines)


def security_scan_gate(skill_path: Path) -> tuple[bool, str]:
    """HARD security gate the ``skill_manage`` tool calls BEFORE any mutation.

    A ``False`` first element MUST block the write. The gate is unconditional on
    the tool path — it runs even when no human will see the diff. The policy is
    conservative for agent-authored content: a ``dangerous`` verdict (any
    critical finding) blocks; ``safe`` and ``caution`` are allowed through (the
    provenance/visibility net — audit + snapshot + ``agent_self`` tagging —
    catches the residual risk a static scanner cannot).

    Returns ``(ok, reason)``. Never raises — a scan that itself errors fails
    CLOSED (blocked) so a broken scanner can't become a bypass.
    """
    # 1. ENTRY
    log.tool.debug(
        "[knowledge] security_scan_gate: entry",
        extra={"_fields": {"path": str(skill_path)}},
    )
    try:
        result = scan_skill_dir(skill_path)
    except Exception as exc:  # B5 — fail closed, never bypass on scanner error
        log.tool.error(
            "[knowledge] security_scan_gate: scan crashed — failing closed",
            exc_info=exc, extra={"_fields": {"path": str(skill_path)}},
        )
        return False, (
            "Security scan failed to run; blocking the write to fail closed."
        )
    if result.verdict == "dangerous":
        # 4. EXIT — blocked
        log.tool.warning(
            "[knowledge] security_scan_gate: BLOCKED",
            extra={"_fields": {
                "skill": result.skill_name, "findings": len(result.findings),
            }},
        )
        return False, (
            f"Security scan blocked this skill ({result.summary}):\n"
            f"{format_scan_report(result)}"
        )
    # 4. EXIT — allowed
    log.tool.debug(
        "[knowledge] security_scan_gate: allowed",
        extra={"_fields": {"skill": result.skill_name, "verdict": result.verdict}},
    )
    return True, result.summary
