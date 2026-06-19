"""Unit tests for _looks_like_git_repo heuristic in skill_command.py."""
from __future__ import annotations

import pytest

from stackowl.commands.skill_command import _looks_like_git_repo


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Standard owner/repo — canonical case
        ("https://github.com/owner/repo", True),
        # Trailing slash — previously misclassified as archive
        ("https://github.com/owner/repo/", True),
        # Extra path segments (e.g. tree/main) — still a git host repo
        ("https://github.com/owner/repo/tree/main", True),
        # .git suffix — explicit marker, any host
        ("https://example.com/some/path.git", True),
        # git@ SSH URL
        ("git@github.com:owner/repo.git", True),
        # gitlab and bitbucket hosts
        ("https://gitlab.com/ns/project", True),
        ("https://bitbucket.org/team/repo/", True),
        # codeberg host
        ("https://codeberg.org/owner/repo", True),
        # .zip archive — must NOT be treated as git
        ("https://example.com/releases/skill-v1.0.zip", False),
        # .tar.gz archive
        ("https://example.com/dist/tool.tar.gz", False),
        # Unknown host with two segments — not a known git forge
        ("https://selfhosted.example.com/owner/repo", False),
        # Only one path segment on a known host (bare host path)
        ("https://github.com/owner", False),
        # Empty path on a known host
        ("https://github.com/", False),
    ],
)
def test_looks_like_git_repo(url: str, expected: bool) -> None:
    assert _looks_like_git_repo(url) is expected
