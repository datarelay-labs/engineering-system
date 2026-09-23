#!/usr/bin/env python3
"""Emit minimal deterministic context for an existing branch or PR."""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
from pathlib import Path

import yaml


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def resolve_base(root: Path, explicit: str) -> str:
    if explicit:
        return explicit
    symbolic = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    candidates = [symbolic, "origin/main", "origin/master", "main", "master", "HEAD^"]
    for candidate in candidates:
        if candidate and git(root, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return ""


def changed_files(root: Path, base: str) -> list[str]:
    if base:
        text = git(root, "diff", "--name-only", f"{base}...HEAD")
        if text:
            return sorted({line for line in text.splitlines() if line.strip()})
    text = git(root, "status", "--porcelain")
    found = []
    for line in text.splitlines():
        value = line[3:].strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value:
            found.append(value)
    return sorted(set(found))


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
