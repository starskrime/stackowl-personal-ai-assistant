"""Anti-bot helpers — captcha detection, fingerprint policy, allowed-domain check.

Camoufox handles the heavy fingerprint work at the browser binary level.
This module adds the *runtime detection* concerns that live above the browser:
- Detect captchas on a loaded page (so tools can return an actionable error
  rather than hanging while the LLM bangs on a wall).
- Enforce a domain allowlist for the ``browser_browse`` meta-tool's inner loop.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# Common captcha indicators. The ordering matters — first match wins.
_CAPTCHA_INDICATORS: tuple[tuple[str, str], ...] = (
    ("cloudflare_turnstile", 'iframe[src*="challenges.cloudflare.com"]'),
    ("cloudflare_managed",   'div.cf-browser-verification, div#cf-challenge-running'),
    ("hcaptcha",             'iframe[src*="hcaptcha.com"], div.h-captcha'),
    ("recaptcha_v2",         'iframe[src*="recaptcha"], div.g-recaptcha'),
    ("datadome",             'iframe[src*="captcha-delivery.com"], div#ddv1-captcha'),
    ("perimeterx",           'div[id*="px-captcha"]'),
    ("arkose_funcaptcha",    'iframe[src*="arkoselabs.com"], div#FunCaptcha'),
)


async def detect_captcha(page: Any) -> str | None:
    """Return a captcha-type name if a captcha widget is visible, else None.

    Best-effort — relies on common selectors that catch the bulk of the WAF
    market in 2026. False negatives are possible against bespoke challenges.
    """
    for kind, selector in _CAPTCHA_INDICATORS:
        try:
            handle = await page.query_selector(selector)
        except Exception:
            continue
        if handle is None:
            continue
        try:
            visible = await handle.is_visible()
        except Exception:
            visible = True
        if visible:
            return kind
    return None


def is_domain_allowed(url: str, allowed_domains: list[str] | None) -> bool:
    """Return True if ``url``'s host matches one of ``allowed_domains``.

    Empty/None allowlist = allow all (caller's choice). Match is case-insensitive
    and matches the host suffix so ``"example.com"`` covers ``"www.example.com"``
    and ``"api.example.com"``.
    """
    if not allowed_domains:
        return True
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    needles = [d.lower().lstrip(".") for d in allowed_domains]
    return any(host == needle or host.endswith("." + needle) for needle in needles)
