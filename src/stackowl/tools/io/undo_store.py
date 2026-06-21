"""Write-safety substrate: pre-image snapshots + a one-shot ``undo_write`` tool.

A successful-but-wrong edit should be "one undo away". Before any in-place write,
a file-mutating tool snapshots the file's pre-image through :class:`UndoStore`,
which returns an opaque token. ``undo_write`` (or a direct ``restore(token)``)
puts the pre-image back. The store is a bounded ring — only the most recent N
snapshots are retained; the oldest are dropped (and their on-disk pre-images
removed) so the substrate can't grow without bound.

Snapshots live under ``StackowlHome.home()/undo`` — operational state, never
inside the user's workspace or the project tree (all state under ~/.stackowl/).

Provenance / port-vs-build: BUILD (StackOwl-native write-safety primitive).
The post-write read-back-verify pattern it complements is the E3 ``edit`` port;
see ``_bmad-output/research/tool-port-analysis.md`` (E3 ``edit`` row).
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult

# Bounded ring size. 50 snapshots is generous for an interactive edit loop while
# capping disk use; oldest pre-images are evicted (and deleted) past this count.
_MAX_SNAPSHOTS = 50

_INDEX_NAME = "index.json"
# Sentinel target_path marking a multi-file group snapshot (its blob is a JSON
# {"restore":[{path,content}], "delete":[path]} payload, not a raw pre-image).
_GROUP_MARKER = "__group__"


@dataclass(frozen=True)
class _SnapshotMeta:
    """One snapshot's bookkeeping entry (the pre-image bytes live in a sibling file)."""

    token: str
    target_path: str  # absolute path of the file the pre-image belongs to
    blob_name: str  # filename of the pre-image blob within the undo dir
    created_at: float


class UndoStore:
    """Bounded, on-disk ring of file pre-images keyed by opaque tokens.

    Every method is self-healing: a missing/corrupt index is treated as empty,
    a missing blob yields a structured failure (never an exception), and the
    most-recent-first ordering is reconstructed purely from the index so a
    crash mid-write can never strand the store.
    """

    def __init__(self, root: Path | None = None, *, max_snapshots: int = _MAX_SNAPSHOTS) -> None:
        if root is None:
            from stackowl.paths import StackowlHome

            root = StackowlHome.home() / "undo"
        self._root = root
        self._max = max(1, max_snapshots)

    # ------------------------------------------------------------------ index io

    @property
    def root(self) -> Path:
        return self._root

    def _index_path(self) -> Path:
        return self._root / _INDEX_NAME

    def _load_index(self) -> list[_SnapshotMeta]:
        """Load the index, oldest-first. Missing/corrupt index → empty (self-healing)."""
        path = self._index_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            log.tool.warning("undo_store: corrupt index — treating as empty", extra={"_fields": {"path": str(path)}})
            return []
        out: list[_SnapshotMeta] = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                try:
                    out.append(
                        _SnapshotMeta(
                            token=str(item["token"]),
                            target_path=str(item["target_path"]),
                            blob_name=str(item["blob_name"]),
                            created_at=float(item["created_at"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        return out

    def _save_index(self, metas: list[_SnapshotMeta]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "token": m.token,
                "target_path": m.target_path,
                "blob_name": m.blob_name,
                "created_at": m.created_at,
            }
            for m in metas
        ]
        self._index_path().write_text(json.dumps(payload), encoding="utf-8")

    # ------------------------------------------------------------------ public api

    def snapshot(self, target: Path, content: str) -> str:
        """Persist *content* as the pre-image of *target*; return its undo token.

        Evicts the oldest snapshot(s) past the ring bound (deleting their blobs).
        """
        log.tool.debug("undo_store.snapshot: entry", extra={"_fields": {"target": str(target)}})
        self._root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        blob_name = f"{token}.bak"
        (self._root / blob_name).write_text(content, encoding="utf-8")

        metas = self._load_index()
        metas.append(
            _SnapshotMeta(
                token=token,
                target_path=str(target.resolve()),
                blob_name=blob_name,
                created_at=time.time(),
            )
        )
        # Evict oldest past the bound (index is oldest-first).
        while len(metas) > self._max:
            stale = metas.pop(0)
            try:
                (self._root / stale.blob_name).unlink()
            except OSError:
                log.tool.warning(
                    "undo_store.snapshot: stale blob unlink failed",
                    extra={"_fields": {"blob": stale.blob_name}},
                )
        self._save_index(metas)
        log.tool.debug(
            "undo_store.snapshot: exit",
            extra={"_fields": {"token": token, "ring_size": len(metas)}},
        )
        return token

    def snapshot_group(
        self,
        restore_items: list[tuple[Path, str]],
        delete_paths: list[Path] | None = None,
    ) -> str:
        """Snapshot a multi-file change as ONE ring entry; return one group token.

        ``restore_items`` are (path, pre-image content) pairs to write back on
        undo; ``delete_paths`` are files the change CREATED that undo must remove.
        A single ``restore(group_token)`` reverts the whole patch — and because it
        occupies one ring slot, it is not subject to per-file eviction (so a
        multi-file patch is genuinely one ``undo_write`` away). Atomicity rollback
        on failure is handled in-memory by the caller; this is for user-facing undo.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        blob_name = f"{token}.bak"
        payload = {
            "restore": [{"path": str(Path(p).resolve()), "content": c} for p, c in restore_items],
            "delete": [str(Path(p).resolve()) for p in (delete_paths or [])],
        }
        (self._root / blob_name).write_text(json.dumps(payload), encoding="utf-8")
        metas = self._load_index()
        metas.append(
            _SnapshotMeta(token=token, target_path=_GROUP_MARKER, blob_name=blob_name, created_at=time.time())
        )
        while len(metas) > self._max:
            stale = metas.pop(0)
            with contextlib.suppress(OSError):
                (self._root / stale.blob_name).unlink()
        self._save_index(metas)
        log.tool.debug(
            "undo_store.snapshot_group: exit",
            extra={"_fields": {"token": token, "files": len(restore_items), "deletes": len(delete_paths or [])}},
        )
        return token

    def latest_token(self) -> str | None:
        """Token of the most recent snapshot, or None when the store is empty."""
        metas = self._load_index()
        return metas[-1].token if metas else None

    def restore(self, token: str) -> tuple[bool, str]:
        """Restore the pre-image identified by *token* back to its target file.

        Returns ``(success, message)``. Never raises — a missing token or blob,
        an unwritable target, or any OS error is reported as a structured failure.
        The consumed snapshot is dropped from the ring on success.
        """
        log.tool.debug("undo_store.restore: entry", extra={"_fields": {"token": token}})
        metas = self._load_index()
        match = next((m for m in metas if m.token == token), None)
        if match is None:
            log.tool.info("undo_store.restore: unknown token", extra={"_fields": {"token": token}})
            return False, f"Unknown undo token: {token!r}"

        blob = self._root / match.blob_name
        try:
            pre_image = blob.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            log.tool.warning(
                "undo_store.restore: pre-image blob missing",
                extra={"_fields": {"token": token, "blob": match.blob_name}},
            )
            return False, f"Pre-image for token {token!r} is missing: {exc}"

        # Group snapshot (multi-file patch undo): restore all members + remove
        # files the patch created. One token reverts the whole patch.
        if match.target_path == _GROUP_MARKER:
            return self._restore_group(token, pre_image, metas, blob)

        target = Path(match.target_path)
        # Defense-in-depth: re-confine the write boundary. EditTool guards the path
        # before snapshotting, so a normally-indexed target is already inside the
        # workspace — but restore() is the one write that trusts a stored path, so
        # re-validate it here (a tampered index must never let restore overwrite an
        # arbitrary file). Party Security § "path guard at every write boundary".
        from stackowl.tools.io.path_guard import is_within_root

        if not is_within_root(target):
            log.tool.warning(
                "undo_store.restore: target escapes workspace — refusing",
                extra={"_fields": {"token": token, "target": match.target_path}},
            )
            return False, f"Refusing to restore outside the workspace: {match.target_path}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(pre_image, encoding="utf-8")
        except OSError as exc:
            log.tool.error(
                "undo_store.restore: write-back failed",
                exc_info=exc,
                extra={"_fields": {"token": token, "target": match.target_path}},
            )
            return False, f"Failed to restore {match.target_path}: {exc}"

        # Consume the snapshot — drop its index entry and blob.
        metas = [m for m in metas if m.token != token]
        with contextlib.suppress(OSError):
            blob.unlink()  # best-effort; index no longer references it
        self._save_index(metas)
        log.tool.debug(
            "undo_store.restore: exit",
            extra={"_fields": {"token": token, "target": match.target_path, "ring_size": len(metas)}},
        )
        return True, f"Restored {match.target_path} from pre-image (token {token})."

    def _restore_group(
        self, token: str, blob_text: str, metas: list[_SnapshotMeta], blob: Path,
    ) -> tuple[bool, str]:
        """Restore a multi-file group snapshot: write back pre-images + remove created files."""
        from stackowl.tools.io.path_guard import is_within_root

        try:
            payload = json.loads(blob_text)
        except ValueError:
            return False, f"Corrupt group snapshot for token {token!r}"
        restored = 0
        skipped = 0
        for item in payload.get("restore", []):
            p = Path(str(item.get("path", "")))
            if not is_within_root(p):
                skipped += 1
                continue
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(item.get("content", "")), encoding="utf-8")
                restored += 1
            except OSError:
                skipped += 1
        for dp in payload.get("delete", []):
            p = Path(str(dp))
            if not is_within_root(p):
                skipped += 1
                continue
            with contextlib.suppress(OSError):
                p.unlink(missing_ok=True)
        # Consume the group snapshot.
        remaining = [m for m in metas if m.token != token]
        with contextlib.suppress(OSError):
            blob.unlink()
        self._save_index(remaining)
        msg = f"Reverted patch: {restored} file(s) restored"
        if skipped:
            msg += f", {skipped} skipped (outside workspace or unwritable)"
        log.tool.debug(
            "undo_store._restore_group: exit",
            extra={"_fields": {"token": token, "restored": restored, "skipped": skipped}},
        )
        return skipped == 0, msg + f" (token {token})."


class UndoWriteTool(Tool):
    """Restore the most recent file pre-image — undo the last in-place write."""

    def __init__(self, store: UndoStore | None = None) -> None:
        self._store = store or UndoStore()

    @property
    def name(self) -> str:
        return "undo_write"

    @property
    def description(self) -> str:
        return (
            "Undo the most recent file write/edit by restoring the file's "
            "pre-image. Pass a specific 'token' to undo a particular write, or "
            "omit it to undo the latest. Restores in place; confined to prior "
            "snapshots taken by edit/patch tools."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "token": {
                    "type": "string",
                    "description": "Undo token from a prior edit result. Omit to undo the most recent write.",
                },
            },
            "required": [],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="transactional",
            toolset_group="code",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        token_arg = kwargs.get("token")
        token = str(token_arg) if token_arg else None
        log.tool.debug("undo_write.execute: entry", extra={"_fields": {"has_token": token is not None}})

        if token is None:
            token = self._store.latest_token()
            if token is None:
                duration_ms = (time.monotonic() - t0) * 1000
                log.tool.info("undo_write.execute: nothing to undo")
                # Pure no-op: no snapshot exists, so nothing was restored. This is
                # not an effectful failure and must not trip the give-up floor.
                return ToolResult(
                    success=False,
                    output="",
                    error="Nothing to undo: no snapshots are available.",
                    duration_ms=duration_ms,
                    side_effect_committed=False,
                )

        ok, message = self._store.restore(token)
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.debug("undo_write.execute: exit", extra={"_fields": {"success": ok, "duration_ms": duration_ms}})
        if not ok:
            return ToolResult(success=False, output="", error=message, duration_ms=duration_ms)
        return ToolResult(success=True, output=message, duration_ms=duration_ms)
