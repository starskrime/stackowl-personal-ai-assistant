"""Adversarial tests for the zip extraction guard in remote_install._safe_extract.

A verified-but-malicious zip is still untrusted DATA: a plugin author whose
checksum matched the index can still craft archive members that escape the
extraction root. _safe_extract must fail-closed against every escape vector and
write NOTHING outside the destination directory:

  - a ``../escape`` member  (relative path traversal)
  - an absolute-path member (``/etc/...`` style)
  - a prefix-collision sibling (``../<dest>X/...`` — defeats a naive
    ``str.startswith`` containment check)
  - a symlink member (a follow-up write through it escapes the root)

Each must raise PluginVerificationError and leave the filesystem untouched
outside the extraction root.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from stackowl.plugins.remote_install import _safe_extract
from stackowl.plugins.verify import PluginVerificationError


def _zip_with_members(members: dict[str, str]) -> bytes:
    """Build a zip from {arcname: content}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, content in members.items():
            zf.writestr(arcname, content)
    return buf.getvalue()


def _zip_with_symlink(link_arcname: str, link_target: str) -> bytes:
    """Build a zip containing a single symlink member pointing at link_target."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zinfo = zipfile.ZipInfo(link_arcname)
        # Mark the member as a symlink: file-type bits 0o120000 in the high
        # 16 bits of external_attr (the Unix st_mode), with 0o777 perms.
        zinfo.external_attr = (0o120000 | 0o777) << 16
        zf.writestr(zinfo, link_target)
    return buf.getvalue()


def test_relative_traversal_member_refused(tmp_path: Path) -> None:
    dest = tmp_path / "extract_root"
    outside = tmp_path / "escape.txt"
    blob = _zip_with_members({"../escape.txt": "owned"})
    with pytest.raises(PluginVerificationError):
        _safe_extract(blob, dest)
    assert not outside.exists()


def test_absolute_path_member_refused(tmp_path: Path) -> None:
    dest = tmp_path / "extract_root"
    # An absolute member that, if honored, would write outside dest.
    abs_target = tmp_path / "abs_escape.txt"
    blob = _zip_with_members({str(abs_target): "owned"})
    with pytest.raises(PluginVerificationError):
        _safe_extract(blob, dest)
    assert not abs_target.exists()


def test_prefix_collision_sibling_refused(tmp_path: Path) -> None:
    # dest is ``.../root``; a naive startswith check accepts a sibling whose
    # path begins with the same string, e.g. ``.../rootX/evil.py``.
    dest = tmp_path / "root"
    sibling_victim = tmp_path / "rootX" / "evil.py"
    blob = _zip_with_members({"../rootX/evil.py": "owned"})
    with pytest.raises(PluginVerificationError):
        _safe_extract(blob, dest)
    assert not sibling_victim.exists()


def test_symlink_member_refused(tmp_path: Path) -> None:
    dest = tmp_path / "extract_root"
    # A symlink that points outside the root; a follow-up write through it
    # would escape. Reject the symlink member itself, fail-closed.
    blob = _zip_with_symlink("link", str(tmp_path / "secret"))
    with pytest.raises(PluginVerificationError):
        _safe_extract(blob, dest)
    # The symlink must not have been materialized inside dest.
    assert not (dest / "link").exists()
    assert not (dest / "link").is_symlink()
