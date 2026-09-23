#!/usr/bin/env python3
"""Emit minimal deterministic context for an existing branch or PR."""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
from pathlib import Path

import yaml


def fail_context(message: str) -> None:
    raise SystemExit(f"CONTEXT_ROUTER=FAIL {message}")


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(args=["git", *args], returncode=127, stdout="")


def run_git_bytes(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(args=["git", *args], returncode=127, stdout=b"")


def decode_git_path(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def nul_records(payload: bytes) -> list[bytes]:
    if not payload:
        return []
    records = payload.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    return records


def parse_status_z(payload: bytes) -> set[str]:
    found: set[str] = set()
    records = nul_records(payload)
    index = 0
    while index < len(records):
        entry = records[index]
        if len(entry) < 4 or entry[2:3] != b" ":
            fail_context("malformed git status output")
        status = entry[:2]
        paths = [decode_git_path(entry[3:])]
        index += 1
        if b"R" in status or b"C" in status:
            if index >= len(records):
                fail_context("malformed git status output")
            paths.append(decode_git_path(records[index]))
            index += 1
        found.update(path for path in paths if path)
    return found


def parse_name_status_z(payload: bytes) -> set[str]:
    found: set[str] = set()
    records = nul_records(payload)
    index = 0
    while index < len(records):
        status = records[index]
        if not status:
            fail_context("malformed git diff output")
        path_count = 2 if status[:1] in (b"R", b"C") else 1
        if index + 1 + path_count > len(records):
            fail_context("malformed git diff output")
        for offset in range(1, 1 + path_count):
            path = decode_git_path(records[index + offset])
            if path:
                found.add(path)
        index += 1 + path_count
    return found


def git(root: Path, *args: str) -> str:
    completed = run_git(root, *args)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def resolve_base(root: Path, explicit: str) -> str:
    if explicit:
        if not git(root, "rev-parse", "--verify", "--quiet", f"{explicit}^{{commit}}"):
            fail_context(f"unresolved base: {explicit}")
        return explicit
    symbolic = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    candidates = [symbolic, "origin/main", "origin/master", "main", "master", "HEAD^"]
    for candidate in candidates:
        if candidate and git(root, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return ""


def committed_paths(root: Path, base: str) -> set[str]:
    completed = run_git_bytes(root, "diff", "-z", "--name-status", "--find-renames", f"{base}...HEAD")
    if completed.returncode != 0:
        fail_context(f"git diff failed for base: {base}")
    return parse_name_status_z(completed.stdout or b"")


def worktree_paths(root: Path) -> set[str]:
    completed = run_git_bytes(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if completed.returncode != 0:
        fail_context("git status failed")
    return parse_status_z(completed.stdout or b"")


def changed_files(root: Path, base: str) -> list[str]:
    found: set[str] = set()
    if base:
        found.update(committed_paths(root, base))
    found.update(worktree_paths(root))
    return sorted(found)


def matches(pattern: str, path: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.endswith("/**") and path.startswith(pattern[:-3].rstrip("/") + "/"):
        return True
    return False


def affected_domains(root: Path, files: list[str]) -> list[str]:
    manifest = root / ".engineering/tests.yaml"
    if not manifest.is_file():
        return []
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    domains: set[str] = set()
    for pattern, spec in (data.get("paths") or {}).items():
        if any(matches(str(pattern), path) for path in files):
            domains.update(str(item) for item in (spec or {}).get("domains") or [])
    return sorted(domains)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show minimum context for the current diff")
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", default="")
    parser.add_argument("--max-files", type=int, default=40)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    base = resolve_base(root, args.base)
    files = changed_files(root, base)
    domains = affected_domains(root, files)

    print(f"CONTEXT_BASE={base or '<none>'}")
    print(f"CHANGED_COUNT={len(files)}")
    for path in files[: max(args.max_files, 0)]:
        print(f"CHANGED_FILE={path}")
    if len(files) > max(args.max_files, 0):
        print(f"CHANGED_FILES_TRUNCATED={len(files) - max(args.max_files, 0)}")
    print("AFFECTED_DOMAINS=" + (",".join(domains) if domains else "<none>"))
    print("READ=AGENTS.md")
    print("READ=.engineering/project.yaml")
    if files and (root / ".engineering/tests.yaml").is_file():
        print("READ=.engineering/tests.yaml")
    print("CONTEXT_ROUTER=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
