#!/usr/bin/env python3
"""Private Git-local reversible structural folding for optional context blocks."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import context_compiler


STORE_PARENT = "engineering-system"
STORE_NAME = "context-fold-v1"
KEY_NAME = "store.key"
LOCK_NAME = "store.lock"
ENTRIES_NAME = "entries"
KEY_BYTES = 32
MAX_ENTRY_BYTES = context_compiler.MAX_BLOCK_TEXT_BYTES
MAX_ENTRIES = 64
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FOLD_IDS = context_compiler.MAX_BLOCKS
HANDLE_RE = re.compile(r"^[0-9a-f]{64}$")
MARKER_RE = re.compile(
    r"^\[DR-FOLD v=1 handle=([0-9a-f]{64}) bytes=([0-9]{1,9})\]$"
)
REQUEST_FIELDS = frozenset({"task", "blocks", "fold_ids", "enabled"})


class FoldError(ValueError):
    pass


def _git_output(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FoldError("GIT_UNAVAILABLE") from exc
    if completed.returncode != 0:
        raise FoldError("GIT_BOUNDARY_UNAVAILABLE")
    return completed.stdout.strip()
def absolute_git_dir(root: Path) -> Path:
    raw = _git_output(root, "rev-parse", "--absolute-git-dir")
    path = Path(raw)
    if not path.is_absolute():
        raise FoldError("GIT_BOUNDARY_UNAVAILABLE")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FoldError("GIT_BOUNDARY_UNAVAILABLE") from exc
    if not resolved.is_dir():
        raise FoldError("GIT_BOUNDARY_UNAVAILABLE")
    return resolved


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise FoldError("STORE_BOUNDARY_INVALID") from exc


def _assert_node(path: Path, *, directory: bool, private: bool) -> os.stat_result:
    info = _lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise FoldError("STORE_BOUNDARY_INVALID")
    elif not stat.S_ISREG(info.st_mode):
        raise FoldError("STORE_BOUNDARY_INVALID")
    if private and info.st_mode & 0o077:
        raise FoldError("STORE_PERMISSIONS_UNSAFE")
    return info


def _assert_private(path: Path, *, directory: bool) -> os.stat_result:
    return _assert_node(path, directory=directory, private=True)


def _ensure_private_directory(path: Path, *, parent: Path) -> Path:
    if path.exists() or path.is_symlink():
        _assert_private(path, directory=True)
    else:
        try:
            path.mkdir(mode=0o700)
        except OSError as exc:
            raise FoldError("STORE_CREATE_FAILED") from exc
        try:
            os.chmod(path, 0o700)
        except OSError as exc:
            raise FoldError("STORE_CREATE_FAILED") from exc
        _assert_private(path, directory=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(parent):
        raise FoldError("STORE_OUT_OF_BOUNDS")
    return resolved
def store_directory(root: Path, *, create: bool) -> Path:
    git_dir = absolute_git_dir(root)
    parent = git_dir / STORE_PARENT
    if parent.is_symlink():
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    if parent.exists():
        _assert_node(parent, directory=True, private=False)
        parent_resolved = parent.resolve(strict=True)
        if not parent_resolved.is_relative_to(git_dir):
            raise FoldError("STORE_OUT_OF_BOUNDS")
    elif create:
        try:
            parent.mkdir(mode=0o700)
        except OSError as exc:
            raise FoldError("STORE_CREATE_FAILED") from exc
        parent_resolved = parent.resolve(strict=True)
    else:
        return parent / STORE_NAME

    store = parent_resolved / STORE_NAME
    if create:
        return _ensure_private_directory(store, parent=git_dir)
    if store.is_symlink():
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    if not store.exists():
        return store
    _assert_private(store, directory=True)
    resolved = store.resolve(strict=True)
    if not resolved.is_relative_to(git_dir):
        raise FoldError("STORE_OUT_OF_BOUNDS")
    return resolved


def entries_directory(root: Path, *, create: bool) -> Path:
    store = store_directory(root, create=create)
    entries = store / ENTRIES_NAME
    if create:
        return _ensure_private_directory(entries, parent=store)
    if entries.is_symlink():
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    if entries.exists():
        _assert_private(entries, directory=True)
        resolved = entries.resolve(strict=True)
        if not resolved.is_relative_to(store.resolve(strict=True)):
            raise FoldError("STORE_OUT_OF_BOUNDS")
        return resolved
    return entries


def _write_private_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise
    except OSError as exc:
        raise FoldError("STORE_WRITE_FAILED") from exc
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise FoldError("STORE_WRITE_FAILED")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    _assert_private(path, directory=False)


def _read_private_bytes(path: Path, *, missing: str, unreadable: str) -> bytes:
    if path.is_symlink():
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise FoldError(missing) from exc
    except OSError as exc:
        raise FoldError(unreadable) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FoldError("STORE_BOUNDARY_INVALID")
        if info.st_mode & 0o077:
            raise FoldError("STORE_PERMISSIONS_UNSAFE")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


@contextmanager
def _exclusive_store_lock(root: Path) -> Iterator[None]:
    store = store_directory(root, create=True)
    lock_path = store / LOCK_NAME
    if lock_path.is_symlink():
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise FoldError("STORE_LOCK_FAILED") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FoldError("STORE_BOUNDARY_INVALID")
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            raise FoldError("STORE_LOCK_FAILED") from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def _load_key(root: Path, *, create: bool) -> bytes:
    store = store_directory(root, create=create)
    key_path = store / KEY_NAME
    if key_path.is_symlink():
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    if not key_path.exists():
        if not create:
            raise FoldError("STORE_KEY_MISSING")
        key = secrets.token_bytes(KEY_BYTES)
        try:
            _write_private_new(key_path, key)
        except FileExistsError:
            pass
    key = _read_private_bytes(
        key_path,
        missing="STORE_KEY_MISSING",
        unreadable="STORE_KEY_UNREADABLE",
    )
    if len(key) != KEY_BYTES:
        raise FoldError("STORE_KEY_INVALID")
    return key


def _handle(key: bytes, content: bytes) -> str:
    return hmac.new(key, content, hashlib.sha256).hexdigest()


def _marker(handle: str, size: int) -> str:
    return f"[DR-FOLD v=1 handle={handle} bytes={size}]"


def parse_marker(marker: Any) -> tuple[str, int]:
    if not isinstance(marker, str):
        raise FoldError("MARKER_INVALID")
    match = MARKER_RE.fullmatch(marker)
    if match is None:
        raise FoldError("MARKER_INVALID")
    handle = match.group(1)
    size = int(match.group(2))
    if size <= 0 or size > MAX_ENTRY_BYTES:
        raise FoldError("MARKER_INVALID")
    return handle, size


def _scan_entries(root: Path) -> tuple[int, int]:
    directory = entries_directory(root, create=False)
    if not directory.exists():
        return 0, 0
    count = 0
    total = 0
    for item in sorted(directory.iterdir(), key=lambda value: value.name):
        if item.is_symlink():
            raise FoldError("STORE_SYMLINK_FORBIDDEN")
        if item.name.startswith("."):
            raise FoldError("STORE_ENTRY_INVALID")
        if not item.name.endswith(".bin") or not HANDLE_RE.fullmatch(item.stem):
            raise FoldError("STORE_ENTRY_INVALID")
        info = _assert_private(item, directory=False)
        size = info.st_size
        if size <= 0 or size > MAX_ENTRY_BYTES:
            raise FoldError("STORE_ENTRY_INVALID")
        count += 1
        total += size
        if count > MAX_ENTRIES or total > MAX_TOTAL_BYTES:
            raise FoldError("STORE_LIMIT_EXCEEDED")
    return count, total


def _content_bytes(text: Any) -> bytes:
    if not isinstance(text, str) or not text:
        raise FoldError("CONTENT_INVALID")
    content = text.encode("utf-8")
    if len(content) > MAX_ENTRY_BYTES:
        raise FoldError("ENTRY_TOO_LARGE")
    return content


def _preflight_fold_batch_locked(root: Path, texts: list[str]) -> None:
    key = _load_key(root, create=True)
    directory = entries_directory(root, create=True)
    count, total = _scan_entries(root)
    pending: dict[str, bytes] = {}
    for text in texts:
        content = _content_bytes(text)
        handle = _handle(key, content)
        destination = directory / f"{handle}.bin"
        if destination.is_symlink():
            raise FoldError("STORE_SYMLINK_FORBIDDEN")
        if destination.exists():
            existing = _read_private_bytes(
                destination,
                missing="STORE_ENTRY_MISSING",
                unreadable="STORE_ENTRY_UNREADABLE",
            )
            if existing != content or not hmac.compare_digest(_handle(key, existing), handle):
                raise FoldError("STORE_ENTRY_CORRUPT")
            continue
        pending.setdefault(handle, content)
    pending_bytes = sum(len(content) for content in pending.values())
    if count + len(pending) > MAX_ENTRIES or total + pending_bytes > MAX_TOTAL_BYTES:
        raise FoldError("STORE_LIMIT_EXCEEDED")


def _put_locked(root: Path, text: str) -> tuple[str, bool]:
    content = _content_bytes(text)
    key = _load_key(root, create=True)
    handle = _handle(key, content)
    directory = entries_directory(root, create=True)
    destination = directory / f"{handle}.bin"
    if destination.is_symlink():
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    if destination.exists():
        existing = _read_private_bytes(
            destination,
            missing="STORE_ENTRY_MISSING",
            unreadable="STORE_ENTRY_UNREADABLE",
        )
        if existing != content or not hmac.compare_digest(_handle(key, existing), handle):
            raise FoldError("STORE_ENTRY_CORRUPT")
        return _marker(handle, len(content)), True

    count, total = _scan_entries(root)
    if count + 1 > MAX_ENTRIES or total + len(content) > MAX_TOTAL_BYTES:
        raise FoldError("STORE_LIMIT_EXCEEDED")
    try:
        _write_private_new(destination, content)
    except FileExistsError:
        existing = _read_private_bytes(
            destination,
            missing="STORE_ENTRY_MISSING",
            unreadable="STORE_ENTRY_UNREADABLE",
        )
        if existing != content or not hmac.compare_digest(_handle(key, existing), handle):
            raise FoldError("STORE_ENTRY_CORRUPT")
        return _marker(handle, len(content)), True
    return _marker(handle, len(content)), False


def put(root: Path, text: str) -> tuple[str, bool]:
    with _exclusive_store_lock(root):
        return _put_locked(root, text)


def expand(root: Path, marker: str) -> str:
    handle, expected_size = parse_marker(marker)
    key = _load_key(root, create=False)
    directory = entries_directory(root, create=False)
    if not directory.exists():
        raise FoldError("STORE_ENTRY_MISSING")
    path = directory / f"{handle}.bin"
    if path.is_symlink():
        raise FoldError("STORE_SYMLINK_FORBIDDEN")
    if not path.exists():
        raise FoldError("STORE_ENTRY_MISSING")
    content = _read_private_bytes(
        path,
        missing="STORE_ENTRY_MISSING",
        unreadable="STORE_ENTRY_UNREADABLE",
    )
    if len(content) != expected_size:
        raise FoldError("STORE_ENTRY_CORRUPT")
    actual = _handle(key, content)
    if not hmac.compare_digest(actual, handle):
        raise FoldError("STORE_ENTRY_CORRUPT")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FoldError("STORE_ENTRY_CORRUPT") from exc
def purge(root: Path) -> dict[str, int | str]:
    directory = entries_directory(root, create=False)
    if not directory.exists():
        return {"decision": "PURGED", "entries_deleted": 0, "bytes_deleted": 0}
    with _exclusive_store_lock(root):
        directory = entries_directory(root, create=False)
        if not directory.exists():
            return {"decision": "PURGED", "entries_deleted": 0, "bytes_deleted": 0}
        count = 0
        total = 0
        for item in sorted(directory.iterdir(), key=lambda value: value.name):
            if item.is_symlink():
                raise FoldError("STORE_SYMLINK_FORBIDDEN")
            if not item.name.endswith(".bin") or not HANDLE_RE.fullmatch(item.stem):
                raise FoldError("STORE_ENTRY_INVALID")
            info = _assert_private(item, directory=False)
            total += info.st_size
            try:
                item.unlink()
            except OSError as exc:
                raise FoldError("STORE_PURGE_FAILED") from exc
            count += 1
        return {"decision": "PURGED", "entries_deleted": count, "bytes_deleted": total}


def _fold_ids(raw: Any, block_ids: set[str], *, enabled: bool) -> list[str]:
    if raw is None and not enabled:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_FOLD_IDS:
        raise FoldError("FOLD_IDS_INVALID")
    result: list[str] = []
    for value in raw:
        if (
            not isinstance(value, str)
            or context_compiler.SAFE_ID_RE.fullmatch(value) is None
            or value not in block_ids
        ):
            raise FoldError("FOLD_ID_INVALID")
        result.append(value)
    if len(result) != len(set(result)):
        raise FoldError("FOLD_ID_DUPLICATE")
    if enabled and not result:
        raise FoldError("FOLD_IDS_EMPTY")
    return result


def fold_request(root: Path, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != REQUEST_FIELDS:
        raise FoldError("REQUEST_INVALID")
    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        raise FoldError("ENABLED_INVALID")
    try:
        task, blocks = context_compiler.parse_request(
            {"task": raw["task"], "blocks": raw["blocks"]}
        )
    except context_compiler.CompilerError as exc:
        raise FoldError(f"CONTEXT_INPUT_INVALID:{exc}") from exc
    by_id = {block.block_id: block for block in blocks}
    fold_ids = _fold_ids(raw["fold_ids"], set(by_id), enabled=enabled)

    if not enabled:
        return {
            "task": raw["task"],
            "blocks": raw["blocks"],
            "telemetry": {
                "decision": "BYPASS",
                "requested_count": len(fold_ids),
                "folded_count": 0,
                "deduplicated_count": 0,
                "original_folded_bytes": 0,
                "marker_bytes": 0,
            },
        }

    for block_id in fold_ids:
        if by_id[block_id].protected:
            raise FoldError("PROTECTED_BLOCK_FOLD_FORBIDDEN")

    replacements: dict[str, str] = {}
    original_bytes = 0
    marker_bytes = 0
    deduplicated = 0
    with _exclusive_store_lock(root):
        _preflight_fold_batch_locked(
            root, [by_id[block_id].text for block_id in fold_ids]
        )
        for block_id in fold_ids:
            block = by_id[block_id]
            marker, was_deduplicated = _put_locked(root, block.text)
            replacements[block_id] = marker
            original_bytes += len(block.text.encode("utf-8"))
            marker_bytes += len(marker.encode("utf-8"))
            deduplicated += int(was_deduplicated)
    output_blocks: list[dict[str, Any]] = []
    for raw_block in raw["blocks"]:
        item = dict(raw_block)
        block_id = item["id"]
        if block_id in replacements:
            item["text"] = replacements[block_id]
        output_blocks.append(item)

    return {
        "task": task,
        "blocks": output_blocks,
        "telemetry": {
            "decision": "FOLDED",
            "requested_count": len(fold_ids),
            "folded_count": len(fold_ids),
            "deduplicated_count": deduplicated,
            "original_folded_bytes": original_bytes,
            "marker_bytes": marker_bytes,
        },
    }


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FoldError("INPUT_INVALID") from exc


def _write_output_private(path: str, data: bytes) -> None:
    target = Path(path)
    if target.is_symlink():
        raise FoldError("OUTPUT_SYMLINK_FORBIDDEN")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except OSError as exc:
        raise FoldError("OUTPUT_WRITE_FAILED") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FoldError("OUTPUT_WRITE_FAILED")
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise FoldError("OUTPUT_WRITE_FAILED")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: str, value: Any) -> None:
    payload = (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    _write_output_private(path, payload)


def _write_text_private(path: str, text: str) -> None:
    _write_output_private(path, text.encode("utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reversible Git-local context folding")
    sub = parser.add_subparsers(dest="command", required=True)

    fold = sub.add_parser("fold")
    fold.add_argument("--root", required=True)
    fold.add_argument("--input", required=True)
    fold.add_argument("--output", required=True)

    expand_cmd = sub.add_parser("expand")
    expand_cmd.add_argument("--root", required=True)
    expand_cmd.add_argument("--marker", required=True)
    expand_cmd.add_argument("--output", required=True)

    purge_cmd = sub.add_parser("purge")
    purge_cmd.add_argument("--root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.root)
        if args.command == "fold":
            result = fold_request(root, _read_json(args.input))
            _write_json(args.output, result)
            telemetry = result["telemetry"]
            print(
                "CONTEXT_FOLD=PASS "
                f"decision={telemetry['decision']} "
                f"folded={telemetry['folded_count']}"
            )
            return 0
        if args.command == "expand":
            original = expand(root, args.marker)
            _write_text_private(args.output, original)
            print("CONTEXT_FOLD_EXPAND=PASS")
            return 0
        if args.command == "purge":
            report = purge(root)
            print(json.dumps(report, sort_keys=True, separators=(",", ":")))
            return 0
    except FoldError as exc:
        print(f"CONTEXT_FOLD=BLOCK reason={exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
