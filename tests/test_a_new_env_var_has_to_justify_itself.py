"""D18.1 — behavioural settings belong in config; env is for four other things.

THE REFERENCE RULE: `.env` is secrets only, and *every* behavioural setting —
timeouts, thresholds, feature flags, display prefs — lives in `config.yaml`.

**Adopting it verbatim would be wrong here, and measuring said so.** All 18
`STACKOWL_*` variables were read on 2026-09-05 and every one has a reason to be
environmental rather than a Settings field:

    BOOTSTRAP           read before Settings can exist — you cannot read
                        config.yaml to learn where config.yaml is.
                        DATA_DIR, HOME, LOG_DIR, PID_FILE, CONFIG_FILE, YAML,
                        CORE_SOCKET, MODEL_CACHE_DIR, TEST_MODE
    HOST-SPECIFIC       a property of the MACHINE, not of the user. The same
                        config file deploys to several hosts.
                        CONTEXT_CEILING ("ONLY to opt into a host-specific cap
                        (e.g. to bound KV-cache RAM on a constrained inference
                        server)" — its own docstring), BROWSER_OFFLINE
    TERMINAL CONVENTION following NO_COLOR / prefers-reduced-motion, where the
                        environment is the conventional home.
                        REDUCED_MOTION, NO_GLYPHS
    LOGGING BOOTSTRAP   logging configures before Settings loads.
                        LOG_LEVEL, LOG_RETAIN_DAYS
    DEPLOYMENT SECRET   read at import, where SecretResolver (which needs
                        config) is not available. FINGERPRINT_SECRET, whose own
                        comment states the fallback's weakness honestly.
    UNWIRED             HEADLESS_OAUTH, in the vendor integration D16.4 measured
                        as in-tree but never registered.

STACKOWL SOLVES THE SECRETS HALF DIFFERENTLY, AND BETTER. There is no `.env`.
`config/secret_resolver.py` resolves `keychain:<service>`, `file:<path>` or a
bare env name — so the config file holds a POINTER, never the value, and the
strongest option is the OS keychain rather than a dotfile.

SO WHAT IS ACTUALLY MISSING is not a migration. It is that today's discipline is
held by the authors who happened to write each of these carefully, and nothing
asks the question when the NEXT one is added. A behavioural setting introduced
as a bare `os.environ.get` would look exactly like these and belong in Settings.

This guard is that question, asked automatically: a new `STACKOWL_*` name must
be classified here, with its reason, or the gate fails.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stackowl"
_ENV_RE = re.compile(r"STACKOWL_[A-Z0-9_]+")

#: Every environment variable this platform reads, and WHY it is environmental
#: rather than a Settings field. Adding a name here is a design decision: if the
#: honest answer is "it is a behavioural setting", it belongs in Settings and in
#: `stackowl.yaml`, where an operator can discover it.
_JUSTIFIED: dict[str, str] = {
    # bootstrap — needed before Settings exists
    "STACKOWL_DATA_DIR": "bootstrap",
    "STACKOWL_HOME": "bootstrap",
    "STACKOWL_LOG_DIR": "bootstrap",
    "STACKOWL_PID_FILE": "bootstrap",
    "STACKOWL_CONFIG_FILE": "bootstrap",
    "STACKOWL_YAML": "bootstrap",
    "STACKOWL_CORE_SOCKET": "bootstrap",
    "STACKOWL_MODEL_CACHE_DIR": "bootstrap",
    "STACKOWL_TEST_MODE": "bootstrap",
    # host-specific — a property of the machine, not the user
    "STACKOWL_CONTEXT_CEILING": "host-specific",
    "STACKOWL_BROWSER_OFFLINE": "host-specific",
    # terminal convention — NO_COLOR / prefers-reduced-motion family
    "STACKOWL_REDUCED_MOTION": "terminal-convention",
    "STACKOWL_NO_GLYPHS": "terminal-convention",
    # logging bootstrap — configured before Settings loads
    "STACKOWL_LOG_LEVEL": "logging-bootstrap",
    "STACKOWL_LOG_RETAIN_DAYS": "logging-bootstrap",
    "STACKOWL_LOG_": "logging-bootstrap",  # the prefix, scanned in observability
    # deployment secret — read at import, before SecretResolver is available
    "STACKOWL_FINGERPRINT_SECRET": "deployment-secret",
    # unwired — the in-tree vendor integration nothing registers (D16.4)
    "STACKOWL_HEADLESS_OAUTH": "unwired",
}

_VALID_REASONS = {
    "bootstrap", "host-specific", "terminal-convention",
    "logging-bootstrap", "deployment-secret", "unwired",
}


def _env_names() -> set[str]:
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        found |= set(_ENV_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return found


@pytest.mark.tripwire
def test_every_env_var_is_classified() -> None:
    actual = _env_names()
    assert len(actual) > 10, f"expected the real env surface, found {len(actual)}"

    unclassified = actual - set(_JUSTIFIED)
    assert not unclassified, (
        f"new environment variable(s): {sorted(unclassified)}.\n"
        "D18.1: a BEHAVIOURAL setting — a timeout, threshold, feature flag or "
        "display preference — belongs in Settings and `stackowl.yaml`, where an "
        "operator can discover it. If this one is genuinely bootstrap, "
        "host-specific, a terminal convention, logging bootstrap or a deployment "
        "secret, classify it here with that reason."
    )

    stale = set(_JUSTIFIED) - actual
    assert not stale, (
        f"classified but no longer read: {sorted(stale)}. Remove them — a list "
        "that outlives its subjects stops describing anything."
    )


@pytest.mark.tripwire
def test_no_reason_is_invented() -> None:
    """The taxonomy is closed. 'behavioural' is deliberately NOT a valid reason:
    a behavioural setting has a home, and it is not the environment."""
    bad = {k: v for k, v in _JUSTIFIED.items() if v not in _VALID_REASONS}
    assert not bad, f"unknown justification(s): {bad}"


def test_secrets_do_not_live_in_a_dotenv() -> None:
    """The half of the rule StackOwl already answers, and more strongly.

    `SecretResolver` takes a REFERENCE — `keychain:`, `file:` or an env name — so
    the config file never holds a secret value, and the strongest option is the
    OS keychain rather than a dotfile.
    """
    resolver = (_SRC / "config" / "secret_resolver.py").read_text(encoding="utf-8")

    assert "keychain:" in resolver
    assert "file:" in resolver
    assert not (_SRC.parent.parent / ".env").exists(), (
        "a .env appeared — secrets belong in the keychain, via a keychain: reference"
    )
