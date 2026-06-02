"""is_local_url — base_url host locality classifier (E10-S1 FIX 1).

Locality must be derived from the configured base_url host, NOT the routing tier:
the shipped ollama.yaml is tier ``fast`` yet on-box. These cases pin that signal.
"""

from __future__ import annotations

import pytest

from stackowl.infra.net.host_locality import is_local_url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",  # the SHIPPED default ollama base_url
        "http://localhost:1234/v1",  # lmstudio
        "http://127.0.0.1:11434/v1",  # loopback literal
        "http://[::1]:11434/v1",  # IPv6 loopback
        "http://192.168.1.50:11434/v1",  # private LAN
        "http://10.0.0.5:8080",  # private
        "http://172.16.3.4/v1",  # private
        "http://169.254.10.10/v1",  # link-local
        "http://LocalHost:11434/v1",  # case-insensitive
    ],
)
def test_local_hosts_classify_local(url: str) -> None:
    assert is_local_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.anthropic.com/v1",
        "https://api.openai.com/v1",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://api.x.ai/v1",
        "http://example.com/v1",
        "https://8.8.8.8/v1",  # public IP literal
        None,  # no base_url
        "",  # blank
        "   ",  # whitespace
        "not a url",  # unparseable → fail safe to cloud
    ],
)
def test_cloud_or_unknown_hosts_classify_not_local(url: str | None) -> None:
    assert is_local_url(url) is False
