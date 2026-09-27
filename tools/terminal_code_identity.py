#!/usr/bin/env python3
"""Bind terminal finalization to the committed finalizer and consumer.

A same-HEAD dirty worktree is not executable authority. Fresh finalization
imports those modules only after their bytes match the HEAD blobs and the
repository has no dirty, untracked, or submodule drift. Bytecode caches are
not source. This module does not verify a host receipt.
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
TERMINAL_FILES = (
    "tools/terminal_code_identity.py",
    "tools/persistent_benchmark_telemetry.py",
    "tools/benchmark_execution.py",
)


class TerminalCodeError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _git(repo: Path, *args: str, data: bytes | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "--no-replace-objects", *args],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TerminalCodeError("TERMINAL_CODE_UNVERIFIED") from exc
    if completed.returncode != 0:
        raise TerminalCodeError("TERMINAL_CODE_UNVERIFIED")
    return completed.stdout.decode("utf-8").strip()


def _status_path(line: str) -> str:
    path = line[3:] if len(line) >= 4 else line
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    return path.replace("\\", "/")


def _bytecode(path: str) -> bool:
    return "/__pycache__/" in f"/{path}" or path.endswith(".pyc") or path.endswith("/__pycache__")


def _drift(repo: Path) -> bool:
    status = _git(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if any(line and not _bytecode(_status_path(line)) for line in status.splitlines()):
        return True
    submodules = _git(repo, "submodule", "status", "--recursive")
    return any(line and not line.startswith(" ") for line in submodules.splitlines())


def require_terminal_code_id(repo: Path | None = None) -> str:
    """Return the bound blob identity, or reject a drifted terminal tree."""
    root = REPO if repo is None else repo.resolve()
    if _drift(root):
        raise TerminalCodeError("TERMINAL_CODE_DIRTY")
    parts: list[str] = []
    for relative in TERMINAL_FILES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise TerminalCodeError("TERMINAL_CODE_UNVERIFIED")
        data = path.read_bytes()
        blob = _git(root, "hash-object", "--no-filters", "--stdin", data=data)
        committed = _git(root, "rev-parse", "--verify", f"HEAD:{relative}")
        if not blob or blob != committed:
            raise TerminalCodeError("TERMINAL_CODE_DIRTY")
        parts.append(f"{relative} {blob}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _load_verified(relative: str) -> Any:
    """Execute one terminal module from the bytes just bound to HEAD."""
    code_id = require_terminal_code_id(REPO)
    path = REPO / relative
    data = path.read_bytes()
    blob = _git(REPO, "hash-object", "--no-filters", "--stdin", data=data)
    committed = _git(REPO, "rev-parse", "--verify", f"HEAD:{relative}")
    if blob != committed:
        raise TerminalCodeError("TERMINAL_CODE_DIRTY")
    name = "_es_terminal_" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
    module = sys.modules.get(name)
    if module is not None and getattr(module, "TERMINAL_CODE_ID", None) == code_id:
        return module
    spec = importlib.util.spec_from_loader(name, loader=None)
    if spec is None:
        raise TerminalCodeError("TERMINAL_CODE_UNVERIFIED")
    loaded = importlib.util.module_from_spec(spec)
    loaded.__file__ = str(path)
    loaded.TERMINAL_CODE_ID = code_id
    sys.modules[name] = loaded
    exec(compile(data, str(path), "exec"), loaded.__dict__)
    return loaded


def finalize_lane(descriptor_path: Path, *, root: Path) -> dict[str, Any]:
    """Finalize only after the committed finalizer is the code that runs."""
    try:
        module = _load_verified("tools/persistent_benchmark_telemetry.py")
    except TerminalCodeError as exc:
        return {"status": "BLOCK", "reason": exc.code, "execute_worker": False}
    return module.finalize_lane(descriptor_path, root=root)


def derive_observed_fields(record: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Consume terminal telemetry only through the committed consumer."""
    module = _load_verified("tools/benchmark_execution.py")
    return module.derive_observed_fields(record, **kwargs)
